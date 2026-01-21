"""
Management command to generate politician profile parts using batch XML processing
Supports Gemini Batch API for cost-effective processing.
"""
import time
import logging
import hashlib
import base64
import secrets
import xml.etree.ElementTree as ET
from xml.sax.saxutils import escape, unescape
import re
import tiktoken
import requests
from collections import defaultdict
from datetime import datetime, date
from django.core.management.base import BaseCommand, CommandError
from django.core.management import call_command
from django.conf import settings

from parliament_speeches.models import (
    Politician, Speech, AgendaItem, PlenarySession, PoliticianProfilePart
)
from parliament_speeches.ai_service import AIService
from .batch_api_mixin import GeminiBatchAPIMixin


logger = logging.getLogger(__name__)


class Command(GeminiBatchAPIMixin, BaseCommand):
    help = '''Generate structured politician profile parts using two-phase approach. 
    Supports multiple providers (Claude, OpenAI, Ollama).
    
    NEW APPROACH:
    - Phase 1: Generate AGENDA, PLENARY_SESSION, MONTH, YEAR profiles from speeches
    - Phase 2: Generate ALL profiles by aggregating monthly profiles using AI
    
    Features:
    - Two-phase profile generation for better accuracy
    - ALL profiles created from monthly profiles (not from raw speeches)
    - Integrity checks to remove orphaned/invalid profiles
    - Statistics showing profile completion status
    - Cleanup-only mode for maintenance
    
    Usage examples:
    - Generate profiles: --id 31
    - Run integrity check: --id 31 --integrity-check
    - Cleanup only: --id 31 --cleanup-only
    - Dry run cleanup: --id 31 --integrity-check --dry-run'''

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.ai_service = None
        self.session_key = None
        self.agenda_id_mapping = {}  # encrypted_id -> real_agenda_id
        self.plenary_id_mapping = {}  # encrypted_id -> real_plenary_id
        self.reverse_agenda_mapping = {}  # real_agenda_id -> encrypted_id
        self.reverse_plenary_mapping = {}  # real_plenary_id -> encrypted_id

    def add_arguments(self, parser):
        parser.add_argument(
            '--id',
            type=int,
            required=True,
            help='Politician ID to profile'
        )
        parser.add_argument(
            '--categories',
            nargs='+',
            choices=[choice[0] for choice in PoliticianProfilePart.PROFILE_CATEGORIES],
            help='Specific categories to profile (default: all)'
        )
        parser.add_argument(
            '--overwrite',
            action='store_true',
            help='Overwrite existing profile parts instead of skipping them'
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Run without saving profile parts to database'
        )
        parser.add_argument(
            '--ai-provider',
            type=str,
            choices=['claude', 'openai', 'ollama', 'gemini'],
            help='Provider to use (claude, openai, ollama, gemini). Default: claude.'
        )
        parser.add_argument(
            '--batch-size',
            type=int,
            default=10,
            help='Number of periods to process in parallel (default: 10)'
        )
        parser.add_argument(
            '--integrity-check',
            action='store_true',
            help='Run integrity checks and remove orphaned/invalid profiles'
        )
        parser.add_argument(
            '--cleanup-only',
            action='store_true',
            help='Only run cleanup operations, skip profile generation'
        )
        
        # Add batch API arguments from mixin
        self.add_batch_api_arguments(parser)

    def handle(self, *args, **options):
        self.dry_run = options['dry_run']
        politician_id = options['id']
        categories = options.get('categories') or [choice[0] for choice in PoliticianProfilePart.PROFILE_CATEGORIES]
        overwrite = options.get('overwrite', False)
        batch_size = options.get('batch_size', 10)
        self.batch_size = batch_size  # Store for batch API mixin
        integrity_check = options.get('integrity_check', False)
        cleanup_only = options.get('cleanup_only', False)
        
        # Initialize AI service with selected provider
        selected_provider = options.get('ai_provider') or 'gemini'
        self.ai_service = AIService(provider=selected_provider)
        self.ai_provider = selected_provider  # For batch API mixin
        
        # Initialize batch API settings
        self.initialize_batch_api(options)
        
        # Show AI provider information
        provider_info = self.ai_service.get_provider_info()
        if options.get('ai_provider'):
            self.stdout.write(self.style.SUCCESS(f"Using Provider: {provider_info['provider']} ({provider_info['model']}) [Override]"))
        else:
            self.stdout.write(self.style.SUCCESS(f"Using Provider: {provider_info['provider']} ({provider_info['model']}) [Default]"))
        
        if self.dry_run:
            self.stdout.write(self.style.WARNING("🔍 DRY RUN MODE - No profile parts will be saved"))

        try:
            politician = Politician.objects.get(id=politician_id)
        except Politician.DoesNotExist:
            raise CommandError(f'Politician with ID {politician_id} does not exist')

        self.stdout.write(
            self.style.SUCCESS(f'Profiling politician: {politician.full_name}')
        )

        # Get all speeches for analysis
        speeches = Speech.objects.filter(
            politician=politician,
            event_type='SPEECH'
        ).select_related('agenda_item__plenary_session').order_by('date')

        if not speeches.exists():
            raise CommandError(f'No speeches found for politician {politician.full_name}')

        self.stdout.write(f'Found {speeches.count()} speeches to analyze...')

        # Run integrity checks if requested
        if integrity_check or cleanup_only:
            try:
                self.run_integrity_checks(politician, speeches, categories)
            except Exception as e:
                logger.exception("Error during integrity checks")
                raise CommandError(f"Error during integrity checks: {str(e)}")

        # Skip profile generation if cleanup-only mode
        if cleanup_only:
            self.stdout.write(self.style.SUCCESS("✅ Cleanup completed"))
            return

        # NEW APPROACH: Generate non-ALL profiles first, then create ALL from monthly profiles
        try:
            self.process_politician_speeches_new_approach(politician, speeches, categories, overwrite, batch_size)
            self.stdout.write(self.style.SUCCESS("✅ Successfully completed politician profiling"))
        except Exception as e:
            logger.exception("Error during politician profiling")
            raise CommandError(f"Error during processing: {str(e)}")
        
        # Always run integrity checks at the end to ensure data consistency
        self.stdout.write("\n" + "="*80)
        self.stdout.write(self.style.WARNING("🔍 Running final integrity checks..."))
        try:
            self.run_integrity_checks(politician, speeches, categories)
            self.stdout.write(self.style.SUCCESS("✅ Final integrity checks completed"))
        except Exception as e:
            logger.exception("Error during final integrity checks")
            self.stdout.write(self.style.ERROR(f"⚠️  Warning: Final integrity check failed: {str(e)}"))
        
        # Sync profiling counts for this politician
        self.stdout.write("\n" + "="*80)
        self.stdout.write(self.style.WARNING("📊 Syncing profiling counts..."))
        try:
            call_command('sync_profiling_counts', politician_id=politician.id)
            self.stdout.write(self.style.SUCCESS("✅ Profiling counts synchronized"))
        except Exception as e:
            logger.exception("Error during profiling counts sync")
            self.stdout.write(self.style.ERROR(f"⚠️  Warning: Profiling counts sync failed: {str(e)}"))

    def run_integrity_checks(self, politician, speeches, categories):
        """Run comprehensive integrity checks and cleanup orphaned profiles"""
        self.stdout.write(f"\n🔍 INTEGRITY CHECKS: Starting cleanup for {politician.full_name}")
        
        # Show current profile statistics
        self._show_profile_statistics(politician, "BEFORE CLEANUP")
        
        # Get current valid periods from speeches
        current_periods = self._get_current_valid_periods(speeches)
        
        # Get all existing profiles for this politician
        all_profiles = PoliticianProfilePart.objects.filter(politician=politician)
        total_profiles = all_profiles.count()
        
        self.stdout.write(f"\n📊 Found {total_profiles} existing profiles to check")
        
        # Run different integrity checks
        orphaned_count = self._check_orphaned_agenda_profiles(politician, current_periods['agenda_ids'])
        orphaned_count += self._check_orphaned_session_profiles(politician, current_periods['plenary_ids'])
        orphaned_count += self._check_orphaned_month_profiles(politician, current_periods['months'])
        orphaned_count += self._check_orphaned_year_profiles(politician, current_periods['years'])
        orphaned_count += self._check_invalid_all_profiles(politician)
        orphaned_count += self._check_invalid_categories(politician, categories)
        orphaned_count += self._check_null_reference_profiles(politician)
        orphaned_count += self._check_incomplete_analysis_profiles(politician)
        
        # Final summary
        remaining_profiles = PoliticianProfilePart.objects.filter(politician=politician).count()
        self.stdout.write(f"\n📊 INTEGRITY CHECK SUMMARY:")
        self.stdout.write(f"   • Original profiles: {total_profiles}")
        self.stdout.write(f"   • Removed profiles: {orphaned_count}")
        self.stdout.write(f"   • Remaining profiles: {remaining_profiles}")
        
        if orphaned_count > 0:
            percentage_cleaned = (orphaned_count / total_profiles) * 100 if total_profiles > 0 else 0
            self.stdout.write(f"   • Cleanup percentage: {percentage_cleaned:.1f}%")
            self.stdout.write(self.style.SUCCESS(f"✅ Cleaned up {orphaned_count} orphaned/invalid profiles"))
            
            # Show updated profile statistics
            self._show_profile_statistics(politician, "AFTER CLEANUP")
        else:
            self.stdout.write(self.style.SUCCESS("✅ No orphaned profiles found - database is clean"))

    def _get_current_valid_periods(self, speeches):
        """Get all valid periods that should exist based on current speeches"""
        agenda_ids, plenary_ids, months, years = self._collect_periods_from_speeches(speeches)
        return {
            'agenda_ids': agenda_ids,
            'plenary_ids': plenary_ids,
            'months': months,
            'years': years
        }

    def _check_orphaned_agenda_profiles(self, politician, valid_agenda_ids):
        """Check for agenda profiles that reference non-existent or invalid agenda items"""
        self.stdout.write(f"\n🔍 Checking agenda profiles...")
        
        agenda_profiles = PoliticianProfilePart.objects.filter(
            politician=politician,
            period_type='AGENDA'
        )
        
        orphaned_count = 0
        for profile in agenda_profiles:
            should_remove = False
            
            # Check if agenda_item is null
            if not profile.agenda_item:
                should_remove = True
                reason = "null agenda_item reference"
            # Check if agenda_item doesn't exist in current speeches
            elif profile.agenda_item.id not in valid_agenda_ids:
                should_remove = True
                reason = f"agenda item {profile.agenda_item.id} no longer has speeches"
            
            if should_remove:
                if not self.dry_run:
                    profile.delete()
                orphaned_count += 1
                self.stdout.write(f"   🗑️  Removed AGENDA profile: {profile.category} - {reason}")
        
        if orphaned_count == 0:
            self.stdout.write(f"   ✅ All {agenda_profiles.count()} agenda profiles are valid")
        
        return orphaned_count

    def _check_orphaned_session_profiles(self, politician, valid_plenary_ids):
        """Check for session profiles that reference non-existent plenary sessions"""
        self.stdout.write(f"\n🔍 Checking plenary session profiles...")
        
        session_profiles = PoliticianProfilePart.objects.filter(
            politician=politician,
            period_type='PLENARY_SESSION'
        )
        
        orphaned_count = 0
        for profile in session_profiles:
            should_remove = False
            
            # Check if plenary_session is null
            if not profile.plenary_session:
                should_remove = True
                reason = "null plenary_session reference"
            # Check if plenary_session doesn't exist in current speeches
            elif profile.plenary_session.id not in valid_plenary_ids:
                should_remove = True
                reason = f"plenary session {profile.plenary_session.id} no longer has speeches"
            
            if should_remove:
                if not self.dry_run:
                    profile.delete()
                orphaned_count += 1
                self.stdout.write(f"   🗑️  Removed SESSION profile: {profile.category} - {reason}")
        
        if orphaned_count == 0:
            self.stdout.write(f"   ✅ All {session_profiles.count()} session profiles are valid")
        
        return orphaned_count

    def _check_orphaned_month_profiles(self, politician, valid_months):
        """Check for month profiles that don't correspond to current speech months"""
        self.stdout.write(f"\n🔍 Checking month profiles...")
        
        month_profiles = PoliticianProfilePart.objects.filter(
            politician=politician,
            period_type='MONTH'
        )
        
        orphaned_count = 0
        for profile in month_profiles:
            should_remove = False
            
            # Check if month is null
            if not profile.month:
                should_remove = True
                reason = "null month reference"
            # Check if month doesn't exist in current speeches
            elif profile.month not in valid_months:
                should_remove = True
                reason = f"month {profile.month} no longer has speeches"
            
            if should_remove:
                if not self.dry_run:
                    profile.delete()
                orphaned_count += 1
                self.stdout.write(f"   🗑️  Removed MONTH profile: {profile.category} - {reason}")
        
        if orphaned_count == 0:
            self.stdout.write(f"   ✅ All {month_profiles.count()} month profiles are valid")
        
        return orphaned_count

    def _check_orphaned_year_profiles(self, politician, valid_years):
        """Check for year profiles that don't correspond to current speech years"""
        self.stdout.write(f"\n🔍 Checking year profiles...")
        
        year_profiles = PoliticianProfilePart.objects.filter(
            politician=politician,
            period_type='YEAR'
        )
        
        orphaned_count = 0
        for profile in year_profiles:
            should_remove = False
            
            # Check if year is null
            if not profile.year:
                should_remove = True
                reason = "null year reference"
            # Check if year doesn't exist in current speeches
            elif profile.year not in valid_years:
                should_remove = True
                reason = f"year {profile.year} no longer has speeches"
            
            if should_remove:
                if not self.dry_run:
                    profile.delete()
                orphaned_count += 1
                self.stdout.write(f"   🗑️  Removed YEAR profile: {profile.category} - {reason}")
        
        if orphaned_count == 0:
            self.stdout.write(f"   ✅ All {year_profiles.count()} year profiles are valid")
        
        return orphaned_count

    def _check_invalid_all_profiles(self, politician):
        """Check for ALL profiles that have invalid references (should all be null)"""
        self.stdout.write(f"\n🔍 Checking ALL period profiles...")
        
        # Find ALL profiles that incorrectly have non-null references
        invalid_all_profiles = PoliticianProfilePart.objects.filter(
            politician=politician,
            period_type='ALL'
        ).exclude(
            agenda_item__isnull=True,
            plenary_session__isnull=True,
            month__isnull=True,
            year__isnull=True
        )
        
        orphaned_count = 0
        for profile in invalid_all_profiles:
            if not self.dry_run:
                profile.delete()
            orphaned_count += 1
            self.stdout.write(f"   🗑️  Removed invalid ALL profile: {profile.category} - had non-null period references")
        
        # Check for duplicate ALL profiles (should only be one per category)
        all_profiles = PoliticianProfilePart.objects.filter(
            politician=politician,
            period_type='ALL',
            agenda_item__isnull=True,
            plenary_session__isnull=True,
            month__isnull=True,
            year__isnull=True
        )
        
        # Group by category and remove duplicates
        from collections import defaultdict
        category_groups = defaultdict(list)
        for profile in all_profiles:
            category_groups[profile.category].append(profile)
        
        for category, profiles in category_groups.items():
            if len(profiles) > 1:
                # Keep the most recent one, delete the rest
                profiles.sort(key=lambda x: x.id, reverse=True)
                for duplicate in profiles[1:]:
                    if not self.dry_run:
                        duplicate.delete()
                    orphaned_count += 1
                    self.stdout.write(f"   🗑️  Removed duplicate ALL profile: {category}")
        
        if orphaned_count == 0:
            valid_count = PoliticianProfilePart.objects.filter(
                politician=politician,
                period_type='ALL',
                agenda_item__isnull=True,
                plenary_session__isnull=True,
                month__isnull=True,
                year__isnull=True
            ).count()
            self.stdout.write(f"   ✅ All {valid_count} ALL profiles are valid")
        
        return orphaned_count

    def _check_invalid_categories(self, politician, valid_categories):
        """Check for profiles with invalid/obsolete categories"""
        self.stdout.write(f"\n🔍 Checking profile categories...")
        
        # Get ALL valid categories from model definition (not just ones being processed)
        all_valid_categories = [choice[0] for choice in PoliticianProfilePart.PROFILE_CATEGORIES]
        
        all_profiles = PoliticianProfilePart.objects.filter(politician=politician)
        orphaned_count = 0
        
        for profile in all_profiles:
            if profile.category not in all_valid_categories:
                if not self.dry_run:
                    profile.delete()
                orphaned_count += 1
                self.stdout.write(f"   🗑️  Removed profile with invalid category: {profile.category}")
        
        if orphaned_count == 0:
            self.stdout.write(f"   ✅ All profiles have valid categories")
        
        return orphaned_count

    def _check_null_reference_profiles(self, politician):
        """Check for profiles with inconsistent null references"""
        self.stdout.write(f"\n🔍 Checking null reference consistency...")
        
        orphaned_count = 0
        
        # Check for profiles that should have references but don't
        agenda_profiles_without_ref = PoliticianProfilePart.objects.filter(
            politician=politician,
            period_type='AGENDA',
            agenda_item__isnull=True
        )
        
        for profile in agenda_profiles_without_ref:
            if not self.dry_run:
                profile.delete()
            orphaned_count += 1
            self.stdout.write(f"   🗑️  Removed AGENDA profile without agenda_item reference: {profile.category}")
        
        session_profiles_without_ref = PoliticianProfilePart.objects.filter(
            politician=politician,
            period_type='PLENARY_SESSION',
            plenary_session__isnull=True
        )
        
        for profile in session_profiles_without_ref:
            if not self.dry_run:
                profile.delete()
            orphaned_count += 1
            self.stdout.write(f"   🗑️  Removed SESSION profile without plenary_session reference: {profile.category}")
        
        month_profiles_without_ref = PoliticianProfilePart.objects.filter(
            politician=politician,
            period_type='MONTH',
            month__isnull=True
        )
        
        for profile in month_profiles_without_ref:
            if not self.dry_run:
                profile.delete()
            orphaned_count += 1
            self.stdout.write(f"   🗑️  Removed MONTH profile without month reference: {profile.category}")
        
        year_profiles_without_ref = PoliticianProfilePart.objects.filter(
            politician=politician,
            period_type='YEAR',
            year__isnull=True
        )
        
        for profile in year_profiles_without_ref:
            if not self.dry_run:
                profile.delete()
            orphaned_count += 1
            self.stdout.write(f"   🗑️  Removed YEAR profile without year reference: {profile.category}")
        
        if orphaned_count == 0:
            self.stdout.write(f"   ✅ All profiles have consistent references")
        
        return orphaned_count

    def _check_incomplete_analysis_profiles(self, politician):
        """Check for profiles with incomplete analysis (starting with <analysis> tag)"""
        self.stdout.write(f"\n🔍 Checking for incomplete/malformed analysis...")
        
        orphaned_count = 0
        
        # Find all profiles for this politician
        all_profiles = PoliticianProfilePart.objects.filter(politician=politician)
        
        for profile in all_profiles:
            # Check if analysis starts with <analysis> tag (indicates incomplete parsing)
            if profile.analysis and profile.analysis.strip().startswith('<analysis>'):
                if not self.dry_run:
                    profile.delete()
                orphaned_count += 1
                
                # Determine period description for better logging
                period_desc = self._get_profile_period_description(profile)
                self.stdout.write(f"   🗑️  Removed incomplete profile: {profile.category} - {period_desc}")
        
        if orphaned_count == 0:
            self.stdout.write(f"   ✅ All profiles have complete analysis")
        
        return orphaned_count

    def _get_profile_period_description(self, profile):
        """Get a human-readable description of a profile's period"""
        if profile.period_type == 'AGENDA' and profile.agenda_item:
            return f"AGENDA: {profile.agenda_item.title[:30]}..."
        elif profile.period_type == 'PLENARY_SESSION' and profile.plenary_session:
            return f"SESSION: {profile.plenary_session.title[:30]}..."
        elif profile.period_type == 'MONTH' and profile.month:
            return f"MONTH: {profile.month}"
        elif profile.period_type == 'YEAR' and profile.year:
            return f"YEAR: {profile.year}"
        elif profile.period_type == 'ALL':
            return "ALL periods"
        else:
            return f"{profile.period_type} (unknown)"

    def _show_profile_statistics(self, politician, title):
        """Show detailed profile statistics for this politician"""
        self.stdout.write(f"\n📊 PROFILE STATISTICS - {title}")
        self.stdout.write("─" * 60)
        
        # Get total profiles
        total_profiles = PoliticianProfilePart.objects.filter(politician=politician).count()
        
        if total_profiles == 0:
            self.stdout.write("   No profiles found")
            return
        
        # Calculate expected profiles based on current speeches
        speeches = Speech.objects.filter(politician=politician, event_type='SPEECH')
        if speeches.exists():
            agenda_ids, plenary_ids, months, years = self._collect_periods_from_speeches(speeches)
            categories = [choice[0] for choice in PoliticianProfilePart.PROFILE_CATEGORIES]
            expected_total = (len(agenda_ids) + len(plenary_ids) + len(months) + len(years) + 1) * len(categories)
            completion_percentage = (total_profiles / expected_total) * 100 if expected_total > 0 else 0
            
            self.stdout.write(f"   🎯 Total profiles: {total_profiles}")
            self.stdout.write(f"   📈 Expected profiles: {expected_total}")
            self.stdout.write(f"   📊 Completion: {completion_percentage:.1f}%")
        else:
            self.stdout.write(f"   🎯 Total profiles: {total_profiles}")
            self.stdout.write(f"   ⚠️  No speeches found for calculation")
        
        # Breakdown by period type
        period_stats = {}
        for period_type in ['AGENDA', 'PLENARY_SESSION', 'MONTH', 'YEAR', 'ALL']:
            count = PoliticianProfilePart.objects.filter(
                politician=politician,
                period_type=period_type
            ).count()
            period_stats[period_type] = count
        
        self.stdout.write(f"\n   📋 Breakdown by period:")
        for period_type, count in period_stats.items():
            self.stdout.write(f"      • {period_type}: {count}")
        
        # Breakdown by category
        category_stats = {}
        for choice in PoliticianProfilePart.PROFILE_CATEGORIES:
            category = choice[0]
            count = PoliticianProfilePart.objects.filter(
                politician=politician,
                category=category
            ).count()
            if count > 0:
                category_stats[category] = count
        
        if category_stats:
            self.stdout.write(f"\n   📋 Breakdown by category:")
            for category, count in sorted(category_stats.items()):
                self.stdout.write(f"      • {category}: {count}")
        
        self.stdout.write("─" * 60)

    def process_politician_speeches_new_approach(self, politician, speeches, categories, overwrite, batch_size):
        """NEW APPROACH: Generate all profiles except ALL first, then create ALL from monthly profiles"""
        self.stdout.write(f"\n🔄 NEW APPROACH: Two-phase profile generation")
        self.stdout.write(f"   Phase 1: Generate AGENDA, PLENARY_SESSION, MONTH, YEAR profiles")
        self.stdout.write(f"   Phase 2: Generate ALL profiles from monthly profiles using AI")
        
        # Collect all periods from speeches
        agenda_ids, plenary_ids, months, years = self._collect_periods_from_speeches(speeches)
        
        self.stdout.write(f"\n📊 Found periods:")
        self.stdout.write(f"   • Agenda items: {len(agenda_ids)}")
        self.stdout.write(f"   • Plenary sessions: {len(plenary_ids)}")
        self.stdout.write(f"   • Months: {len(months)}")
        self.stdout.write(f"   • Years: {len(years)}")
        
        # PHASE 1: Generate all profiles except ALL
        self.stdout.write(f"\n📍 PHASE 1: Generating non-ALL profiles")
        try:
            self._process_non_all_profiles(politician, speeches, categories, overwrite, batch_size, 
                                         agenda_ids, plenary_ids, months, years)
            self.stdout.write(self.style.SUCCESS("✅ Phase 1 completed: All non-ALL profiles generated"))
        except Exception as e:
            logger.exception("Error in Phase 1")
            raise CommandError(f"Error in Phase 1: {str(e)}")
        
        # VALIDATION: Ensure Phase 1 actually completed successfully
        max_retries = 3
        retry_count = 0
        
        while retry_count < max_retries:
            phase1_validation = self._validate_phase1_completion(politician, categories, agenda_ids, plenary_ids, months, years)
            
            if phase1_validation['is_complete']:
                self.stdout.write(f"✅ PHASE 1 VALIDATION PASSED: All non-ALL profiles exist")
                break
            
            retry_count += 1
            self.stdout.write(f"❌ PHASE 1 VALIDATION FAILED (Attempt {retry_count}/{max_retries})")
            self.stdout.write(f"   Missing profiles: {phase1_validation['missing_count']}")
            self.stdout.write(f"   Missing by type: {phase1_validation['missing_by_type']}")
            
            if retry_count >= max_retries:
                raise CommandError(f"Phase 1 incomplete after {max_retries} attempts - not all non-ALL profiles were generated")
            
            # Re-queue missing profiles
            self.stdout.write(f"\n🔄 Re-queueing {phase1_validation['missing_count']} missing profiles...")
            try:
                self._process_non_all_profiles(politician, speeches, categories, overwrite, batch_size, 
                                             agenda_ids, plenary_ids, months, years)
                self.stdout.write(self.style.SUCCESS(f"✅ Re-processing completed (Attempt {retry_count})"))
            except Exception as e:
                logger.exception(f"Error during re-processing (Attempt {retry_count})")
                self.stdout.write(f"❌ Error during re-processing: {str(e)}")
                if retry_count >= max_retries:
                    raise CommandError(f"Error during re-processing: {str(e)}")
        
        # PHASE 2: Generate ALL profiles from monthly profiles
        self.stdout.write(f"\n📍 PHASE 2: Generating ALL profiles from monthly profiles")
        try:
            self._process_all_profiles_from_monthly(politician, categories, overwrite)
            self.stdout.write(self.style.SUCCESS("✅ Phase 2 completed: ALL profiles generated from monthly profiles"))
        except Exception as e:
            logger.exception("Error in Phase 2")
            raise CommandError(f"Error in Phase 2: {str(e)}")
        
        # Final summary
        self._show_final_profile_summary_new(politician, speeches, categories)

    def _process_non_all_profiles(self, politician, speeches, categories, overwrite, batch_size, 
                                 agenda_ids, plenary_ids, months, years):
        """Phase 1: Generate all profiles except ALL period"""
        import concurrent.futures
        from threading import Lock
        
        # Collect all periods that need processing (excluding ALL)
        all_periods = []
        
        # Add agenda periods
        agenda_periods = self._get_missing_agenda_periods(politician, categories, agenda_ids)
        for agenda_id in agenda_periods:
            all_periods.append(('AGENDA', agenda_id))
        
        # Add session periods
        session_periods = self._get_missing_session_periods(politician, categories, plenary_ids)
        for plenary_id in session_periods:
            all_periods.append(('PLENARY_SESSION', plenary_id))
        
        # Add month periods
        month_periods = self._get_missing_month_periods(politician, categories, months)
        for month in month_periods:
            all_periods.append(('MONTH', month))
        
        # Add year periods
        year_periods = self._get_missing_year_periods(politician, categories, years)
        for year in year_periods:
            all_periods.append(('YEAR', year))
        
        total_periods = len(all_periods)
        completed_periods = 0
        progress_lock = Lock()
        
        if total_periods == 0:
            self.stdout.write("✅ No non-ALL periods need processing")
            return
        
        # Use Gemini Batch API if enabled
        if self.should_use_batch_api():
            self.stdout.write(self.style.HTTP_INFO(f"Using Google Gemini BATCH API for politician profiles"))
            self.stdout.write("=" * 80)
            self._process_periods_with_batch_api(politician, speeches, categories, all_periods, overwrite)
            return
        
        self.stdout.write(f"\n🔄 Processing {total_periods} non-ALL periods in parallel batches of {batch_size}")
        
        def process_single_period(period_info):
            """Process a single period - wrapper for parallel execution"""
            period_type, period_id = period_info
            try:
                if period_type == 'AGENDA':
                    success = self._process_single_agenda_period(politician, speeches, categories, period_id, overwrite)
                elif period_type == 'PLENARY_SESSION':
                    success = self._process_single_session_period(politician, speeches, categories, period_id, overwrite)
                elif period_type == 'MONTH':
                    success = self._process_single_month_period(politician, speeches, categories, period_id, overwrite)
                elif period_type == 'YEAR':
                    success = self._process_single_year_period(politician, speeches, categories, period_id, overwrite)
                else:
                    success = False
                
                # Thread-safe progress update
                nonlocal completed_periods
                with progress_lock:
                    completed_periods += 1
                    self.stdout.write(f"📊 Progress: {completed_periods}/{total_periods} periods completed")
                
                return success
            except Exception as e:
                logger.exception(f"Error processing {period_type} period {period_id}")
                with progress_lock:
                    completed_periods += 1
                    self.stdout.write(f"❌ Failed: {period_type} {period_id} - {str(e)}")
                return False
        
        try:
            # Process periods in parallel batches
            with concurrent.futures.ThreadPoolExecutor(max_workers=batch_size) as executor:
                future_to_period = {executor.submit(process_single_period, period): period for period in all_periods}
                
                for future in concurrent.futures.as_completed(future_to_period):
                    period = future_to_period[future]
                    try:
                        success = future.result()
                    except Exception as e:
                        logger.exception(f"Exception in parallel processing for {period}")
                        with progress_lock:
                            self.stdout.write(f"❌ Exception: {period} - {str(e)}")
                
        except KeyboardInterrupt:
            self.stdout.write(f"\n❌ Operation cancelled by user")
            self.stdout.write(f"📊 Partial progress: {completed_periods}/{total_periods} periods were completed before cancellation")
            return
        
        self.stdout.write(f"\n🎉 Phase 1 completed: {completed_periods} non-ALL periods processed successfully!")

    def _validate_phase1_completion(self, politician, categories, agenda_ids, plenary_ids, months, years):
        """Validate that Phase 1 completed successfully - all non-ALL profiles exist"""
        self.stdout.write(f"\n🔍 PHASE 1 VALIDATION: Checking non-ALL profiles completeness")
        
        missing_by_type = {
            'agendas': 0,
            'sessions': 0,
            'months': 0,
            'years': 0
        }
        total_missing = 0
        
        # Check agenda profiles
        for agenda_id in agenda_ids:
            for category in categories:
                if not PoliticianProfilePart.objects.filter(
                    politician=politician,
                    category=category,
                    period_type='AGENDA',
                    agenda_item_id=agenda_id
                ).exists():
                    missing_by_type['agendas'] += 1
                    total_missing += 1
        
        # Check session profiles
        for plenary_id in plenary_ids:
            for category in categories:
                if not PoliticianProfilePart.objects.filter(
                    politician=politician,
                    category=category,
                    period_type='PLENARY_SESSION',
                    plenary_session_id=plenary_id
                ).exists():
                    missing_by_type['sessions'] += 1
                    total_missing += 1
        
        # Check month profiles
        for month in months:
            for category in categories:
                if not PoliticianProfilePart.objects.filter(
                    politician=politician,
                    category=category,
                    period_type='MONTH',
                    month=month
                ).exists():
                    missing_by_type['months'] += 1
                    total_missing += 1
        
        # Check year profiles
        for year in years:
            for category in categories:
                if not PoliticianProfilePart.objects.filter(
                    politician=politician,
                    category=category,
                    period_type='YEAR',
                    year=year
                ).exists():
                    missing_by_type['years'] += 1
                    total_missing += 1
        
        is_complete = total_missing == 0
        
        if is_complete:
            self.stdout.write(f"✅ All non-ALL profiles exist")
        else:
            self.stdout.write(f"❌ Missing {total_missing} non-ALL profiles:")
            for period_type, count in missing_by_type.items():
                if count > 0:
                    self.stdout.write(f"   • {period_type}: {count} missing")
        
        return {
            'is_complete': is_complete,
            'missing_count': total_missing,
            'missing_by_type': missing_by_type
        }

    def _validate_monthly_profiles_completeness(self, politician, categories):
        """Validate that all required monthly profiles exist before creating ALL profiles"""
        self.stdout.write(f"\n🔍 VALIDATION: Checking monthly profiles completeness")
        
        # Get all speeches to determine expected months
        speeches = Speech.objects.filter(
            politician=politician,
            event_type='SPEECH'
        ).order_by('date')
        
        if not speeches.exists():
            return {
                'is_complete': False,
                'missing_count': 0,
                'missing_categories': categories,
                'missing_months': []
            }
        
        # Extract unique months from speeches
        months = set()
        for speech in speeches:
            months.add(f"{speech.date.month:02d}.{speech.date.year}")
        
        expected_monthly_profiles = len(months) * len(categories)
        self.stdout.write(f"📊 Expected monthly profiles: {expected_monthly_profiles} ({len(months)} months × {len(categories)} categories)")
        
        # Check which monthly profiles actually exist
        existing_monthly_profiles = PoliticianProfilePart.objects.filter(
            politician=politician,
            period_type='MONTH'
        )
        
        existing_count = existing_monthly_profiles.count()
        self.stdout.write(f"📊 Existing monthly profiles: {existing_count}")
        
        # Check for missing profiles by category and month
        missing_categories = []
        missing_months = []
        missing_count = 0
        
        for category in categories:
            category_missing_months = []
            for month in months:
                if not PoliticianProfilePart.objects.filter(
                    politician=politician,
                    category=category,
                    period_type='MONTH',
                    month=month
                ).exists():
                    category_missing_months.append(month)
                    missing_count += 1
            
            if category_missing_months:
                missing_categories.append(category)
                missing_months.extend(category_missing_months)
                self.stdout.write(f"   ❌ {category}: missing {len(category_missing_months)} months")
            else:
                self.stdout.write(f"   ✅ {category}: complete ({len(months)} months)")
        
        is_complete = missing_count == 0
        
        if is_complete:
            self.stdout.write(f"✅ All {expected_monthly_profiles} monthly profiles exist")
        else:
            self.stdout.write(f"❌ Missing {missing_count}/{expected_monthly_profiles} monthly profiles")
        
        return {
            'is_complete': is_complete,
            'missing_count': missing_count,
            'missing_categories': missing_categories,
            'missing_months': list(set(missing_months))
        }

    def _process_all_profiles_from_monthly(self, politician, categories, overwrite):
        """Phase 2: Generate ALL profiles from existing monthly profiles using AI"""
        self.stdout.write(f"\n📍 PHASE 2: Creating ALL profiles from monthly profiles")
        
        # VALIDATION: Check that all required monthly profiles exist before proceeding
        validation_result = self._validate_monthly_profiles_completeness(politician, categories)
        if not validation_result['is_complete']:
            self.stdout.write(f"❌ VALIDATION FAILED: Cannot create ALL profiles")
            self.stdout.write(f"   Missing monthly profiles: {validation_result['missing_count']}")
            self.stdout.write(f"   Missing categories: {', '.join(validation_result['missing_categories'])}")
            self.stdout.write(f"   Missing months: {', '.join(validation_result['missing_months'])}")
            raise CommandError("Phase 1 incomplete - not all monthly profiles were generated")
        
        self.stdout.write(f"✅ VALIDATION PASSED: All required monthly profiles exist")
        
        # Get all monthly profiles for this politician
        monthly_profiles = PoliticianProfilePart.objects.filter(
            politician=politician,
            period_type='MONTH'
        ).order_by('category', 'month')
        
        if not monthly_profiles.exists():
            self.stdout.write("⚠️  No monthly profiles found - cannot create ALL profiles")
            return
        
        # Group monthly profiles by category
        profiles_by_category = {}
        for profile in monthly_profiles:
            if profile.category not in profiles_by_category:
                profiles_by_category[profile.category] = []
            profiles_by_category[profile.category].append(profile)
        
        self.stdout.write(f"📊 Found monthly profiles for {len(profiles_by_category)} categories")
        
        # Use Gemini Batch API if enabled
        if self.should_use_batch_api():
            self._process_all_profiles_with_batch_api(politician, categories, profiles_by_category, overwrite)
            return
        
        # Original processing: Process each category that needs ALL profile
        for category in categories:
            if category not in profiles_by_category:
                self.stdout.write(f"⚠️  No monthly profiles found for category: {category}")
                continue
            
            # Check if ALL profile already exists
            existing_all = PoliticianProfilePart.objects.filter(
                politician=politician,
                category=category,
                period_type='ALL',
                agenda_item__isnull=True,
                plenary_session__isnull=True,
                month__isnull=True,
                year__isnull=True
            ).first()
            
            if existing_all and not overwrite:
                self.stdout.write(f"⏭️  ALL profile already exists for {category}")
                continue
            
            # Get monthly profiles for this category
            monthly_profiles_for_category = profiles_by_category[category]
            self.stdout.write(f"🔄 Creating ALL profile for {category} from {len(monthly_profiles_for_category)} monthly profiles")
            
            # Create ALL profile from monthly profiles
            success = self._create_all_profile_from_monthly(politician, category, monthly_profiles_for_category, overwrite)
            
            if success:
                self.stdout.write(f"✅ Created ALL profile for {category}")
            else:
                self.stdout.write(f"❌ Failed to create ALL profile for {category}")

    def _create_all_profile_from_monthly(self, politician, category, monthly_profiles, overwrite):
        """Create ALL profile from monthly profiles using AI"""
        try:
            # Prepare monthly profiles data for AI
            monthly_data = []
            for profile in monthly_profiles:
                monthly_data.append({
                    'month': profile.month,
                    'analysis': profile.analysis,
                    'speeches_analyzed': profile.speeches_analyzed,
                    'date_range_start': profile.date_range_start,
                    'date_range_end': profile.date_range_end,
                    'is_incomplete': profile.is_incomplete
                })
            
            # Create prompt for AI to combine monthly profiles
            prompt = self._create_monthly_aggregation_prompt(category, monthly_data)
            
            if self.dry_run:
                self.stdout.write("🔍 DRY RUN: Would send monthly aggregation request to AI")
                return True
            
            # Send to AI
            self.stdout.write(f"🔄 Sending monthly aggregation request to AI for {category}...")
            response = self.ai_service.generate_summary(prompt, max_tokens=2000, temperature=0.3)
            
            if not response:
                self.stdout.write(f"❌ No response from AI for {category}")
                return False
            
            # Parse AI response and create ALL profile
            analysis_text = self._parse_monthly_aggregation_response(response)
            if not analysis_text:
                self.stdout.write(f"❌ Failed to parse AI response for {category}")
                return False
            
            # Calculate aggregated metrics
            total_speeches = sum(p['speeches_analyzed'] for p in monthly_data)
            date_ranges = [p['date_range_start'] for p in monthly_data if p['date_range_start']]
            date_range_start = min(date_ranges) if date_ranges else None
            date_ranges_end = [p['date_range_end'] for p in monthly_data if p['date_range_end']]
            date_range_end = max(date_ranges_end) if date_ranges_end else None
            
            metrics = {
                'speeches_count': total_speeches,
                'monthly_profiles_aggregated': len(monthly_data),
                'date_range_start': date_range_start.isoformat() if date_range_start else None,
                'date_range_end': date_range_end.isoformat() if date_range_end else None,
            }
            
            # Check if any of the monthly profiles are incomplete
            has_incomplete_profiles = any(m['is_incomplete'] for m in monthly_data)
            
            # Create or update ALL profile
            from django.utils import timezone
            profile_data = {
                'politician': politician,
                'category': category,
                'period_type': 'ALL',
                'analysis': analysis_text,
                'metrics': metrics,
                'speeches_analyzed': total_speeches,
                'date_range_start': date_range_start,
                'date_range_end': date_range_end,
                'is_incomplete': has_incomplete_profiles,
                'agenda_item': None,
                'plenary_session': None,
                'month': None,
                'year': None,
                'ai_summary_generated_at': timezone.now()
            }
            
            # Check if ALL profile already exists
            existing_all = PoliticianProfilePart.objects.filter(
                politician=politician,
                category=category,
                period_type='ALL',
                agenda_item__isnull=True,
                plenary_session__isnull=True,
                month__isnull=True,
                year__isnull=True
            ).first()
            
            if existing_all:
                # Update existing - clear translations if content changed
                if existing_all.analysis != analysis_text:
                    profile_data['analysis_en'] = None
                    profile_data['analysis_ru'] = None
                
                for key, value in profile_data.items():
                    if key not in ['politician', 'category', 'period_type']:
                        setattr(existing_all, key, value)
                existing_all.save()
                self.stdout.write(f"🔄 Updated existing ALL profile for {category}")
            else:
                # Create new
                PoliticianProfilePart.objects.create(**profile_data)
                self.stdout.write(f"✅ Created new ALL profile for {category}")
            
            return True
            
        except Exception as e:
            logger.exception(f"Error creating ALL profile from monthly profiles for {category}")
            self.stdout.write(f"❌ Error: {str(e)}")
            return False

    def _create_monthly_aggregation_prompt(self, category, monthly_data):
        """Create AI prompt to aggregate monthly profiles into ALL profile"""
        # Get category definition
        category_definitions = self._get_category_definitions([category])
        
        # Build monthly data text
        monthly_text = ""
        for data in monthly_data:
            monthly_text += f"\n**{data['month']}:** {data['analysis']}\n"
        
        prompt = f"""Analyze the following monthly profile summaries for a politician and create a comprehensive ALL-period profile. Write in Estonian language, speak like native Estonian.

CATEGORY: {category}

MONTHLY PROFILES:
{monthly_text}

Your task is to create a comprehensive {category} profile that synthesizes insights from all monthly periods.

**IMPORTANT INSTRUCTIONS:**
* Write **1–4 sentences** that capture the overall patterns and trends across all months
* Identify recurring themes, evolution over time, and key characteristics
* Be **concise, evidence-based, and neutral**
* Focus on **overall patterns** rather than repeating monthly details
* If there's insufficient data across months, write "Not enough data" in Estonian

## Profile Type Definition

{category_definitions}

## General Rules
* Synthesize insights from all monthly periods
* Identify patterns, trends, and evolution over time
* Be concise and analytical
* Focus on overall characteristics rather than monthly specifics

Response format:
<analysis>
Your comprehensive analysis here
</analysis>

The analysis should be in Estonian language, analytical and specific, capturing the overall {category.lower().replace('_', ' ')} patterns across all time periods."""

        return prompt

    def _parse_monthly_aggregation_response(self, response):
        """Parse AI response to extract analysis text"""
        import re
        
        # Look for <analysis>...</analysis> tags
        analysis_match = re.search(r'<analysis>(.*?)</analysis>', response, re.DOTALL)
        if analysis_match:
            return analysis_match.group(1).strip()
        
        # If no tags, return the whole response (fallback)
        return response.strip()

    def _show_final_profile_summary_new(self, politician, speeches, categories):
        """Show final summary for the new approach"""
        self.stdout.write(f"\n📊 FINAL SUMMARY - NEW APPROACH:")
        self.stdout.write("─" * 60)
        
        # Count profiles by period type
        period_stats = {}
        for period_type in ['AGENDA', 'PLENARY_SESSION', 'MONTH', 'YEAR', 'ALL']:
            count = PoliticianProfilePart.objects.filter(
                politician=politician,
                period_type=period_type
            ).count()
            period_stats[period_type] = count
        
        self.stdout.write(f"📋 Profiles by period type:")
        for period_type, count in period_stats.items():
            self.stdout.write(f"   • {period_type}: {count}")
        
        # Count profiles by category
        category_stats = {}
        for category in categories:
            count = PoliticianProfilePart.objects.filter(
                politician=politician,
                category=category
            ).count()
            if count > 0:
                category_stats[category] = count
        
        if category_stats:
            self.stdout.write(f"\n📋 Profiles by category:")
            for category, count in sorted(category_stats.items()):
                self.stdout.write(f"   • {category}: {count}")
        
        # Show ALL profile creation method
        all_profiles = PoliticianProfilePart.objects.filter(
            politician=politician,
            period_type='ALL'
        )
        
        if all_profiles.exists():
            self.stdout.write(f"\n✅ ALL profiles created from monthly profiles: {all_profiles.count()}")
            for profile in all_profiles:
                metrics = profile.metrics or {}
                monthly_count = metrics.get('monthly_profiles_aggregated', 0)
                self.stdout.write(f"   • {profile.category}: aggregated from {monthly_count} monthly profiles")
        else:
            self.stdout.write(f"\n⚠️  No ALL profiles found")
        
        self.stdout.write("─" * 60)

    def process_politician_speeches(self, politician, speeches, categories, overwrite, batch_size):
        """Process politician speeches using separate requests per period"""
        self.stdout.write(f"\n📍 NEW APPROACH: Processing each period separately")
        
        # Collect all periods from speeches
        agenda_ids, plenary_ids, months, years = self._collect_periods_from_speeches(speeches)
        
        self.stdout.write(f"📊 Found periods:")
        self.stdout.write(f"   • Agenda items: {len(agenda_ids)}")
        self.stdout.write(f"   • Plenary sessions: {len(plenary_ids)}")
        self.stdout.write(f"   • Months: {len(months)}")
        self.stdout.write(f"   • Years: {len(years)}")
        self.stdout.write(f"   • Total periods: {len(agenda_ids) + len(plenary_ids) + len(months) + len(years) + 1}")
        
        # Calculate missing profiles for each period type
        missing_counts = self._calculate_missing_by_period(politician, categories, agenda_ids, plenary_ids, months, years)
        total_missing = sum(missing_counts.values())
        
        self.stdout.write(f"\n📊 Missing profiles by period:")
        self.stdout.write(f"   • Agenda-specific: {missing_counts['agendas']}")
        self.stdout.write(f"   • Session-specific: {missing_counts['sessions']}")
        self.stdout.write(f"   • Month-specific: {missing_counts['months']}")
        self.stdout.write(f"   • Year-specific: {missing_counts['years']}")
        self.stdout.write(f"   • General overview: {missing_counts['all']}")
        self.stdout.write(f"   🎯 TOTAL TO GENERATE: {total_missing}")
        
        if total_missing == 0:
            self.stdout.write(self.style.SUCCESS("🎉 All profiles already exist! Nothing to generate."))
            return
        
        # Ask for confirmation
        if not self._get_period_confirmation(missing_counts, categories):
            self.stdout.write("❌ Operation cancelled by user")
            return

        # Process each period type separately with parallel processing
        try:
            self._process_periods_separately(politician, speeches, categories, overwrite, 
                                           agenda_ids, plenary_ids, months, years, missing_counts, batch_size)
            self.stdout.write(self.style.SUCCESS("✅ Successfully completed politician profiling"))
        except Exception as e:
            logger.exception("Error during politician profiling")
            raise CommandError(f"Error during processing: {str(e)}")

    def _encrypt_id(self, id_value, prefix):
        """Create a reversible encrypted ID"""
        # Convert ID to bytes with prefix
        id_bytes = f"{prefix}_{id_value}".encode('utf-8')
        
        # Create a simple reversible hash using the session key
        hasher = hashlib.blake2b(id_bytes, key=self.session_key, digest_size=8)
        hash_bytes = hasher.digest()
        
        # Encode as base64 and make it URL-safe
        encrypted_id = base64.urlsafe_b64encode(hash_bytes).decode('utf-8').rstrip('=')
        
        return encrypted_id

    def _generate_xml_document(self, speeches):
        """Generate XML document with speeches and encrypted IDs"""
        xml_lines = ['<?xml version="1.0" encoding="UTF-8"?>']
        xml_lines.append('<speeches>')
        
        for speech in speeches:
            # Skip incomplete speeches (stenogram being prepared)
            if speech.is_incomplete or not speech.text or not speech.text.strip():
                continue
                
            agenda_encrypted_id = self.reverse_agenda_mapping[speech.agenda_item.id]
            plenary_encrypted_id = self.reverse_plenary_mapping[speech.agenda_item.plenary_session.id]
            date_str = speech.date.strftime('%Y-%m-%d')
            
            # Escape XML special characters
            escaped_text = escape(speech.text)
            xml_lines.append(
                f'  <speech aid="{agenda_encrypted_id}" plid="{plenary_encrypted_id}" date="{date_str}">{escaped_text}</speech>'
            )
        
        xml_lines.append('</speeches>')
        return '\n'.join(xml_lines)

    def process_profile_batch(self, xml_content, politician, speeches, categories, overwrite):
        """Process politician profiles with auto-retry until all are processed"""
        original_categories = categories.copy()
        remaining_categories = categories.copy()
        attempt = 1
        
        try:
            while remaining_categories:
                # Calculate missing profiles for this retry attempt
                if attempt > 1:
                    missing_count = self._count_missing_profiles(politician, remaining_categories, speeches)
                    self.stdout.write(f"\n🔄 RETRY {attempt}: Processing {len(remaining_categories)} categories ({missing_count} missing profiles)")
                else:
                    missing_count = self._count_missing_profiles(politician, remaining_categories, speeches)
                    self.stdout.write(f"\n📍 ATTEMPT 1: Processing {len(remaining_categories)} categories ({missing_count} missing profiles)")
                
                # Safety check: if we've been trying for too long without progress, ask user
                if attempt > 10:
                    processed_count = len(original_categories) - len(remaining_categories)
                    self.stdout.write(f"⚠️  After {attempt-1} attempts: {processed_count}/{len(original_categories)} categories completed")
                    
                    if not self.dry_run:
                        response = input("Continue trying? (Y/N): ").strip().upper()
                        if response not in ['Y', 'YES']:
                            self.stdout.write("❌ Processing stopped by user")
                            break
                    else:
                        self.stdout.write("🔍 DRY RUN: Would ask user to continue after 10 attempts")
                
                # Process this batch
                success = self._process_single_profile_batch(xml_content, politician, speeches, remaining_categories, attempt)
                
                if success:
                    self.stdout.write(f"✅ Batch completed successfully!")
                    # Check which categories still need processing
                    remaining_categories = self._get_missing_categories(politician, speeches, original_categories, overwrite)
                    if not remaining_categories:
                        self.stdout.write(f"🎉 All profile categories have been processed!")
                        break
                    else:
                        # Some categories were processed but not all, continue with remaining
                        processed_count = len(original_categories) - len(remaining_categories)
                        self.stdout.write(f"📊 Progress: {processed_count}/{len(original_categories)} categories completed")
                else:
                    # Check which categories still need processing after failure
                    remaining_categories = self._get_missing_categories(politician, speeches, original_categories, overwrite)
                    
                    if not remaining_categories:
                        self.stdout.write(f"✅ All categories have been processed despite the error!")
                        break
                    
                    processed_count = len(original_categories) - len(remaining_categories)
                    self.stdout.write(f"⚠️  Network interrupted. {processed_count}/{len(original_categories)} categories completed.")
                    self.stdout.write(f"🔄 Continuing with remaining {len(remaining_categories)} categories...")
                
                attempt += 1
                
                # Add a small delay between retries to avoid overwhelming the API
                if remaining_categories:
                    self.stdout.write(f"⏳ Waiting 2 seconds before retry...")
                    time.sleep(2)
        
        except KeyboardInterrupt:
            self.stdout.write(f"\n❌ Operation cancelled by user")
            processed_count = len(original_categories) - len(remaining_categories)
            if processed_count > 0:
                self.stdout.write(f"📊 Partial progress: {processed_count}/{len(original_categories)} categories were completed before cancellation")
            return
        
        # Final summary
        self.stdout.write(f"\n📍 FINAL PROCESSING SUMMARY")
        self._show_final_profile_summary(politician, speeches, original_categories)

    def _process_single_profile_batch(self, xml_content, politician, speeches, categories, attempt):
        """Process a single batch of profile categories"""
        try:
            missing_count = self._count_missing_profiles(politician, categories, speeches)
            self.stdout.write(f"\n📍 STEP: Sending request to AI for {len(categories)} categories ({missing_count} missing profiles)")
            
            # Send to AI
            ai_response = self._send_ai_request(xml_content, politician, speeches, categories)
            
            if not ai_response:
                self.stdout.write(self.style.ERROR("❌ Failed to get AI response"))
                return False

            return True
            
        except Exception as e:
            logger.exception(f"Error in profile batch processing attempt {attempt}")
            self.stdout.write(self.style.ERROR(f"❌ Profile batch processing failed: {str(e)}"))
            return False

    def _get_missing_categories(self, politician, speeches, original_categories, overwrite):
        """Get list of categories that still need processing"""
        if overwrite:
            # If overwriting, we need to check what was actually processed in this session
            # For now, return empty list to indicate all done (live processing handles this)
            return []
        
        missing_categories = []
        
        # Collect periods from speeches data
        agenda_ids, plenary_ids, months, years = self._collect_periods_from_speeches(speeches)
        
        for category in original_categories:
            # Check if this category has all required profiles
            category_complete = True
            
            # Check AGENDA profiles
            for agenda_id in agenda_ids:
                if not PoliticianProfilePart.objects.filter(
                politician=politician, 
                    category=category,
                    period_type='AGENDA',
                    agenda_item_id=agenda_id
            ).exists():
                    category_complete = False
                    break
            
            if not category_complete:
                missing_categories.append(category)
                continue

            # Check PLENARY_SESSION profiles
            for plenary_id in plenary_ids:
                if not PoliticianProfilePart.objects.filter(
                    politician=politician,
                    category=category,
                    period_type='PLENARY_SESSION',
                    plenary_session_id=plenary_id
                ).exists():
                    category_complete = False
                    break
            
            if not category_complete:
                missing_categories.append(category)
                continue
            
            # Check MONTH profiles
            for month in months:
                if not PoliticianProfilePart.objects.filter(
                    politician=politician,
                    category=category,
                    period_type='MONTH',
                    month=month
                ).exists():
                    category_complete = False
                    break
            
            if not category_complete:
                missing_categories.append(category)
                continue
            
            # Check YEAR profiles
            for year in years:
                if not PoliticianProfilePart.objects.filter(
                    politician=politician,
                    category=category,
                    period_type='YEAR',
                    year=year
                ).exists():
                    category_complete = False
                    break
            
            if not category_complete:
                missing_categories.append(category)
                continue
            
            # Check ALL profile
            if not PoliticianProfilePart.objects.filter(
                politician=politician,
                category=category,
                period_type='ALL',
                agenda_item__isnull=True,
                plenary_session__isnull=True,
                month__isnull=True,
                year__isnull=True
            ).exists():
                category_complete = False
            
            if not category_complete:
                missing_categories.append(category)
        
        return missing_categories

    def _collect_periods_from_speeches(self, speeches):
        """Extract unique periods from speeches data"""
        agenda_ids = set()
        plenary_ids = set()
        months = set()
        years = set()
        
        for speech in speeches:
            agenda_ids.add(speech.agenda_item.id)
            plenary_ids.add(speech.agenda_item.plenary_session.id)
            months.add(f"{speech.date.month:02d}.{speech.date.year}")
            years.add(speech.date.year)
        
        return agenda_ids, plenary_ids, months, years

    def _calculate_missing_by_period(self, politician, categories, agenda_ids, plenary_ids, months, years):
        """Calculate missing profiles for each period type"""
        missing_counts = {
            'agendas': 0,
            'sessions': 0,
            'months': 0,
            'years': 0,
            'all': 0
        }
        
        # Count missing agenda profiles
        for agenda_id in agenda_ids:
            for category in categories:
                if not PoliticianProfilePart.objects.filter(
                    politician=politician,
                    category=category,
                    period_type='AGENDA',
                    agenda_item_id=agenda_id
                ).exists():
                    missing_counts['agendas'] += 1
        
        # Count missing plenary session profiles
        for plenary_id in plenary_ids:
            for category in categories:
                if not PoliticianProfilePart.objects.filter(
                    politician=politician,
                    category=category,
                    period_type='PLENARY_SESSION',
                    plenary_session_id=plenary_id
                ).exists():
                    missing_counts['sessions'] += 1
        
        # Count missing month profiles
        for month in months:
            for category in categories:
                if not PoliticianProfilePart.objects.filter(
                    politician=politician,
                    category=category,
                    period_type='MONTH',
                    month=month
                ).exists():
                    missing_counts['months'] += 1
        
        # Count missing year profiles
        for year in years:
            for category in categories:
                if not PoliticianProfilePart.objects.filter(
                    politician=politician,
                    category=category,
                    period_type='YEAR',
                    year=year
                ).exists():
                    missing_counts['years'] += 1
        
        # Count missing ALL profiles
        for category in categories:
            if not PoliticianProfilePart.objects.filter(
                politician=politician,
                category=category,
                period_type='ALL',
                agenda_item__isnull=True,
                plenary_session__isnull=True,
                month__isnull=True,
                year__isnull=True
            ).exists():
                missing_counts['all'] += 1
        
        return missing_counts

    def _get_period_confirmation(self, missing_counts, categories):
        """Ask user for confirmation with period-based approach"""
        total_missing = sum(missing_counts.values())
        
        if self.dry_run:
            self.stdout.write(self.style.WARNING("🔍 DRY RUN MODE - No actual API calls will be made"))
            return True
        
        self.stdout.write(f"\n📋 SEPARATE PERIOD PROCESSING SUMMARY")
        self.stdout.write("─" * 50)
        self.stdout.write(f"   📂 Categories: {len(categories)}")
        self.stdout.write(f"   🎯 Total profiles to generate: {total_missing}")
        self.stdout.write(f"   🔄 Separate AI requests will be made for each missing period")
        self.stdout.write(f"   ✅ No exclusions needed - each request contains only relevant speeches")
        
        while True:
            response = input("\n❓ Do you want to proceed? (Y/N): ").strip().upper()
            if response in ['Y', 'YES']:
                return True
            elif response in ['N', 'NO']:
                return False
            else:
                self.stdout.write("Please enter Y or N")

    def _process_periods_separately(self, politician, speeches, categories, overwrite, 
                                   agenda_ids, plenary_ids, months, years, missing_counts, batch_size):
        """Process each period separately with parallel processing"""
        import concurrent.futures
        from threading import Lock
        
        # Collect all periods that need processing
        all_periods = []
        
        # Add agenda periods
        if missing_counts['agendas'] > 0:
            agenda_periods = self._get_missing_agenda_periods(politician, categories, agenda_ids)
            for agenda_id in agenda_periods:
                all_periods.append(('AGENDA', agenda_id))
        
        # Add session periods
        if missing_counts['sessions'] > 0:
            session_periods = self._get_missing_session_periods(politician, categories, plenary_ids)
            for plenary_id in session_periods:
                all_periods.append(('PLENARY_SESSION', plenary_id))
        
        # Add month periods
        if missing_counts['months'] > 0:
            month_periods = self._get_missing_month_periods(politician, categories, months)
            for month in month_periods:
                all_periods.append(('MONTH', month))
        
        # Add year periods
        if missing_counts['years'] > 0:
            year_periods = self._get_missing_year_periods(politician, categories, years)
            for year in year_periods:
                all_periods.append(('YEAR', year))
        
        # Add ALL period
        if missing_counts['all'] > 0:
            all_periods.append(('ALL', None))
        
        total_periods = len(all_periods)
        completed_periods = 0
        progress_lock = Lock()
        
        if total_periods == 0:
            self.stdout.write("✅ No periods need processing")
            return
        
        self.stdout.write(f"\n🔄 Processing {total_periods} periods in parallel batches of {batch_size}")
        
        def process_single_period(period_info):
            """Process a single period - wrapper for parallel execution"""
            period_type, period_id = period_info
            try:
                if period_type == 'AGENDA':
                    success = self._process_single_agenda_period(politician, speeches, categories, period_id, overwrite)
                elif period_type == 'PLENARY_SESSION':
                    success = self._process_single_session_period(politician, speeches, categories, period_id, overwrite)
                elif period_type == 'MONTH':
                    success = self._process_single_month_period(politician, speeches, categories, period_id, overwrite)
                elif period_type == 'YEAR':
                    success = self._process_single_year_period(politician, speeches, categories, period_id, overwrite)
                elif period_type == 'ALL':
                    success = self._process_all_period(politician, speeches, categories, overwrite)
                else:
                    success = False
                
                # Thread-safe progress update
                nonlocal completed_periods
                with progress_lock:
                    completed_periods += 1
                    self.stdout.write(f"📊 Progress: {completed_periods}/{total_periods} periods completed")
                
                return success
            except Exception as e:
                logger.exception(f"Error processing {period_type} period {period_id}")
                with progress_lock:
                    completed_periods += 1
                    self.stdout.write(f"❌ Failed: {period_type} {period_id} - {str(e)}")
                return False
        
        try:
            # Process periods in parallel batches
            with concurrent.futures.ThreadPoolExecutor(max_workers=batch_size) as executor:
                future_to_period = {executor.submit(process_single_period, period): period for period in all_periods}
                
                for future in concurrent.futures.as_completed(future_to_period):
                    period = future_to_period[future]
                    try:
                        success = future.result()
                    except Exception as e:
                        logger.exception(f"Exception in parallel processing for {period}")
                        with progress_lock:
                            self.stdout.write(f"❌ Exception: {period} - {str(e)}")
                
        except KeyboardInterrupt:
            self.stdout.write(f"\n❌ Operation cancelled by user")
            self.stdout.write(f"📊 Partial progress: {completed_periods}/{total_periods} periods were completed before cancellation")
            return
        
        self.stdout.write(f"\n🎉 All {completed_periods} periods processed successfully!")

    def _get_missing_agenda_periods(self, politician, categories, agenda_ids):
        """Get list of agenda IDs that need profiles generated"""
        missing_agendas = []
        for agenda_id in agenda_ids:
            needs_processing = False
            for category in categories:
                if not PoliticianProfilePart.objects.filter(
                    politician=politician,
                    category=category,
                    period_type='AGENDA',
                    agenda_item_id=agenda_id
                ).exists():
                    needs_processing = True
                    break
            if needs_processing:
                missing_agendas.append(agenda_id)
        return missing_agendas

    def _get_missing_session_periods(self, politician, categories, plenary_ids):
        """Get list of plenary session IDs that need profiles generated"""
        missing_sessions = []
        for plenary_id in plenary_ids:
            needs_processing = False
            for category in categories:
                if not PoliticianProfilePart.objects.filter(
                    politician=politician,
                    category=category,
                    period_type='PLENARY_SESSION',
                    plenary_session_id=plenary_id
                ).exists():
                    needs_processing = True
                    break
            if needs_processing:
                missing_sessions.append(plenary_id)
        return missing_sessions

    def _get_missing_month_periods(self, politician, categories, months):
        """Get list of months that need profiles generated"""
        missing_months = []
        for month in months:
            needs_processing = False
            for category in categories:
                if not PoliticianProfilePart.objects.filter(
                    politician=politician,
                    category=category,
                    period_type='MONTH',
                    month=month
                ).exists():
                    needs_processing = True
                    break
            if needs_processing:
                missing_months.append(month)
        return missing_months

    def _get_missing_year_periods(self, politician, categories, years):
        """Get list of years that need profiles generated"""
        missing_years = []
        for year in years:
            needs_processing = False
            for category in categories:
                if not PoliticianProfilePart.objects.filter(
                    politician=politician,
                    category=category,
                    period_type='YEAR',
                    year=year
                ).exists():
                    needs_processing = True
                    break
            if needs_processing:
                missing_years.append(year)
        return missing_years

    def _get_missing_categories_for_agenda(self, politician, categories, agenda_id, overwrite):
        """Get list of categories that need profiles generated for this agenda item"""
        if overwrite:
            return categories  # If overwriting, generate all categories
        
        missing_categories = []
        for category in categories:
            profile = PoliticianProfilePart.objects.filter(
                politician=politician,
                category=category,
                period_type='AGENDA',
                agenda_item_id=agenda_id
            ).first()
            
            if not profile:
                # Profile doesn't exist, needs generation
                missing_categories.append(category)
            elif profile.ai_summary_generated_at:
                # Profile exists, check if it needs regeneration:
                # 1) Any speeches were parsed after profile was generated
                # 2) The completion status has changed (profile.is_incomplete doesn't match current state)
                speeches_parsed_after = Speech.objects.filter(
                    politician=politician,
                    event_type='SPEECH',
                    agenda_item_id=agenda_id,
                    parsed_at__gt=profile.ai_summary_generated_at
                ).exists()
                
                has_incomplete_speeches = Speech.objects.filter(
                    politician=politician,
                    event_type='SPEECH',
                    agenda_item_id=agenda_id,
                    is_incomplete=True
                ).exists()
                
                # Check if completion status has changed
                completion_status_changed = profile.is_incomplete != has_incomplete_speeches
                
                # Regenerate if: new speeches parsed AND no incomplete speeches, OR completion status changed
                if (speeches_parsed_after and not has_incomplete_speeches) or completion_status_changed:
                    missing_categories.append(category)
        return missing_categories

    def _get_missing_categories_for_session(self, politician, categories, plenary_id, overwrite):
        """Get list of categories that need profiles generated for this plenary session"""
        if overwrite:
            return categories  # If overwriting, generate all categories
        
        missing_categories = []
        for category in categories:
            profile = PoliticianProfilePart.objects.filter(
                politician=politician,
                category=category,
                period_type='PLENARY_SESSION',
                plenary_session_id=plenary_id
            ).first()
            
            if not profile:
                # Profile doesn't exist, needs generation
                missing_categories.append(category)
            elif profile.ai_summary_generated_at:
                # Profile exists, check if it needs regeneration:
                # 1) Any speeches were parsed after profile was generated
                # 2) The completion status has changed (profile.is_incomplete doesn't match current state)
                speeches_parsed_after = Speech.objects.filter(
                    politician=politician,
                    event_type='SPEECH',
                    agenda_item__plenary_session_id=plenary_id,
                    parsed_at__gt=profile.ai_summary_generated_at
                ).exists()
                
                has_incomplete_speeches = Speech.objects.filter(
                    politician=politician,
                    event_type='SPEECH',
                    agenda_item__plenary_session_id=plenary_id,
                    is_incomplete=True
                ).exists()
                
                # Check if completion status has changed
                completion_status_changed = profile.is_incomplete != has_incomplete_speeches
                
                # Regenerate if: new speeches parsed AND no incomplete speeches, OR completion status changed
                if (speeches_parsed_after and not has_incomplete_speeches) or completion_status_changed:
                    missing_categories.append(category)
        return missing_categories

    def _get_missing_categories_for_month(self, politician, categories, month, overwrite):
        """Get list of categories that need profiles generated for this month"""
        if overwrite:
            return categories  # If overwriting, generate all categories
        
        missing_categories = []
        # Parse month (format: MM.YYYY)
        try:
            month_num, year = month.split('.')
            month_num, year = int(month_num), int(year)
        except (ValueError, AttributeError):
            return categories  # Invalid month format, regenerate all
        
        for category in categories:
            profile = PoliticianProfilePart.objects.filter(
                politician=politician,
                category=category,
                period_type='MONTH',
                month=month
            ).first()
            
            if not profile:
                # Profile doesn't exist, needs generation
                missing_categories.append(category)
            elif profile.ai_summary_generated_at:
                # Profile exists, check if it needs regeneration:
                # 1) Any speeches were parsed after profile was generated
                # 2) The completion status has changed (profile.is_incomplete doesn't match current state)
                speeches_parsed_after = Speech.objects.filter(
                    politician=politician,
                    event_type='SPEECH',
                    date__month=month_num,
                    date__year=year,
                    parsed_at__gt=profile.ai_summary_generated_at
                ).exists()
                
                has_incomplete_speeches = Speech.objects.filter(
                    politician=politician,
                    event_type='SPEECH',
                    date__month=month_num,
                    date__year=year,
                    is_incomplete=True
                ).exists()
                
                # Check if completion status has changed
                completion_status_changed = profile.is_incomplete != has_incomplete_speeches
                
                # Regenerate if: new speeches parsed AND no incomplete speeches, OR completion status changed
                if (speeches_parsed_after and not has_incomplete_speeches) or completion_status_changed:
                    missing_categories.append(category)
        return missing_categories

    def _get_missing_categories_for_year(self, politician, categories, year, overwrite):
        """Get list of categories that need profiles generated for this year"""
        if overwrite:
            return categories  # If overwriting, generate all categories
        
        missing_categories = []
        for category in categories:
            profile = PoliticianProfilePart.objects.filter(
                politician=politician,
                category=category,
                period_type='YEAR',
                year=year
            ).first()
            
            if not profile:
                # Profile doesn't exist, needs generation
                missing_categories.append(category)
            elif profile.ai_summary_generated_at:
                # Profile exists, check if it needs regeneration:
                # 1) Any speeches were parsed after profile was generated
                # 2) The completion status has changed (profile.is_incomplete doesn't match current state)
                speeches_parsed_after = Speech.objects.filter(
                    politician=politician,
                    event_type='SPEECH',
                    date__year=year,
                    parsed_at__gt=profile.ai_summary_generated_at
                ).exists()
                
                has_incomplete_speeches = Speech.objects.filter(
                    politician=politician,
                    event_type='SPEECH',
                    date__year=year,
                    is_incomplete=True
                ).exists()
                
                # Check if completion status has changed
                completion_status_changed = profile.is_incomplete != has_incomplete_speeches
                
                # Regenerate if: new speeches parsed AND no incomplete speeches, OR completion status changed
                if (speeches_parsed_after and not has_incomplete_speeches) or completion_status_changed:
                    missing_categories.append(category)
        return missing_categories

    def _get_missing_categories_for_all(self, politician, categories, overwrite):
        """Get list of categories that need profiles generated for ALL period"""
        if overwrite:
            return categories  # If overwriting, generate all categories
        
        missing_categories = []
        for category in categories:
            profile = PoliticianProfilePart.objects.filter(
                politician=politician,
                category=category,
                period_type='ALL',
                agenda_item__isnull=True,
                plenary_session__isnull=True,
                month__isnull=True,
                year__isnull=True
            ).first()
            
            if not profile:
                # Profile doesn't exist, needs generation
                missing_categories.append(category)
            elif profile.ai_summary_generated_at:
                # Profile exists, check if it needs regeneration:
                # 1) Any speeches were parsed after profile was generated
                # 2) The completion status has changed (profile.is_incomplete doesn't match current state)
                speeches_parsed_after = Speech.objects.filter(
                    politician=politician,
                    event_type='SPEECH',
                    parsed_at__gt=profile.ai_summary_generated_at
                ).exists()
                
                has_incomplete_speeches = Speech.objects.filter(
                    politician=politician,
                    event_type='SPEECH',
                    is_incomplete=True
                ).exists()
                
                # Check if completion status has changed
                completion_status_changed = profile.is_incomplete != has_incomplete_speeches
                
                # Regenerate if: new speeches parsed AND no incomplete speeches, OR completion status changed
                if (speeches_parsed_after and not has_incomplete_speeches) or completion_status_changed:
                    missing_categories.append(category)
        return missing_categories

    def _process_single_agenda_period(self, politician, speeches, categories, agenda_id, overwrite):
        """Process a single agenda item period"""
        try:
            agenda_item = AgendaItem.objects.get(id=agenda_id)
            self.stdout.write(f"\n📍 Processing AGENDA: {agenda_item.title[:50]}...")
            
            # Check which categories need processing for this agenda
            missing_categories = self._get_missing_categories_for_agenda(politician, categories, agenda_id, overwrite)
            
            if not missing_categories:
                self.stdout.write(f"✅ All profiles exist for agenda: {agenda_item.title[:50]}...")
                return True
            
            self.stdout.write(f"🎯 Need to generate: {', '.join(missing_categories)} ({len(missing_categories)}/{len(categories)})")
            
            # Filter speeches for this agenda item
            period_speeches = [s for s in speeches if s.agenda_item_id == agenda_id]
            
            if not period_speeches:
                self.stdout.write(f"⚠️  No speeches found for agenda {agenda_id}")
                return
            
            # Generate XML for this period only
            xml_content = self._generate_period_xml(period_speeches, 'AGENDA')
            
            # Create prompt for this specific period with only missing categories
            prompt = self._create_period_prompt(missing_categories, xml_content, 'AGENDA', agenda_item.title)
            
            # Send to AI and process response
            response = self._send_period_ai_request(prompt, politician, period_speeches, missing_categories, 'AGENDA', agenda_item=agenda_item)
            
            if response:
                self.stdout.write(f"✅ Completed agenda: {agenda_item.title[:50]}...")
                return True
            else:
                self.stdout.write(f"❌ Failed agenda: {agenda_item.title[:50]}...")
                return False
                
        except AgendaItem.DoesNotExist:
            self.stdout.write(f"❌ Agenda item {agenda_id} not found")
            return False
        except Exception as e:
            self.stdout.write(f"❌ Error processing agenda {agenda_id}: {str(e)}")
            return False

    def _process_single_session_period(self, politician, speeches, categories, plenary_id, overwrite):
        """Process a single plenary session period"""
        try:
            plenary_session = PlenarySession.objects.get(id=plenary_id)
            self.stdout.write(f"\n📍 Processing SESSION: {plenary_session.title[:50]}...")
            
            # Check which categories need processing for this session
            missing_categories = self._get_missing_categories_for_session(politician, categories, plenary_id, overwrite)
            
            if not missing_categories:
                self.stdout.write(f"✅ All profiles exist for session: {plenary_session.title[:50]}...")
                return
            
            self.stdout.write(f"🎯 Need to generate: {', '.join(missing_categories)} ({len(missing_categories)}/{len(categories)})")
            
            # Filter speeches for this plenary session
            period_speeches = [s for s in speeches if s.agenda_item.plenary_session_id == plenary_id]
            
            if not period_speeches:
                self.stdout.write(f"⚠️  No speeches found for session {plenary_id}")
                return
            
            # Generate XML for this period only
            xml_content = self._generate_period_xml(period_speeches, 'PLENARY_SESSION')
            
            # Create prompt for this specific period with only missing categories
            prompt = self._create_period_prompt(missing_categories, xml_content, 'PLENARY_SESSION', plenary_session.title)
            
            # Send to AI and process response
            response = self._send_period_ai_request(prompt, politician, period_speeches, missing_categories, 'PLENARY_SESSION', plenary_session=plenary_session)
            
            if response:
                self.stdout.write(f"✅ Completed session: {plenary_session.title[:50]}...")
            else:
                self.stdout.write(f"❌ Failed session: {plenary_session.title[:50]}...")
                
        except PlenarySession.DoesNotExist:
            self.stdout.write(f"❌ Plenary session {plenary_id} not found")
        except Exception as e:
            self.stdout.write(f"❌ Error processing session {plenary_id}: {str(e)}")

    def _process_single_month_period(self, politician, speeches, categories, month, overwrite):
        """Process a single month period"""
        try:
            self.stdout.write(f"\n📍 Processing MONTH: {month}")
            
            # Check which categories need processing for this month
            missing_categories = self._get_missing_categories_for_month(politician, categories, month, overwrite)
            
            if not missing_categories:
                self.stdout.write(f"✅ All profiles exist for month: {month}")
                return
            
            self.stdout.write(f"🎯 Need to generate: {', '.join(missing_categories)} ({len(missing_categories)}/{len(categories)})")
            
            # Parse month (format: MM.YYYY)
            month_num, year = month.split('.')
            month_num, year = int(month_num), int(year)
            
            # Filter speeches for this month
            period_speeches = [s for s in speeches if s.date.month == month_num and s.date.year == year]
            
            if not period_speeches:
                self.stdout.write(f"⚠️  No speeches found for month {month}")
                return
            
            # Generate XML for this period only
            xml_content = self._generate_period_xml(period_speeches, 'MONTH')
            
            # Create prompt for this specific period with only missing categories
            prompt = self._create_period_prompt(missing_categories, xml_content, 'MONTH', f"Month {month}")
            
            # Send to AI and process response
            response = self._send_period_ai_request(prompt, politician, period_speeches, missing_categories, 'MONTH', month=month)
            
            if response:
                self.stdout.write(f"✅ Completed month: {month}")
            else:
                self.stdout.write(f"❌ Failed month: {month}")
                
        except Exception as e:
            self.stdout.write(f"❌ Error processing month {month}: {str(e)}")

    def _process_single_year_period(self, politician, speeches, categories, year, overwrite):
        """Process a single year period"""
        try:
            self.stdout.write(f"\n📍 Processing YEAR: {year}")
            
            # Check which categories need processing for this year
            missing_categories = self._get_missing_categories_for_year(politician, categories, year, overwrite)
            
            if not missing_categories:
                self.stdout.write(f"✅ All profiles exist for year: {year}")
                return
            
            self.stdout.write(f"🎯 Need to generate: {', '.join(missing_categories)} ({len(missing_categories)}/{len(categories)})")
            
            # Filter speeches for this year
            period_speeches = [s for s in speeches if s.date.year == year]
            
            if not period_speeches:
                self.stdout.write(f"⚠️  No speeches found for year {year}")
                return
            
            # Generate XML for this period only
            xml_content = self._generate_period_xml(period_speeches, 'YEAR')
            
            # Create prompt for this specific period with only missing categories
            prompt = self._create_period_prompt(missing_categories, xml_content, 'YEAR', f"Year {year}")
            
            # Send to AI and process response
            response = self._send_period_ai_request(prompt, politician, period_speeches, missing_categories, 'YEAR', year=year)
            
            if response:
                self.stdout.write(f"✅ Completed year: {year}")
            else:
                self.stdout.write(f"❌ Failed year: {year}")
                
        except Exception as e:
            self.stdout.write(f"❌ Error processing year {year}: {str(e)}")

    def _process_all_period(self, politician, speeches, categories, overwrite):
        """Process the ALL period (general overview)"""
        try:
            self.stdout.write(f"\n📍 Processing ALL: General overview")
            
            # Check which categories need processing for ALL period
            missing_categories = self._get_missing_categories_for_all(politician, categories, overwrite)
            
            if not missing_categories:
                self.stdout.write(f"✅ All profiles exist for ALL period")
                return
            
            self.stdout.write(f"🎯 Need to generate: {', '.join(missing_categories)} ({len(missing_categories)}/{len(categories)})")
            
            # Use all speeches
            period_speeches = list(speeches)
            
            if not period_speeches:
                self.stdout.write(f"⚠️  No speeches found for ALL period")
                return
            
            # Generate XML for all speeches
            xml_content = self._generate_period_xml(period_speeches, 'ALL')
            
            # Create prompt for general overview with only missing categories
            prompt = self._create_period_prompt(missing_categories, xml_content, 'ALL', "General overview across all periods")
            
            # Send to AI and process response
            response = self._send_period_ai_request(prompt, politician, period_speeches, missing_categories, 'ALL')
            
            if response:
                self.stdout.write(f"✅ Completed ALL: General overview")
            else:
                self.stdout.write(f"❌ Failed ALL: General overview")
                
        except Exception as e:
            self.stdout.write(f"❌ Error processing ALL period: {str(e)}")

    def _generate_period_xml(self, period_speeches, period_type):
        """Generate XML for a specific period's speeches"""
        xml_lines = ['<?xml version="1.0" encoding="UTF-8"?>']
        xml_lines.append('<speeches>')
        
        for speech in period_speeches:
            # Skip incomplete speeches (stenogram being prepared)
            if speech.is_incomplete or not speech.text or not speech.text.strip():
                continue
                
            date_str = speech.date.strftime('%Y-%m-%d')
            # For period-specific processing, we don't need encrypted IDs
            xml_lines.append(f'  <speech date="{date_str}">{escape(speech.text)}</speech>')
        
        xml_lines.append('</speeches>')
        return '\n'.join(xml_lines)

    def _create_period_prompt(self, categories, xml_content, period_type, period_description):
        """Create AI prompt for a specific period with only requested categories"""
        categories_str = ", ".join(categories)
        
        # Build category definitions for only the requested categories
        category_definitions = self._get_category_definitions(categories)
        
        # Build response format for only the requested categories
        response_examples = []
        for category in categories:
            response_examples.append(f'<profile type="{category}">Analysis for {category.lower().replace("_", " ")}</profile>')
        response_format = '\n'.join(response_examples)
        
        prompt = f"""Analyze the following speeches to create politician profile parts for a specific time period. Write in Estonian language, speak like native Estonian.

PERIOD: {period_type} - {period_description}

{xml_content}

You are analyzing speeches from a specific time period.
Your task is to produce structured **summaries** for ONLY the following {len(categories)} categories: {categories_str}

**IMPORTANT: Generate profiles ONLY for the {len(categories)} categories listed above. Do not generate any other profile types.**

For each profile type:
* Write **1–4 sentences**, if there is not enough information, write "Not enough data" in Estonian, don't guess or overthink.
* Summaries must be **concise, evidence-based, and neutral**.
* Every claim must be **grounded in the speeches** (no speculation).
* When mentioning an issue, include **who/what was emphasized, the stance taken, and intensity of support or opposition**.

## Profile Type Definitions

{category_definitions}

## General Rules
* Be concise and neutral.
* Do not speculate beyond speech evidence.
* Focus on **issues, stances, tone, and patterns** that are explicitly present in the speeches.
* Generate profiles for EXACTLY {len(categories)} categories: {categories_str}

Response format:
<profiles>
{response_format}
</profiles>

Each profile description should be in Estonian language, like you are a native Estonian speaker, analytical and specific."""

        return prompt

    def _get_category_definitions(self, categories):
        """Get profile type definitions for only the requested categories"""
        all_definitions = {
            'POLITICAL_POSITION': """### POLITICAL_POSITION
* Identify the most salient issues.
* State direction and strength of stance (support/oppose, strong/moderate).
* Mention shifts compared to earlier periods.
* Note if framing is policy-driven, value-driven, or performance-driven.""",
            
            'TOPIC_EXPERTISE': """### TOPIC_EXPERTISE
* Highlight topics where the speaker shows knowledge and authority.
* Mention use of data, technical terms, or statistics.
* Call out consistent explanations or reliance on expertise.""",
            
            'RHETORICAL_STYLE': """### RHETORICAL_STYLE
* Describe overall tone (conciliatory, combative, optimistic, urgent).
* Point out the balance between emotional and logical appeals.
* Mention formality, complexity, and use of storytelling vs data.""",
            
            'ACTIVITY_PATTERNS': """### ACTIVITY_PATTERNS
* Summarize frequency and rhythm of speeches or public appearances.
* Include references to events, meetings, or travel mentioned.
* Highlight recurring communication patterns (e.g., weekly updates).""",
            
            'OPPOSITION_STANCE': """### OPPOSITION_STANCE
* Identify main opponents or groups criticized.
* Clarify if critiques are policy-based, procedural, or personal.
* Note the intensity of attacks and whether compromise was ruled out.""",
            
            'COLLABORATION_STYLE': """### COLLABORATION_STYLE
* Mention cooperation with colleagues, co-sponsorships, or coalitions.
* Describe openness to compromise or mediation.
* Highlight references to bipartisan or cross-party collaboration.""",
            
            'REGIONAL_FOCUS': """### REGIONAL_FOCUS
* Point out attention to local/district vs national vs international issues.
* Mention specific regional industries, projects, or communities.""",
            
            'ECONOMIC_VIEWS': """### ECONOMIC_VIEWS
* Summarize positions on taxes, spending, regulation, trade, and labor.
* Note attitudes toward redistribution, growth, or fiscal discipline.
* Mention affinity toward business interests vs labor concerns.""",
            
            'SOCIAL_ISSUES': """### SOCIAL_ISSUES
* State positions on abortion, LGBTQ+, immigration, guns, education, policing.
* Clarify balance between civil liberties and security.
* Mention religious or moral framing when used.""",
            
            'LEGISLATIVE_FOCUS': """### LEGISLATIVE_FOCUS
* Identify legislative priorities (topics of bills, amendments, hearings).
* Describe whether the speaker is an initiator, supporter, or opponent.
* Note claimed progress or achievements."""
        }
        
        # Return only definitions for requested categories
        requested_definitions = []
        for category in categories:
            if category in all_definitions:
                requested_definitions.append(all_definitions[category])
        
        return '\n\n'.join(requested_definitions)

    def _send_period_ai_request(self, prompt, politician, period_speeches, categories, period_type, 
                               agenda_item=None, plenary_session=None, month=None, year=None):
        """Send AI request for a specific period with streaming and live profile creation"""
        if self.dry_run:
            self.stdout.write("🔍 DRY RUN: Skipping actual AI request")
            return True

        try:
            self.stdout.write(f"🔄 Streaming response from AI for {period_type}...")
            
            # Use streaming API for real-time feedback with live parsing
            profile_parts = []
            buffer = ""  # Buffer to accumulate partial XML
            processed_profiles = {}  # Track processed profiles to avoid duplicates
            created_count = 0
            
            for chunk in self.ai_service.generate_summary_stream(prompt, max_tokens=65535, temperature=0.3):
                profile_parts.append(chunk)
                buffer += chunk
                
                # Print chunks in real-time
                self.stdout.write(chunk, ending='')
                self.stdout.flush()
                
                # Check for complete profile elements in buffer
                new_profiles = self._process_period_complete_profiles(
                    buffer, politician, processed_profiles, period_speeches, categories, 
                    period_type, agenda_item, plenary_session, month, year
                )
                created_count += new_profiles
            
            response = ''.join(profile_parts).strip()
            self.stdout.write(f"\n✅ Streaming complete! Created {created_count} profiles for {period_type}")
            
            # Final processing for any remaining profiles
            final_profiles = self._process_period_complete_profiles(
                response, politician, processed_profiles, period_speeches, categories, 
                period_type, agenda_item, plenary_session, month, year, final=True
            )
            created_count += final_profiles
            
            if created_count > 0:
                self.stdout.write(f"📊 Total created: {created_count} profiles")
                return True
            else:
                self.stdout.write("❌ No profiles were created")
                return False
            
        except Exception as e:
            logger.exception(f"Error in period AI request for {period_type}")
            self.stdout.write(f"❌ AI request failed: {str(e)}")
            return False

    def _process_period_complete_profiles(self, buffer, politician, processed_profiles, period_speeches, categories, 
                                         period_type, agenda_item=None, plenary_session=None, month=None, year=None, final=False):
        """Process complete profile elements from streaming buffer for period processing"""
        import re
        
        # Find all complete <profile type="..." >...</profile> elements
        pattern = r'<profile\s+type="([^"]+)">([^<]*(?:<(?!/profile>)[^<]*)*)</profile>'
        matches = re.findall(pattern, buffer, re.DOTALL)
        
        new_profiles_count = 0
        
        for category, profile_text in matches:
            # Create unique identifier for this profile
            profile_key = f"{category}-{period_type}"
            
            # Skip if already processed
            if profile_key in processed_profiles:
                continue
                
            # Mark as processed
            processed_profiles[profile_key] = True
            
            # Process this profile immediately
            success = self._process_single_period_profile_live(
                category, profile_text, politician, period_speeches, period_type,
                agenda_item, plenary_session, month, year
            )
            
            if success:
                new_profiles_count += 1
                self.stdout.write(f"\n🟢 Live created: {category} for {period_type}")
            else:
                self.stdout.write(f"\n🔴 Failed to create: {category} for {period_type}")
        
        return new_profiles_count

    def _process_single_period_profile_live(self, category, profile_text, politician, period_speeches, period_type,
                                           agenda_item=None, plenary_session=None, month=None, year=None):
        """Process a single profile element during live streaming for period processing"""
        try:
            if not category or not profile_text:
                return False
            
            # Unescape XML entities
            analysis_text = unescape(profile_text.strip())
            
            # Calculate metrics (convert dates to strings for JSON serialization)
            date_range_start = min(s.date.date() for s in period_speeches) if period_speeches else None
            date_range_end = max(s.date.date() for s in period_speeches) if period_speeches else None
            
            metrics = {
                'speeches_count': len(period_speeches),
                'date_range_start': date_range_start.isoformat() if date_range_start else None,
                'date_range_end': date_range_end.isoformat() if date_range_end else None,
            }
            
            # Check if any speeches are incomplete
            has_incomplete_speeches = any(s.is_incomplete for s in period_speeches)
            
            # Prepare profile data
            from django.utils import timezone
            profile_data = {
                'politician': politician,
                'category': category,
                'period_type': period_type,
                'analysis': analysis_text,
                'metrics': metrics,
                'speeches_analyzed': len(period_speeches),
                'date_range_start': date_range_start,
                'date_range_end': date_range_end,
                'is_incomplete': has_incomplete_speeches,
                'ai_summary_generated_at': timezone.now()
            }
            
            # Add period-specific fields
            if agenda_item:
                profile_data['agenda_item'] = agenda_item
            elif plenary_session:
                profile_data['plenary_session'] = plenary_session
            elif month:
                profile_data['month'] = month
            elif year:
                profile_data['year'] = year
            
            # Check if profile already exists
            existing_query = {
                'politician': politician,
                'category': category,
                'period_type': period_type,
            }
            
            # Add period-specific filters for the check
            if agenda_item:
                existing_query['agenda_item'] = agenda_item
            elif plenary_session:
                existing_query['plenary_session'] = plenary_session
            elif month:
                existing_query['month'] = month
            elif year:
                existing_query['year'] = year
            else:  # ALL period
                existing_query.update({
                    'agenda_item__isnull': True,
                    'plenary_session__isnull': True,
                    'month__isnull': True,
                    'year__isnull': True
                })
            
            existing_profile = PoliticianProfilePart.objects.filter(**existing_query).first()
            
            # Save or update profile
            if not self.dry_run:
                if existing_profile:
                    # Update existing profile - clear translations if content changed
                    if existing_profile.analysis != analysis_text:
                        profile_data['analysis_en'] = None
                        profile_data['analysis_ru'] = None
                    
                    for key, value in profile_data.items():
                        if key not in ['politician', 'category', 'period_type']:  # Don't update key fields
                            setattr(existing_profile, key, value)
                    existing_profile.save()
                    self.stdout.write(f" (updated existing)")
                else:
                    # Create new profile
                    PoliticianProfilePart.objects.create(**profile_data)
                    self.stdout.write(f" (created new)")
            
            return True
            
        except Exception as e:
            logger.exception(f"Error processing single period profile live")
            return False

    def _parse_and_save_period_profiles(self, ai_response, politician, period_speeches, categories, 
                                       period_type, agenda_item=None, plenary_session=None, month=None, year=None):
        """Parse AI response and save profiles for a specific period"""
        import re
        
        try:
            # Extract profiles section
            profiles_match = re.search(r'<profiles>(.*?)</profiles>', ai_response, re.DOTALL)
            if not profiles_match:
                self.stdout.write("❌ No <profiles> section found in AI response")
                return
            
            profiles_xml = f"<profiles>{profiles_match.group(1)}</profiles>"
            
            # Parse XML
            try:
                root = ET.fromstring(profiles_xml)
            except ET.ParseError as e:
                self.stdout.write(f"❌ Failed to parse AI response XML: {e}")
                return
            
            # Process each profile
            created_count = 0
            for profile_elem in root.findall('profile'):
                try:
                    category = profile_elem.get('type')
                    analysis_text = profile_elem.text
                    
                    if not category or not analysis_text:
                        continue
                    
                    # Unescape XML entities
                    analysis_text = unescape(analysis_text.strip())
                    
                    # Calculate metrics (convert dates to strings for JSON serialization)
                    date_range_start = min(s.date.date() for s in period_speeches) if period_speeches else None
                    date_range_end = max(s.date.date() for s in period_speeches) if period_speeches else None
                    
                    metrics = {
                        'speeches_count': len(period_speeches),
                        'date_range_start': date_range_start.isoformat() if date_range_start else None,
                        'date_range_end': date_range_end.isoformat() if date_range_end else None,
                    }
                    
                    # Check if any speeches are incomplete
                    has_incomplete_speeches = any(s.is_incomplete for s in period_speeches)
                    
                    # Prepare profile data
                    from django.utils import timezone
                    profile_data = {
                        'politician': politician,
                        'category': category,
                        'period_type': period_type,
                        'analysis': analysis_text,
                        'metrics': metrics,
                        'speeches_analyzed': len(period_speeches),
                        'date_range_start': date_range_start,
                        'date_range_end': date_range_end,
                        'is_incomplete': has_incomplete_speeches,
                        'ai_summary_generated_at': timezone.now()
                    }
                    
                    # Add period-specific fields
                    if agenda_item:
                        profile_data['agenda_item'] = agenda_item
                    elif plenary_session:
                        profile_data['plenary_session'] = plenary_session
                    elif month:
                        profile_data['month'] = month
                    elif year:
                        profile_data['year'] = year
                    
                    # Save profile
                    if not self.dry_run:
                        PoliticianProfilePart.objects.create(**profile_data)
                        created_count += 1
                        self.stdout.write(f"✅ Created: {category}")
                    else:
                        self.stdout.write(f"🔍 DRY RUN - Would create: {category}")
                        created_count += 1
                        
                except Exception as e:
                    self.stdout.write(f"🔴 Error processing profile: {str(e)}")
            
            self.stdout.write(f"📊 Created {created_count} profiles for {period_type}")
            
        except Exception as e:
            logger.exception("Error parsing period AI response")
            self.stdout.write(f"❌ Error parsing AI response: {str(e)}")

    def _count_missing_profiles(self, politician, categories, speeches):
        """Count how many profiles are missing for the given categories"""
        agenda_ids, plenary_ids, months, years = self._collect_periods_from_speeches(speeches)
        
        missing_count = 0
        
        # Count missing agenda profiles
        for agenda_id in agenda_ids:
            for category in categories:
                if not PoliticianProfilePart.objects.filter(
                    politician=politician,
                    category=category,
                    period_type='AGENDA',
                    agenda_item_id=agenda_id
                ).exists():
                    missing_count += 1
        
        # Count missing plenary session profiles
        for plenary_id in plenary_ids:
            for category in categories:
                if not PoliticianProfilePart.objects.filter(
                    politician=politician,
                    category=category,
                    period_type='PLENARY_SESSION',
                    plenary_session_id=plenary_id
                ).exists():
                    missing_count += 1
        
        # Count missing month profiles
        for month in months:
            for category in categories:
                if not PoliticianProfilePart.objects.filter(
                    politician=politician,
                    category=category,
                    period_type='MONTH',
                    month=month
                ).exists():
                    missing_count += 1
        
        # Count missing year profiles
        for year in years:
            for category in categories:
                if not PoliticianProfilePart.objects.filter(
                    politician=politician,
                    category=category,
                    period_type='YEAR',
                    year=year
                ).exists():
                    missing_count += 1
        
        # Count missing ALL profiles
        for category in categories:
            if not PoliticianProfilePart.objects.filter(
                politician=politician,
                category=category,
                period_type='ALL',
                agenda_item__isnull=True,
                plenary_session__isnull=True,
                month__isnull=True,
                year__isnull=True
            ).exists():
                missing_count += 1
        
        return missing_count

    def _get_already_processed_periods(self, politician, categories, speeches):
        """Generate exclusion info for specific [profile type, period] combinations that are already processed"""
        agenda_ids, plenary_ids, months, years = self._collect_periods_from_speeches(speeches)
        
        excluded_info = []
        exclusions_by_category = {}
        
        # Check each category-period combination individually
        for category in categories:
            category_exclusions = {
                'agendas': [],
                'plenary_sessions': [],
                'months': [],
                'years': [],
                'all': False
            }
            
            # Check agenda items for this specific category
            for agenda_id in agenda_ids:
                if PoliticianProfilePart.objects.filter(
                    politician=politician,
                    category=category,
                    period_type='AGENDA',
                    agenda_item_id=agenda_id
                ).exists():
                    encrypted_aid = self.reverse_agenda_mapping[agenda_id]
                    category_exclusions['agendas'].append(encrypted_aid)
            
            # Check plenary sessions for this specific category
            for plenary_id in plenary_ids:
                if PoliticianProfilePart.objects.filter(
                    politician=politician,
                    category=category,
                    period_type='PLENARY_SESSION',
                    plenary_session_id=plenary_id
                ).exists():
                    encrypted_plid = self.reverse_plenary_mapping[plenary_id]
                    category_exclusions['plenary_sessions'].append(encrypted_plid)
            
            # Check months for this specific category
            for month in months:
                if PoliticianProfilePart.objects.filter(
                    politician=politician,
                    category=category,
                    period_type='MONTH',
                    month=month
                ).exists():
                    category_exclusions['months'].append(month)
            
            # Check years for this specific category
            for year in years:
                if PoliticianProfilePart.objects.filter(
                    politician=politician,
                    category=category,
                    period_type='YEAR',
                    year=year
                ).exists():
                    category_exclusions['years'].append(str(year))
            
            # Check ALL period for this specific category
            if PoliticianProfilePart.objects.filter(
                politician=politician,
                category=category,
                period_type='ALL',
                agenda_item__isnull=True,
                plenary_session__isnull=True,
                month__isnull=True,
                year__isnull=True
            ).exists():
                category_exclusions['all'] = True
            
            exclusions_by_category[category] = category_exclusions
        
        # Build exclusion instructions per category with strong emphasis
        has_exclusions = False
        total_exclusions = 0
        for category, exclusions in exclusions_by_category.items():
            category_has_exclusions = any([
                exclusions['agendas'],
                exclusions['plenary_sessions'], 
                exclusions['months'],
                exclusions['years'],
                exclusions['all']
            ])
            
            if category_has_exclusions:
                if not has_exclusions:
                    excluded_info.append("**CRITICAL: ABSOLUTELY DO NOT generate profiles for the following [profile type, period] combinations:**")
                    excluded_info.append("**THESE PROFILES ALREADY EXIST AND ARE FORBIDDEN TO GENERATE**")
                    excluded_info.append("**VIOLATION OF THESE RULES WILL RESULT IN FAILURE**")
                    has_exclusions = True
                
                excluded_info.append(f"\n• **FORBIDDEN FOR {category}:**")
                
                if exclusions['agendas']:
                    total_exclusions += len(exclusions['agendas'])
                    excluded_info.append(f"  - **FORBIDDEN agenda items with aid: {', '.join(exclusions['agendas'])} (ALREADY EXIST)**")
                
                if exclusions['plenary_sessions']:
                    total_exclusions += len(exclusions['plenary_sessions'])
                    excluded_info.append(f"  - **FORBIDDEN plenary sessions with plid: {', '.join(exclusions['plenary_sessions'])} (ALREADY EXIST)**")
                
                if exclusions['months']:
                    total_exclusions += len(exclusions['months'])
                    excluded_info.append(f"  - **FORBIDDEN months: {', '.join(exclusions['months'])} (ALREADY EXIST)**")
                
                if exclusions['years']:
                    total_exclusions += len(exclusions['years'])
                    excluded_info.append(f"  - **FORBIDDEN years: {', '.join(exclusions['years'])} (ALREADY EXIST)**")
                
                if exclusions['all']:
                    total_exclusions += 1
                    excluded_info.append(f"  - **FORBIDDEN general overview (ALL) (ALREADY EXISTS)**")
        
        if has_exclusions:
            excluded_info.insert(1, f"**TOTAL EXCLUSIONS: {total_exclusions} profiles are FORBIDDEN to generate**")
            excluded_info.append("**REMEMBER: DO NOT GENERATE ANY OF THE ABOVE FORBIDDEN PROFILES**")
            excluded_info.append("")  # Empty line
        
        return "\n".join(excluded_info) if excluded_info else ""

    def _count_tokens(self, text):
        """Count tokens using tiktoken"""
        try:
            # Try to get encoding for the current AI provider
            provider_info = self.ai_service.get_provider_info()
            
            if provider_info['provider'] == 'openai':
                # Use the model-specific encoding
                model = provider_info['model']
                if 'gpt-4' in model:
                    encoding = tiktoken.encoding_for_model("gpt-4")
                elif 'gpt-3.5' in model:
                    encoding = tiktoken.encoding_for_model("gpt-3.5-turbo")
                else:
                    # Fallback to cl100k_base for newer models
                    encoding = tiktoken.get_encoding("cl100k_base")
            else:
                # For Claude and other providers, use cl100k_base as approximation
                encoding = tiktoken.get_encoding("cl100k_base")
            
            tokens = encoding.encode(text)
            return len(tokens)
        except Exception as e:
            logger.warning(f"Failed to count tokens with tiktoken: {e}")
            # Fallback to word count approximation
            word_count = len(text.split())
            return int(word_count * 1.3)  # Rough approximation

    def _get_user_confirmation(self, token_count, speeches, categories):
        """Ask user for confirmation before sending to AI with profile summary"""
        # Generate profile summary
        agenda_ids, plenary_ids, months, years = self._collect_periods_from_speeches(speeches)
        
        # Calculate what actually needs to be generated (excluding existing)
        politician = speeches[0].politician if speeches else None
        if not politician:
            return False
        
        missing_counts = {
            'agendas': 0,
            'sessions': 0, 
            'months': 0,
            'years': 0,
            'all': 0
        }
        
        # Count missing agenda profiles
        for agenda_id in agenda_ids:
            for category in categories:
                if not PoliticianProfilePart.objects.filter(
                    politician=politician,
                    category=category,
                    period_type='AGENDA',
                    agenda_item_id=agenda_id
                ).exists():
                    missing_counts['agendas'] += 1
        
        # Count missing plenary session profiles
        for plenary_id in plenary_ids:
            for category in categories:
                if not PoliticianProfilePart.objects.filter(
                    politician=politician,
                    category=category,
                    period_type='PLENARY_SESSION',
                    plenary_session_id=plenary_id
                ).exists():
                    missing_counts['sessions'] += 1
        
        # Count missing month profiles
        for month in months:
            for category in categories:
                if not PoliticianProfilePart.objects.filter(
                    politician=politician,
                    category=category,
                    period_type='MONTH',
                    month=month
                ).exists():
                    missing_counts['months'] += 1
        
        # Count missing year profiles
        for year in years:
            for category in categories:
                if not PoliticianProfilePart.objects.filter(
                    politician=politician,
                    category=category,
                    period_type='YEAR',
                    year=year
                ).exists():
                    missing_counts['years'] += 1
        
        # Count missing ALL profiles
        for category in categories:
            if not PoliticianProfilePart.objects.filter(
                politician=politician,
                category=category,
                period_type='ALL',
                agenda_item__isnull=True,
                plenary_session__isnull=True,
                month__isnull=True,
                year__isnull=True
            ).exists():
                missing_counts['all'] += 1
        
        # Calculate totals
        total_missing = sum(missing_counts.values())
        total_theoretical = (len(agenda_ids) + len(plenary_ids) + len(months) + len(years) + 1) * len(categories)
        existing_profiles = total_theoretical - total_missing
        
        self.stdout.write(f"\n📋 PROFILE GENERATION SUMMARY")
        self.stdout.write("─" * 50)
        self.stdout.write(f"   📊 Speeches to analyze: {len(speeches)}")
        self.stdout.write(f"   📂 Categories to generate: {len(categories)}")
        self.stdout.write(f"      {', '.join(categories)}")
        
        if existing_profiles > 0:
            self.stdout.write(f"\n   ✅ Already existing: {existing_profiles} profiles")
        
        self.stdout.write(f"\n   🎯 Missing profiles to generate:")
        self.stdout.write(f"      • Agenda-specific: {missing_counts['agendas']} profiles")
        self.stdout.write(f"      • Session-specific: {missing_counts['sessions']} profiles")
        self.stdout.write(f"      • Month-specific: {missing_counts['months']} profiles")
        self.stdout.write(f"      • Year-specific: {missing_counts['years']} profiles")
        self.stdout.write(f"      • General overview: {missing_counts['all']} profiles")
        self.stdout.write(f"\n   🎯 TOTAL PROFILES TO GENERATE: {total_missing}")
        
        if total_missing == 0:
            self.stdout.write(self.style.SUCCESS("\n   🎉 All profiles already exist! Nothing to generate."))
            return False
        
        # Show periods details
        if len(agenda_ids) <= 10:  # Show details if not too many
            self.stdout.write(f"\n   📅 Time periods covered:")
            if months:
                self.stdout.write(f"      • Months: {', '.join(sorted(months))}")
            if years:
                self.stdout.write(f"      • Years: {', '.join(map(str, sorted(years)))}")
        else:
            # Just show summary for large datasets
            month_range = f"{min(months)} to {max(months)}" if months else "None"
            year_range = f"{min(years)}-{max(years)}" if len(years) > 1 else str(list(years)[0]) if years else "None"
            self.stdout.write(f"\n   📅 Time range: {month_range} ({year_range})")
        
        self.stdout.write("─" * 50)
        self.stdout.write(f"   🔢 Estimated tokens: {token_count:,}")
        
        # Continue with cost estimation...
        
        # Estimate cost based on current pricing
        provider_info = self.ai_service.get_provider_info()
        if provider_info['provider'] == 'claude':
            # Claude Sonnet 4 pricing
            if token_count <= 200_000:
                input_cost = (token_count / 1_000_000) * 3.00  # $3/MTok for ≤200K tokens
                output_cost_per_mtok = 15.00  # $15/MTok for output
            else:
                input_cost = (token_count / 1_000_000) * 6.00  # $6/MTok for >200K tokens  
                output_cost_per_mtok = 22.50  # $22.50/MTok for output
            
            # Estimate output tokens (profile parts are typically longer than summaries)
            estimated_output_tokens = token_count * 0.25  # Conservative 25% estimate
            output_cost = (estimated_output_tokens / 1_000_000) * output_cost_per_mtok
            total_cost = input_cost + output_cost
            
            self.stdout.write(f"   Estimated cost (Standard): ~${total_cost:.4f}")
            
        elif provider_info['provider'] == 'openai':
            # OpenAI GPT-4o pricing (approximate)
            input_cost = (token_count / 1_000_000) * 2.50  # ~$2.50/MTok input
            estimated_output_tokens = token_count * 0.25
            output_cost = (estimated_output_tokens / 1_000_000) * 10.00  # ~$10/MTok output
            total_cost = input_cost + output_cost
            self.stdout.write(f"   Estimated cost: ~${total_cost:.4f}")
        elif provider_info['provider'] == 'ollama':
            self.stdout.write(f"   Cost: Free (local model)")
        
        if self.dry_run:
            self.stdout.write(self.style.WARNING("   (DRY RUN - no actual API call will be made)"))
            return True
        
        while True:
            response = input("\n❓ Do you want to proceed? (Y/N): ").strip().upper()
            if response in ['Y', 'YES']:
                return True
            elif response in ['N', 'NO']:
                return False
            else:
                self.stdout.write("Please enter Y or N")

    def _send_ai_request(self, xml_content, politician, speeches, categories):
        """Send the XML content to AI and get response with only missing categories"""
        if self.dry_run:
            self.stdout.write("🔍 DRY RUN: Skipping actual AI request")
            return self._generate_mock_response(categories)

        # Create prompt with only the requested categories and exclude processed periods
        categories_str = ", ".join(categories)
        excluded_periods = self._get_already_processed_periods(politician, categories, speeches)
        
        # Debug: Show exclusion information
        if excluded_periods:
            excluded_lines = excluded_periods.split('\n')
            self.stdout.write(f"\n📋 EXCLUSION INFORMATION ({len(excluded_lines)} lines):")
            self.stdout.write("─" * 50)
            # Show first 10 lines of exclusions
            for i, line in enumerate(excluded_lines[:10]):
                if line.strip():
                    self.stdout.write(f"   {line}")
            if len(excluded_lines) > 10:
                self.stdout.write(f"   ... and {len(excluded_lines) - 10} more exclusion lines")
            self.stdout.write("─" * 50)
        else:
            self.stdout.write("\n📋 No exclusions - generating all profiles")
        
        prompt = f"""Analyze the following speeches to create politician profile parts across different aspects. Write in Estonian language, speak like native Estonian.

{excluded_periods}

Generate profiles for the following categories: {categories_str}

Create profiles for these periods:
1. Each agenda item separately (AGENDA) - use aid="xxx"
2. Each plenary session separately (PLENARY_SESSION) - use plid="yyy"  
3. By months (MONTH) - use month="MM.YYYY"
4. By years (YEAR) - use year="YYYY"
5. General overview (ALL)

---

The speeches are marked with encrypted agenda item IDs (aid) and plenary session IDs (plid) along with dates.

{xml_content}

You are analyzing a collection of political speeches.
Your task is to produce structured **summaries** across different profile types and time scopes (agenda-level, plenary session-level, monthly, yearly).

For each profile type:

* Write **1–4 sentences**, if there is not enough information, write "Not enough data" in Estonian, don't guess or overthink.
* Summaries must be **concise, evidence-based, and neutral**.
* Every claim must be **grounded in the speeches** (no speculation).
* When mentioning an issue, include **who/what was emphasized, the stance taken, and intensity of support or opposition**.

---

## Profile Type Definitions

### POLITICAL_POSITION

* Identify the most salient issues.
* State direction and strength of stance (support/oppose, strong/moderate).
* Mention shifts compared to earlier periods.
* Note if framing is policy-driven, value-driven, or performance-driven.

### TOPIC_EXPERTISE

* Highlight topics where the speaker shows knowledge and authority.
* Mention use of data, technical terms, or statistics.
* Call out consistent explanations or reliance on expertise.

### RHETORICAL_STYLE

* Describe overall tone (conciliatory, combative, optimistic, urgent).
* Point out the balance between emotional and logical appeals.
* Mention formality, complexity, and use of storytelling vs data.

### ACTIVITY_PATTERNS

* Summarize frequency and rhythm of speeches or public appearances.
* Include references to events, meetings, or travel mentioned.
* Highlight recurring communication patterns (e.g., weekly updates).

### OPPOSITION_STANCE

* Identify main opponents or groups criticized.
* Clarify if critiques are policy-based, procedural, or personal.
* Note the intensity of attacks and whether compromise was ruled out.

### COLLABORATION_STYLE

* Mention cooperation with colleagues, co-sponsorships, or coalitions.
* Describe openness to compromise or mediation.
* Highlight references to bipartisan or cross-party collaboration.

### REGIONAL_FOCUS

* Point out attention to local/district vs national vs international issues.
* Mention specific regional industries, projects, or communities.

### ECONOMIC_VIEWS

* Summarize positions on taxes, spending, regulation, trade, and labor.
* Note attitudes toward redistribution, growth, or fiscal discipline.
* Mention affinity toward business interests vs labor concerns.

### SOCIAL_ISSUES

* State positions on abortion, LGBTQ+, immigration, guns, education, policing.
* Clarify balance between civil liberties and security.
* Mention religious or moral framing when used.

### LEGISLATIVE_FOCUS

* Identify legislative priorities (topics of bills, amendments, hearings).
* Describe whether the speaker is an initiator, supporter, or opponent.
* Note claimed progress or achievements.

---

## General Rules

* Be concise and neutral.
* Do not speculate beyond speech evidence.
* Focus on **issues, stances, tone, and patterns** that are explicitly present in the speeches.
* Avoid repeating the same phrases across summaries; tailor by scope (agenda/plenary session/month/year).

Response format:
<profiles>
<profile type="PROFILE_TYPE" period="AGENDA" aid="xxx">Analysis for this specific agenda item</profile>
<profile type="PROFILE_TYPE" period="PLENARY_SESSION" plid="yyy">Analysis for this specific plenary session</profile>
<profile type="PROFILE_TYPE" period="MONTH" month="01.2025">Analysis for this specific month</profile>
<profile type="PROFILE_TYPE" period="YEAR" year="2025">Analysis for this specific year</profile>
<profile type="PROFILE_TYPE" period="ALL">General analysis across all periods</profile>
</profiles>

Each profile description should be in Estonian language, like you are a native Estonian speaker, analytical and specific."""

        self.stdout.write("🔄 Streaming response from AI...")
        self.stdout.write("─" * 60)
        
        try:
            # Reset counters for this batch
            if hasattr(self, '_initial_missing_count'):
                delattr(self, '_initial_missing_count')
            if hasattr(self, '_processed_count'):
                delattr(self, '_processed_count')
            if hasattr(self, '_ai_violations'):
                delattr(self, '_ai_violations')
                
            # Use streaming API for real-time feedback with live parsing
            profile_parts = []
            buffer = ""  # Buffer to accumulate partial XML
            processed_profiles = {}  # Track processed profiles to avoid duplicates
            
            for chunk in self.ai_service.generate_summary_stream(prompt, max_tokens=15000, temperature=0.3):
                profile_parts.append(chunk)
                buffer += chunk
                
                # Print chunks in real-time
                self.stdout.write(chunk, ending='')
                self.stdout.flush()
                
                # Check for complete profile elements in buffer
                self._process_complete_profiles(buffer, politician, processed_profiles, speeches, categories)
            
            response = ''.join(profile_parts).strip()
            self.stdout.write("\n─" * 60)
            self.stdout.write("✅ Streaming complete!")
            
            # Final processing for any remaining profiles
            self._process_complete_profiles(response, politician, processed_profiles, speeches, categories, final=True)
            
            # Show AI violations summary if any occurred
            if hasattr(self, '_ai_violations') and self._ai_violations > 0:
                self.stdout.write(f"\n⚠️  AI VIOLATIONS SUMMARY: {self._ai_violations} profiles were generated despite being in exclusion list")
                self.stdout.write(f"   This indicates the AI is not following exclusion instructions properly.")
            
            return response
            
        except (ConnectionError, TimeoutError, requests.exceptions.RequestException) as e:
            # Network-related errors that might be temporary
            provider_info = self.ai_service.get_provider_info()
            self.stdout.write(f"\n⚠️  Network interruption detected: {str(e)}")
            
            # If we got partial response, try to process what we have
            if profile_parts:
                partial_response = ''.join(profile_parts).strip()
                self.stdout.write(f"📝 Processing partial response ({len(partial_response)} chars)")
                self._process_complete_profiles(partial_response, politician, {}, speeches, categories, final=True)
            
            logger.warning(f"Network interruption in {provider_info['provider']} API: {str(e)}")
            return None  # This will trigger retry logic
            
        except Exception as e:
            provider_info = self.ai_service.get_provider_info()
            logger.exception(f"Error calling {provider_info['provider']} API")
            self.stdout.write(self.style.ERROR(f"❌ API error: {str(e)}"))
            return None

    def _process_complete_profiles(self, buffer, politician, processed_profiles, speeches, categories, final=False):
        """Process complete profile elements from streaming buffer"""
        import re
        
        # Calculate initial remaining count for more accurate tracking
        if not hasattr(self, '_initial_missing_count'):
            self._initial_missing_count = self._count_missing_profiles(politician, categories, speeches)
            self._processed_count = 0
            self._ai_violations = 0  # Track AI exclusion violations
        
        # Find all complete <profile type="..." period="..." ...>...</profile> elements
        pattern = r'<profile\s+([^>]+)>([^<]*(?:<(?!/profile>)[^<]*)*)</profile>'
        matches = re.findall(pattern, buffer, re.DOTALL)
        
        for attributes_str, profile_text in matches:
            # Parse attributes
            attr_pattern = r'(\w+)="([^"]*)"'
            attributes = dict(re.findall(attr_pattern, attributes_str))
            
            # Create unique identifier for this profile
            profile_key = f"{attributes.get('type', '')}-{attributes.get('period', '')}-{attributes.get('aid', '')}-{attributes.get('plid', '')}-{attributes.get('month', '')}-{attributes.get('year', '')}"
            
            # Skip if already processed
            if profile_key in processed_profiles:
                continue
                
            # Mark as processed
            processed_profiles[profile_key] = True
            
            # Check if this profile should have been excluded (AI validation)
            should_be_excluded = self._check_if_profile_should_be_excluded(attributes, politician, speeches, categories)
            
            if should_be_excluded:
                self._ai_violations += 1
                self.stdout.write(f"\n🚨 AI VIOLATION #{self._ai_violations}: Generated excluded profile: {attributes.get('type', 'Unknown')} - {attributes.get('period', 'Unknown')} - {attributes.get('aid', '')}{attributes.get('plid', '')}{attributes.get('month', '')}{attributes.get('year', '')}")
                self.stdout.write(f"   This profile already exists and should have been skipped!")
                # Still process it but mark as violation
                success = self._process_single_profile_live(attributes, profile_text, politician)
                if success:
                    self._processed_count += 1
                    remaining_count = max(0, self._initial_missing_count - self._processed_count)
                    self.stdout.write(f"🟡 Processed despite violation | Profiles remaining: {remaining_count}")
            else:
                # Process this profile immediately
                success = self._process_single_profile_live(attributes, profile_text, politician)
                
                if success:
                    # Update processed count and calculate remaining profiles
                    self._processed_count += 1
                    remaining_count = max(0, self._initial_missing_count - self._processed_count)
                    self.stdout.write(f"\n🟢 Live processed: {attributes.get('type', 'Unknown')} - {attributes.get('period', 'Unknown')} | Profiles remaining: {remaining_count}")
                else:
                    self.stdout.write(f"\n🔴 Failed to process: {attributes.get('type', 'Unknown')} - {attributes.get('period', 'Unknown')}")

    def _check_if_profile_should_be_excluded(self, attributes, politician, speeches, categories):
        """Check if a profile being generated should have been excluded (already exists)"""
        category = attributes.get('type')
        period_type = attributes.get('period')
        
        if not category or not period_type:
            return False
        
        # Build query to check if this profile already exists
        query_filters = {
            'politician': politician,
            'category': category,
            'period_type': period_type,
        }
        
        # Add period-specific filters
        if period_type == 'AGENDA':
            encrypted_aid = attributes.get('aid')
            if not encrypted_aid or encrypted_aid not in self.agenda_id_mapping:
                return False
            agenda_id = self.agenda_id_mapping[encrypted_aid]
            query_filters['agenda_item_id'] = agenda_id
            
        elif period_type == 'PLENARY_SESSION':
            encrypted_plid = attributes.get('plid')
            if not encrypted_plid or encrypted_plid not in self.plenary_id_mapping:
                return False
            plenary_id = self.plenary_id_mapping[encrypted_plid]
            query_filters['plenary_session_id'] = plenary_id
            
        elif period_type == 'MONTH':
            month = attributes.get('month')
            if not month:
                return False
            query_filters['month'] = month
            
        elif period_type == 'YEAR':
            year_str = attributes.get('year')
            if not year_str:
                return False
            try:
                year = int(year_str)
                query_filters['year'] = year
            except ValueError:
                return False
                
        elif period_type == 'ALL':
            # For ALL profiles, ensure null values for period fields
            query_filters.update({
                'agenda_item__isnull': True,
                'plenary_session__isnull': True,
                'month__isnull': True,
                'year__isnull': True
            })
        
        # Check if profile already exists
        return PoliticianProfilePart.objects.filter(**query_filters).exists()

    def _process_single_profile_live(self, attributes, profile_text, politician):
        """Process a single profile element during live streaming and save to database"""
        try:
            category = attributes.get('type')
            period_type = attributes.get('period')
            
            if not category or not period_type or not profile_text:
                return False
            
            # Unescape XML entities
            analysis_text = unescape(profile_text.strip())
            
            # Determine period identifiers
            period_data = self._extract_period_data_from_attributes(attributes, period_type)
            if period_data is None:
                return False
            
            # Calculate basic metrics (we'll have full speeches context from the main process)
            metrics = {'speeches_count': 0}  # Will be updated with proper metrics later
            
            # Check if profile part already exists
            existing_query = {
                'politician': politician,
                'category': category,
                'period_type': period_type,
                **{k: v for k, v in period_data.items() if v is not None}
            }
            
            existing_profile = PoliticianProfilePart.objects.filter(**existing_query).first()
            
            # Prepare profile data
            from django.utils import timezone
            profile_data = {
                'analysis': analysis_text,
                'metrics': metrics,
                'speeches_analyzed': 0,  # Will be updated later
                'ai_summary_generated_at': timezone.now(),
                **period_data
            }
            
            if not self.dry_run:
                if existing_profile:
                    # Update existing
                    for key, value in profile_data.items():
                        setattr(existing_profile, key, value)
                    existing_profile.save()
                else:
                    # Create new
                    profile_data.update({
                        'politician': politician,
                        'category': category,
                        'period_type': period_type,
                    })
                    PoliticianProfilePart.objects.create(**profile_data)
            
            return True
            
        except Exception as e:
            logger.exception(f"Error processing single profile live")
            return False

    def _extract_period_data_from_attributes(self, attributes, period_type):
        """Extract period-specific data from profile attributes"""
        if period_type == 'AGENDA':
            encrypted_aid = attributes.get('aid')
            if not encrypted_aid or encrypted_aid not in self.agenda_id_mapping:
                return None
            agenda_id = self.agenda_id_mapping[encrypted_aid]
            try:
                agenda_item = AgendaItem.objects.get(id=agenda_id)
                return {'agenda_item': agenda_item}
            except AgendaItem.DoesNotExist:
                return None
                
        elif period_type == 'PLENARY_SESSION':
            encrypted_plid = attributes.get('plid')
            if not encrypted_plid or encrypted_plid not in self.plenary_id_mapping:
                return None
            plenary_id = self.plenary_id_mapping[encrypted_plid]
            try:
                plenary_session = PlenarySession.objects.get(id=plenary_id)
                return {'plenary_session': plenary_session}
            except PlenarySession.DoesNotExist:
                return None
                
        elif period_type == 'MONTH':
            month = attributes.get('month')
            if not month:
                return None
            return {'month': month}
            
        elif period_type == 'YEAR':
            year_str = attributes.get('year')
            if not year_str:
                return None
            try:
                year = int(year_str)
                return {'year': year}
            except ValueError:
                return None
                
        elif period_type == 'ALL':
            return {}
            
        return None

    def _show_final_profile_summary(self, politician, speeches, categories):
        """Show final summary of processed profile parts"""
        # Count how many profile parts now exist for each category
        
        # Collect periods from speeches data
        agenda_ids, plenary_ids, months, years = self._collect_periods_from_speeches(speeches)
        
        # Calculate expected total profiles per category
        expected_per_category = len(agenda_ids) + len(plenary_ids) + len(months) + len(years) + 1  # +1 for ALL
        total_expected = expected_per_category * len(categories)
        
        # Count actual profiles
        total_actual = PoliticianProfilePart.objects.filter(politician=politician).count()
        
        self.stdout.write(f"\n📊 FINAL SUMMARY:")
        self.stdout.write(f"   📋 Expected profiles per category: {expected_per_category}")
        self.stdout.write(f"   📋 Categories processed: {len(categories)}")
        self.stdout.write(f"   ✅ Total profiles created: {total_actual}/{total_expected}")
        
        if total_actual == total_expected:
            self.stdout.write(self.style.SUCCESS(f"   🎉 All profiles processed successfully!"))
        elif total_actual > 0:
            remaining = total_expected - total_actual
            self.stdout.write(self.style.WARNING(f"   ⚠️  {remaining} profiles still need processing"))
        else:
            self.stdout.write(self.style.ERROR(f"   ❌ No profiles were processed"))
        
        # Show breakdown by category
        for category in categories:
            category_count = PoliticianProfilePart.objects.filter(
                politician=politician,
                category=category
            ).count()
            self.stdout.write(f"   📊 {category}: {category_count}/{expected_per_category}")
        
        if self.dry_run:
            self.stdout.write(self.style.WARNING("   (DRY RUN - no profiles were actually saved)"))

    def _generate_mock_response(self, categories):
        """Generate a mock response for dry run"""
        mock_profiles = []
        
        for category in categories:
            # Mock profiles for different periods
            for encrypted_aid in list(self.agenda_id_mapping.keys())[:2]:  # First 2 agendas
                mock_profiles.append(f'<profile type="{category}" period="AGENDA" aid="{encrypted_aid}">Mock analüüs päevakorrapunkti jaoks kategoorias {category}</profile>')
            
            for encrypted_plid in list(self.plenary_id_mapping.keys())[:2]:  # First 2 plenary sessions
                mock_profiles.append(f'<profile type="{category}" period="PLENARY_SESSION" plid="{encrypted_plid}">Mock analüüs istungjärgu jaoks kategoorias {category}</profile>')
            
            mock_profiles.append(f'<profile type="{category}" period="MONTH" month="01.2025">Mock analüüs kuu jaoks kategoorias {category}</profile>')
            mock_profiles.append(f'<profile type="{category}" period="YEAR" year="2025">Mock analüüs aasta jaoks kategoorias {category}</profile>')
            mock_profiles.append(f'<profile type="{category}" period="ALL">Mock üldine analüüs kategoorias {category}</profile>')
        
        return f'<profiles>\n{chr(10).join(mock_profiles)}\n</profiles>'

    def _parse_and_save_profile_parts(self, ai_response, politician, speeches, overwrite):
        """Parse AI response XML and save profile parts"""
        created_count = 0
        updated_count = 0
        skipped_count = 0
        
        try:
            # Extract profiles section
            profiles_match = re.search(r'<profiles>(.*?)</profiles>', ai_response, re.DOTALL)
            if not profiles_match:
                self.stdout.write(self.style.ERROR("❌ No <profiles> section found in AI response"))
                return
            
            profiles_xml = f"<profiles>{profiles_match.group(1)}</profiles>"
            
            # Parse XML
            try:
                root = ET.fromstring(profiles_xml)
            except ET.ParseError as e:
                self.stdout.write(self.style.ERROR(f"❌ Failed to parse AI response XML: {e}"))
                return
            
            # Process each profile
            for profile_elem in root.findall('profile'):
                try:
                    result = self._process_single_profile(profile_elem, politician, speeches, overwrite)
                    if result == 'created':
                        created_count += 1
                    elif result == 'updated':
                        updated_count += 1
                    elif result == 'skipped':
                        skipped_count += 1
                except Exception as e:
                    self.stdout.write(self.style.ERROR(f"🔴 Error processing profile: {str(e)}"))
                    skipped_count += 1
        
        except Exception as e:
            logger.exception("Error parsing AI response")
            self.stdout.write(self.style.ERROR(f"❌ Error parsing AI response: {str(e)}"))
        
        # Final summary
        self.stdout.write(f"\n📊 PROCESSING SUMMARY:")
        self.stdout.write(f"   ✅ Created: {created_count}")
        self.stdout.write(f"   🔄 Updated: {updated_count}")
        if skipped_count > 0:
            self.stdout.write(f"   ⏭️  Skipped: {skipped_count}")
        
        if self.dry_run:
            self.stdout.write(self.style.WARNING("   (DRY RUN - no profile parts were actually saved)"))

    def _process_single_profile(self, profile_elem, politician, speeches, overwrite):
        """Process a single profile element and save to database"""
        # Extract attributes
        category = profile_elem.get('type')
        period_type = profile_elem.get('period')
        analysis_text = profile_elem.text
        
        if not category or not period_type or not analysis_text:
            self.stdout.write(self.style.ERROR(f"🔴 Skipped malformed profile element"))
            return 'skipped'
        
        # Unescape XML entities
        analysis_text = unescape(analysis_text.strip())
        
        # Determine period identifiers
        period_data = self._extract_period_data(profile_elem, period_type)
        if period_data is None:
            self.stdout.write(self.style.ERROR(f"🔴 Skipped profile with invalid period data: {period_type}"))
            return 'skipped'
        
        # Calculate metrics for this profile part
        metrics = self._calculate_profile_metrics(politician, speeches, category, period_data)
        
        # Check if profile part already exists
        existing_query = {
            'politician': politician,
            'category': category,
            'period_type': period_type,
            **{k: v for k, v in period_data.items() if v is not None}
        }
        
        existing_profile = PoliticianProfilePart.objects.filter(**existing_query).first()
        
        if existing_profile and not overwrite:
            self.stdout.write(f"⏭️  Skipped existing: {category} - {period_type}")
            return 'skipped'
        
        # Prepare profile data
        from django.utils import timezone
        profile_data = {
            'analysis': analysis_text,
            'metrics': metrics,
            'speeches_analyzed': metrics.get('speeches_count', 0),
            'date_range_start': metrics.get('date_range_start'),
            'date_range_end': metrics.get('date_range_end'),
            'ai_summary_generated_at': timezone.now(),
            **period_data
        }
        
        if not self.dry_run:
            if existing_profile:
                # Update existing - clear translations if content changed
                if existing_profile.analysis != analysis_text:
                    profile_data['analysis_en'] = None
                    profile_data['analysis_ru'] = None
                
                for key, value in profile_data.items():
                    setattr(existing_profile, key, value)
                existing_profile.save()
                self.stdout.write(f"🔄 Updated: {category} - {period_type}")
                return 'updated'
            else:
                # Create new
                profile_data.update({
                    'politician': politician,
                    'category': category,
                    'period_type': period_type,
                })
                PoliticianProfilePart.objects.create(**profile_data)
                self.stdout.write(f"✅ Created: {category} - {period_type}")
                return 'created'
        else:
            action = "Updated" if existing_profile else "Created"
            self.stdout.write(f"🔍 DRY RUN - Would {action.lower()}: {category} - {period_type}")
            return 'created' if not existing_profile else 'updated'

    def _extract_period_data(self, profile_elem, period_type):
        """Extract period-specific data from profile element"""
        if period_type == 'AGENDA':
            encrypted_aid = profile_elem.get('aid')
            if not encrypted_aid or encrypted_aid not in self.agenda_id_mapping:
                return None
            agenda_id = self.agenda_id_mapping[encrypted_aid]
            try:
                agenda_item = AgendaItem.objects.get(id=agenda_id)
                return {'agenda_item': agenda_item}
            except AgendaItem.DoesNotExist:
                return None
                
        elif period_type == 'PLENARY_SESSION':
            encrypted_plid = profile_elem.get('plid')
            if not encrypted_plid or encrypted_plid not in self.plenary_id_mapping:
                return None
            plenary_id = self.plenary_id_mapping[encrypted_plid]
            try:
                plenary_session = PlenarySession.objects.get(id=plenary_id)
                return {'plenary_session': plenary_session}
            except PlenarySession.DoesNotExist:
                return None
                
        elif period_type == 'MONTH':
            month = profile_elem.get('month')
            if not month:
                return None
            return {'month': month}
            
        elif period_type == 'YEAR':
            year_str = profile_elem.get('year')
            if not year_str:
                return None
            try:
                year = int(year_str)
                return {'year': year}
            except ValueError:
                return None
                
        elif period_type == 'ALL':
            return {}
            
        return None

    def _calculate_profile_metrics(self, politician, speeches, category, period_data):
        """Calculate metrics for a specific profile part"""
        # Filter speeches based on period
        filtered_speeches = self._filter_speeches_by_period(speeches, period_data)
        
        metrics = {
            'speeches_count': len(filtered_speeches),
            'date_range_start': None,
            'date_range_end': None,
        }
        
        if filtered_speeches:
            dates = [speech.date.date() for speech in filtered_speeches]
            metrics['date_range_start'] = min(dates)
            metrics['date_range_end'] = max(dates)
            
            # Add category-specific metrics
            if category == 'ACTIVITY_PATTERNS':
                # Calculate monthly distribution
                monthly_counts = defaultdict(int)
                for speech in filtered_speeches:
                    month_key = f"{speech.date.year}-{speech.date.month:02d}"
                    monthly_counts[month_key] += 1
                metrics['monthly_distribution'] = dict(monthly_counts)
                
            elif category == 'RHETORICAL_STYLE':
                # Calculate speech length statistics
                lengths = [len(speech.text) for speech in filtered_speeches if speech.text]
                if lengths:
                    metrics['avg_speech_length'] = sum(lengths) / len(lengths)
                    metrics['min_speech_length'] = min(lengths)
                    metrics['max_speech_length'] = max(lengths)
        
        return metrics

    def _filter_speeches_by_period(self, speeches, period_data):
        """Filter speeches based on period criteria"""
        if 'agenda_item' in period_data and period_data['agenda_item']:
            return [s for s in speeches if s.agenda_item_id == period_data['agenda_item'].id]
        elif 'plenary_session' in period_data and period_data['plenary_session']:
            return [s for s in speeches if s.agenda_item.plenary_session_id == period_data['plenary_session'].id]
        elif 'month' in period_data and period_data['month']:
            try:
                month, year = period_data['month'].split('.')
                month, year = int(month), int(year)
                return [s for s in speeches if s.date.month == month and s.date.year == year]
            except (ValueError, AttributeError):
                return []
        elif 'year' in period_data and period_data['year']:
            year = period_data['year']
            return [s for s in speeches if s.date.year == year]
        else:  # ALL period
            return list(speeches)
    
    # ========================================================================
    # BATCH API METHODS FOR PROFILE GENERATION
    # ========================================================================
    
    def _process_periods_with_batch_api(self, politician, speeches, categories, all_periods, overwrite):
        """Process all periods using Gemini Batch API"""
        # Create a wrapper class to hold period information
        class PeriodWrapper:
            def __init__(self, pk, period_type, period_id, politician, speeches, categories):
                self.pk = pk  # Unique ID for batch processing
                self.period_type = period_type
                self.period_id = period_id
                self.politician = politician
                self.speeches = speeches
                self.categories = categories
        
        # Create wrappers for all periods
        period_wrappers = []
        for idx, (period_type, period_id) in enumerate(all_periods):
            wrapper = PeriodWrapper(idx, period_type, period_id, politician, speeches, categories)
            period_wrappers.append(wrapper)
        
        # Process using batch API with chunking
        self.process_batch_with_chunking(
            period_wrappers,
            "politician profile periods",
            self._create_period_profile_prompt,
            lambda wrapper, result: self._update_period_with_profile(wrapper, result, overwrite)
        )
    
    def _create_period_profile_prompt(self, period_wrapper):
        """Create profile generation prompt for a period using batch API"""
        try:
            period_type = period_wrapper.period_type
            period_id = period_wrapper.period_id
            politician = period_wrapper.politician
            speeches = period_wrapper.speeches
            categories = period_wrapper.categories
            
            # Get period-specific data
            if period_type == 'AGENDA':
                try:
                    agenda_item = AgendaItem.objects.get(id=period_id)
                    period_speeches = [s for s in speeches if s.agenda_item_id == period_id]
                    period_title = agenda_item.title
                except AgendaItem.DoesNotExist:
                    return None
            elif period_type == 'PLENARY_SESSION':
                try:
                    plenary = PlenarySession.objects.get(id=period_id)
                    period_speeches = [s for s in speeches if s.agenda_item.plenary_session_id == period_id]
                    period_title = plenary.title
                except PlenarySession.DoesNotExist:
                    return None
            elif period_type == 'MONTH':
                try:
                    month, year = period_id.split('.')
                    month, year = int(month), int(year)
                    period_speeches = [s for s in speeches if s.date.month == month and s.date.year == year]
                    period_title = period_id
                except (ValueError, AttributeError):
                    return None
            elif period_type == 'YEAR':
                period_speeches = [s for s in speeches if s.date.year == period_id]
                period_title = str(period_id)
            else:
                return None
            
            if not period_speeches:
                return None
            
            # Generate XML for this period
            xml_content = self._generate_period_xml(period_speeches, period_type)
            
            # Create prompt for this specific period with all categories
            prompt = self._create_period_prompt(categories, xml_content, period_type, period_title)
            
            return prompt
            
        except Exception as e:
            logger.exception(f"Error creating prompt for period {period_wrapper.period_type} {period_wrapper.period_id}")
            return None
    
    def _update_period_with_profile(self, period_wrapper, ai_response, overwrite):
        """Update politician profile with AI-generated analysis from batch API"""
        try:
            period_type = period_wrapper.period_type
            period_id = period_wrapper.period_id
            politician = period_wrapper.politician
            speeches = period_wrapper.speeches
            categories = period_wrapper.categories
            
            # Get period-specific data again for saving
            if period_type == 'AGENDA':
                agenda_item = AgendaItem.objects.get(id=period_id)
                period_speeches = [s for s in speeches if s.agenda_item_id == period_id]
            elif period_type == 'PLENARY_SESSION':
                plenary = PlenarySession.objects.get(id=period_id)
                period_speeches = [s for s in speeches if s.agenda_item.plenary_session_id == period_id]
            elif period_type == 'MONTH':
                month, year = period_id.split('.')
                month, year = int(month), int(year)
                period_speeches = [s for s in speeches if s.date.month == month and s.date.year == year]
            elif period_type == 'YEAR':
                period_speeches = [s for s in speeches if s.date.year == period_id]
            else:
                return False
            
            # Parse and save the AI response
            # Prepare keyword arguments based on period type
            kwargs = {}
            if period_type == 'AGENDA':
                kwargs['agenda_item'] = agenda_item
            elif period_type == 'PLENARY_SESSION':
                kwargs['plenary_session'] = plenary
            elif period_type == 'MONTH':
                kwargs['month'] = period_id
            elif period_type == 'YEAR':
                kwargs['year'] = period_id
            
            success = self._parse_and_save_period_profiles(
                ai_response, 
                politician, 
                period_speeches, 
                categories,
                period_type,  # Pass period_type string, not dict
                **kwargs  # Unpack period-specific kwargs
            )
            
            return success
            
        except Exception as e:
            logger.exception(f"Error updating period {period_wrapper.period_type} {period_wrapper.period_id}")
            self.stdout.write(self.style.ERROR(f"Failed to update period {period_wrapper.period_type} {period_wrapper.period_id}: {str(e)}"))
            return False
    
    def _process_all_profiles_with_batch_api(self, politician, categories, profiles_by_category, overwrite):
        """Process ALL profiles using Gemini Batch API"""
        # Create wrapper class for ALL profile generation
        class AllProfileWrapper:
            def __init__(self, pk, category, politician, monthly_profiles):
                self.pk = pk  # Unique ID for batch processing
                self.category = category
                self.politician = politician
                self.monthly_profiles = monthly_profiles
        
        # Collect all categories that need ALL profile generation
        all_profile_wrappers = []
        idx = 0
        
        for category in categories:
            if category not in profiles_by_category:
                self.stdout.write(f"⚠️  No monthly profiles found for category: {category}")
                continue
            
            # Check if ALL profile already exists
            existing_all = PoliticianProfilePart.objects.filter(
                politician=politician,
                category=category,
                period_type='ALL',
                agenda_item__isnull=True,
                plenary_session__isnull=True,
                month__isnull=True,
                year__isnull=True
            ).first()
            
            if existing_all and not overwrite:
                self.stdout.write(f"⏭️  ALL profile already exists for {category}")
                continue
            
            monthly_profiles_for_category = profiles_by_category[category]
            wrapper = AllProfileWrapper(idx, category, politician, monthly_profiles_for_category)
            all_profile_wrappers.append(wrapper)
            idx += 1
        
        if not all_profile_wrappers:
            self.stdout.write("✅ All ALL profiles already exist")
            return
        
        self.stdout.write(self.style.HTTP_INFO(f"Using Google Gemini BATCH API for ALL profiles"))
        self.stdout.write("=" * 80)
        
        # Process using batch API
        self.process_batch_with_chunking(
            all_profile_wrappers,
            "ALL profiles",
            self._create_all_profile_prompt,
            lambda wrapper, result: self._update_all_profile(wrapper, result, overwrite)
        )
    
    def _create_all_profile_prompt(self, wrapper):
        """Create ALL profile generation prompt from monthly profiles"""
        try:
            category = wrapper.category
            monthly_profiles = wrapper.monthly_profiles
            
            # Prepare monthly profiles data for AI
            monthly_data = []
            for profile in monthly_profiles:
                monthly_data.append({
                    'month': profile.month,
                    'analysis': profile.analysis,
                    'speeches_analyzed': profile.speeches_analyzed,
                    'date_range_start': profile.date_range_start,
                    'date_range_end': profile.date_range_end,
                    'is_incomplete': profile.is_incomplete
                })
            
            # Create aggregated monthly profiles text
            monthly_summaries = []
            for data in monthly_data:
                summary = f"Month: {data['month']}\n"
                summary += f"Analysis: {data['analysis']}\n"
                summary += f"Speeches: {data['speeches_analyzed']}\n"
                if data['is_incomplete']:
                    summary += "(Note: This month has incomplete data)\n"
                monthly_summaries.append(summary)
            
            monthly_text = "\n---\n".join(monthly_summaries)
            
            # Create prompt for aggregating monthly profiles
            prompt = f"""Based on the following monthly profiles for category "{category}", create a comprehensive ALL-TIME profile that summarizes the politician's overall stance and behavior.

MONTHLY PROFILES:
{monthly_text}

Please analyze these monthly profiles and create a comprehensive summary that:
1. Identifies the main themes and positions across all months
2. Notes any evolution or changes in stance over time
3. Highlights the most consistent patterns
4. Provides an overall characterization

Provide ONLY the analysis text in Estonian, without any additional formatting or explanations. Write 2-3 paragraphs maximum."""
            
            return prompt
            
        except Exception as e:
            logger.exception(f"Error creating ALL profile prompt for category {wrapper.category}")
            return None
    
    def _update_all_profile(self, wrapper, ai_response, overwrite):
        """Update ALL profile with AI-generated analysis"""
        try:
            category = wrapper.category
            politician = wrapper.politician
            monthly_profiles = wrapper.monthly_profiles
            
            # Calculate aggregate statistics from monthly profiles
            total_speeches = sum(p.speeches_analyzed for p in monthly_profiles)
            date_range_start = min(p.date_range_start for p in monthly_profiles if p.date_range_start)
            date_range_end = max(p.date_range_end for p in monthly_profiles if p.date_range_end)
            has_incomplete = any(p.is_incomplete for p in monthly_profiles)
            
            # Create or update ALL profile
            all_profile, created = PoliticianProfilePart.objects.update_or_create(
                politician=politician,
                category=category,
                period_type='ALL',
                agenda_item=None,
                plenary_session=None,
                month=None,
                year=None,
                defaults={
                    'analysis': ai_response.strip(),
                    'speeches_analyzed': total_speeches,
                    'date_range_start': date_range_start,
                    'date_range_end': date_range_end,
                    'is_incomplete': has_incomplete
                }
            )
            
            if not self.dry_run:
                action = "Created" if created else "Updated"
                self.stdout.write(f"✅ {action} ALL profile for {category}")
            
            return True
            
        except Exception as e:
            logger.exception(f"Error updating ALL profile for category {wrapper.category}")
            self.stdout.write(self.style.ERROR(f"Failed to update ALL profile for {wrapper.category}: {str(e)}"))
            return False

