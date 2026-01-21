"""
Management command to verify and fix is_incomplete flags for all entity types
"""
import logging
from django.core.management.base import BaseCommand
from django.db.models import Q, Exists, OuterRef
from parliament_speeches.models import (
    Speech, PlenarySession, AgendaItem, AgendaSummary, 
    AgendaDecision, AgendaActivePolitician, PoliticianProfilePart
)

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = '''Verify and fix is_incomplete flags for all entity types.
    
    This command checks and updates the is_incomplete flags based on:
    - Speech: Based on parsing status (checks for missing text or metadata)
    - PlenarySession: Has incomplete speeches
    - AgendaItem: Has incomplete speeches
    - AgendaSummary: Related agenda has incomplete speeches
    - AgendaDecision: Related agenda has incomplete speeches
    - AgendaActivePolitician: Related agenda has incomplete speeches
    - PoliticianProfilePart: Has incomplete speeches in the analyzed period
    
    Usage:
    - Fix all: python manage.py fix_incomplete_flags
    - Dry run: python manage.py fix_incomplete_flags --dry-run
    - Specific type: python manage.py fix_incomplete_flags --type speech
    '''

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show what would be changed without making changes'
        )
        parser.add_argument(
            '--type',
            type=str,
            choices=['speech', 'plenary', 'agenda', 'summary', 'decision', 'active', 'profile', 'all'],
            default='all',
            help='Which entity type to fix (default: all)'
        )
        parser.add_argument(
            '--verbose',
            action='store_true',
            help='Show detailed changes for each entity'
        )

    def handle(self, *args, **options):
        self.dry_run = options['dry_run']
        self.verbose = options['verbose']
        entity_type = options['type']
        
        if self.dry_run:
            self.stdout.write(self.style.WARNING("🔍 DRY RUN MODE - No changes will be saved"))
        
        self.stdout.write(self.style.SUCCESS(f"🔧 Fixing is_incomplete flags for: {entity_type}"))
        self.stdout.write("=" * 80)
        
        stats = {}
        
        # Fix each entity type based on selection
        if entity_type in ['speech', 'all']:
            stats['speech'] = self.fix_speech_flags()
        
        if entity_type in ['plenary', 'all']:
            stats['plenary'] = self.fix_plenary_session_flags()
        
        if entity_type in ['agenda', 'all']:
            stats['agenda'] = self.fix_agenda_item_flags()
        
        if entity_type in ['summary', 'all']:
            stats['summary'] = self.fix_agenda_summary_flags()
        
        if entity_type in ['decision', 'all']:
            stats['decision'] = self.fix_agenda_decision_flags()
        
        if entity_type in ['active', 'all']:
            stats['active'] = self.fix_agenda_active_politician_flags()
        
        if entity_type in ['profile', 'all']:
            stats['profile'] = self.fix_politician_profile_flags()
        
        # Show final summary
        self.stdout.write("\n" + "=" * 80)
        self.stdout.write(self.style.SUCCESS("📊 FINAL SUMMARY"))
        self.stdout.write("=" * 80)
        
        total_checked = 0
        total_fixed = 0
        
        for entity_name, entity_stats in stats.items():
            total_checked += entity_stats['checked']
            total_fixed += entity_stats['fixed']
            
            if entity_stats['fixed'] > 0:
                self.stdout.write(
                    f"  {entity_name.upper()}: "
                    f"{entity_stats['fixed']}/{entity_stats['checked']} fixed "
                    f"({entity_stats['set_true']} → True, {entity_stats['set_false']} → False)"
                )
            else:
                self.stdout.write(f"  {entity_name.upper()}: {entity_stats['checked']} checked, all correct ✓")
        
        self.stdout.write("=" * 80)
        self.stdout.write(f"📊 Total: {total_fixed}/{total_checked} entities fixed")
        
        if self.dry_run:
            self.stdout.write(self.style.WARNING("🔍 DRY RUN - No changes were saved"))
        else:
            self.stdout.write(self.style.SUCCESS("✅ All changes saved"))

    def fix_speech_flags(self):
        """Fix is_incomplete flags for Speech entities"""
        self.stdout.write("\n🎤 Fixing Speech flags...")
        
        speeches = Speech.objects.filter(event_type='SPEECH')
        total = speeches.count()
        fixed = 0
        set_true = 0
        set_false = 0
        checked = 0
        
        self.stdout.write(f"   Checking {total:,} speeches...")
        
        # Show progress every N records
        progress_interval = 5000 if total > 50000 else 1000
        
        for speech in speeches.iterator(chunk_size=1000):
            checked += 1
            
            # Show progress
            if checked % progress_interval == 0:
                percentage = (checked / total) * 100
                self.stdout.write(
                    f"   📊 Progress: {checked:,}/{total:,} ({percentage:.1f}%) - "
                    f"Fixed so far: {fixed:,}",
                    ending='\r'
                )
                self.stdout.flush()
            
            # Speech is incomplete if it lacks proper text or critical metadata
            should_be_incomplete = not speech.text or not speech.text.strip()
            
            if speech.is_incomplete != should_be_incomplete:
                if self.verbose:
                    status = "incomplete" if should_be_incomplete else "complete"
                    self.stdout.write(
                        f"\n   🔧 Speech {speech.id}: {speech.politician.full_name if speech.politician else 'Unknown'} "
                        f"on {speech.date} → {status}"
                    )
                
                if not self.dry_run:
                    speech.is_incomplete = should_be_incomplete
                    speech.save(update_fields=['is_incomplete'])
                
                fixed += 1
                if should_be_incomplete:
                    set_true += 1
                else:
                    set_false += 1
        
        # Clear progress line and show final result
        self.stdout.write(' ' * 100, ending='\r')  # Clear the progress line
        self.stdout.write(f"   ✅ Checked {total:,}, fixed {fixed:,}")
        return {'checked': total, 'fixed': fixed, 'set_true': set_true, 'set_false': set_false}

    def fix_plenary_session_flags(self):
        """Fix is_incomplete flags for PlenarySession entities"""
        self.stdout.write("\n🏛️  Fixing PlenarySession flags...")
        
        sessions = PlenarySession.objects.all()
        total = sessions.count()
        fixed = 0
        set_true = 0
        set_false = 0
        
        self.stdout.write(f"   Checking {total} plenary sessions...")
        
        for session in sessions.iterator(chunk_size=100):
            # Session is incomplete if it has any incomplete speeches
            has_incomplete_speeches = Speech.objects.filter(
                agenda_item__plenary_session=session,
                event_type='SPEECH',
                is_incomplete=True
            ).exists()
            
            if session.is_incomplete != has_incomplete_speeches:
                if self.verbose:
                    status = "incomplete" if has_incomplete_speeches else "complete"
                    self.stdout.write(f"   🔧 Session {session.id}: {session.title[:50]}... → {status}")
                
                if not self.dry_run:
                    session.is_incomplete = has_incomplete_speeches
                    session.save(update_fields=['is_incomplete'])
                
                fixed += 1
                if has_incomplete_speeches:
                    set_true += 1
                else:
                    set_false += 1
        
        self.stdout.write(f"   ✅ Checked {total}, fixed {fixed}")
        return {'checked': total, 'fixed': fixed, 'set_true': set_true, 'set_false': set_false}

    def fix_agenda_item_flags(self):
        """Fix is_incomplete flags for AgendaItem entities"""
        self.stdout.write("\n📋 Fixing AgendaItem flags...")
        
        agendas = AgendaItem.objects.all()
        total = agendas.count()
        fixed = 0
        set_true = 0
        set_false = 0
        checked = 0
        
        self.stdout.write(f"   Checking {total:,} agenda items...")
        
        # Show progress every N records
        progress_interval = 1000 if total > 10000 else 100
        
        for agenda in agendas.iterator(chunk_size=100):
            checked += 1
            
            # Show progress
            if checked % progress_interval == 0:
                percentage = (checked / total) * 100
                self.stdout.write(
                    f"   📊 Progress: {checked:,}/{total:,} ({percentage:.1f}%) - "
                    f"Fixed so far: {fixed:,}",
                    ending='\r'
                )
                self.stdout.flush()
            
            # Agenda is incomplete if it has any incomplete speeches
            has_incomplete_speeches = Speech.objects.filter(
                agenda_item=agenda,
                event_type='SPEECH',
                is_incomplete=True
            ).exists()
            
            if agenda.is_incomplete != has_incomplete_speeches:
                if self.verbose:
                    status = "incomplete" if has_incomplete_speeches else "complete"
                    self.stdout.write(f"\n   🔧 Agenda {agenda.id}: {agenda.title[:50]}... → {status}")
                
                if not self.dry_run:
                    agenda.is_incomplete = has_incomplete_speeches
                    agenda.save(update_fields=['is_incomplete'])
                
                fixed += 1
                if has_incomplete_speeches:
                    set_true += 1
                else:
                    set_false += 1
        
        # Clear progress line and show final result
        self.stdout.write(' ' * 100, ending='\r')  # Clear the progress line
        self.stdout.write(f"   ✅ Checked {total:,}, fixed {fixed:,}")
        return {'checked': total, 'fixed': fixed, 'set_true': set_true, 'set_false': set_false}

    def fix_agenda_summary_flags(self):
        """Fix is_incomplete flags for AgendaSummary entities"""
        self.stdout.write("\n📝 Fixing AgendaSummary flags...")
        
        summaries = AgendaSummary.objects.select_related('agenda_item').all()
        total = summaries.count()
        fixed = 0
        set_true = 0
        set_false = 0
        checked = 0
        
        self.stdout.write(f"   Checking {total:,} agenda summaries...")
        
        # Show progress every N records
        progress_interval = 1000 if total > 10000 else 100
        
        for summary in summaries.iterator(chunk_size=100):
            checked += 1
            
            # Show progress
            if checked % progress_interval == 0:
                percentage = (checked / total) * 100
                self.stdout.write(
                    f"   📊 Progress: {checked:,}/{total:,} ({percentage:.1f}%) - "
                    f"Fixed so far: {fixed:,}",
                    ending='\r'
                )
                self.stdout.flush()
            
            # Summary is incomplete if its agenda has incomplete speeches
            has_incomplete_speeches = Speech.objects.filter(
                agenda_item=summary.agenda_item,
                event_type='SPEECH',
                is_incomplete=True
            ).exists()
            
            if summary.is_incomplete != has_incomplete_speeches:
                if self.verbose:
                    status = "incomplete" if has_incomplete_speeches else "complete"
                    self.stdout.write(
                        f"\n   🔧 Summary {summary.id} for agenda {summary.agenda_item.id} → {status}"
                    )
                
                if not self.dry_run:
                    summary.is_incomplete = has_incomplete_speeches
                    summary.save(update_fields=['is_incomplete'])
                
                fixed += 1
                if has_incomplete_speeches:
                    set_true += 1
                else:
                    set_false += 1
        
        # Clear progress line and show final result
        self.stdout.write(' ' * 100, ending='\r')  # Clear the progress line
        self.stdout.write(f"   ✅ Checked {total:,}, fixed {fixed:,}")
        return {'checked': total, 'fixed': fixed, 'set_true': set_true, 'set_false': set_false}

    def fix_agenda_decision_flags(self):
        """Fix is_incomplete flags for AgendaDecision entities"""
        self.stdout.write("\n⚖️  Fixing AgendaDecision flags...")
        
        decisions = AgendaDecision.objects.select_related('agenda_item').all()
        total = decisions.count()
        fixed = 0
        set_true = 0
        set_false = 0
        checked = 0
        
        self.stdout.write(f"   Checking {total:,} agenda decisions...")
        
        # Show progress every N records
        progress_interval = 1000 if total > 10000 else 100
        
        for decision in decisions.iterator(chunk_size=100):
            checked += 1
            
            # Show progress
            if checked % progress_interval == 0:
                percentage = (checked / total) * 100
                self.stdout.write(
                    f"   📊 Progress: {checked:,}/{total:,} ({percentage:.1f}%) - "
                    f"Fixed so far: {fixed:,}",
                    ending='\r'
                )
                self.stdout.flush()
            
            # Decision is incomplete if its agenda has incomplete speeches
            has_incomplete_speeches = Speech.objects.filter(
                agenda_item=decision.agenda_item,
                event_type='SPEECH',
                is_incomplete=True
            ).exists()
            
            if decision.is_incomplete != has_incomplete_speeches:
                if self.verbose:
                    status = "incomplete" if has_incomplete_speeches else "complete"
                    self.stdout.write(
                        f"\n   🔧 Decision {decision.id} for agenda {decision.agenda_item.id} → {status}"
                    )
                
                if not self.dry_run:
                    decision.is_incomplete = has_incomplete_speeches
                    decision.save(update_fields=['is_incomplete'])
                
                fixed += 1
                if has_incomplete_speeches:
                    set_true += 1
                else:
                    set_false += 1
        
        # Clear progress line and show final result
        self.stdout.write(' ' * 100, ending='\r')  # Clear the progress line
        self.stdout.write(f"   ✅ Checked {total:,}, fixed {fixed:,}")
        return {'checked': total, 'fixed': fixed, 'set_true': set_true, 'set_false': set_false}

    def fix_agenda_active_politician_flags(self):
        """Fix is_incomplete flags for AgendaActivePolitician entities"""
        self.stdout.write("\n👤 Fixing AgendaActivePolitician flags...")
        
        active_politicians = AgendaActivePolitician.objects.select_related('agenda_item').all()
        total = active_politicians.count()
        fixed = 0
        set_true = 0
        set_false = 0
        checked = 0
        
        self.stdout.write(f"   Checking {total:,} active politician records...")
        
        # Show progress every N records
        progress_interval = 1000 if total > 10000 else 100
        
        for active in active_politicians.iterator(chunk_size=100):
            checked += 1
            
            # Show progress
            if checked % progress_interval == 0:
                percentage = (checked / total) * 100
                self.stdout.write(
                    f"   📊 Progress: {checked:,}/{total:,} ({percentage:.1f}%) - "
                    f"Fixed so far: {fixed:,}",
                    ending='\r'
                )
                self.stdout.flush()
            
            # Active politician is incomplete if its agenda has incomplete speeches
            has_incomplete_speeches = Speech.objects.filter(
                agenda_item=active.agenda_item,
                event_type='SPEECH',
                is_incomplete=True
            ).exists()
            
            if active.is_incomplete != has_incomplete_speeches:
                if self.verbose:
                    status = "incomplete" if has_incomplete_speeches else "complete"
                    politician_name = active.politician.full_name if active.politician else 'Unknown'
                    self.stdout.write(
                        f"\n   🔧 Active {active.id}: {politician_name} "
                        f"in agenda {active.agenda_item.id} → {status}"
                    )
                
                if not self.dry_run:
                    active.is_incomplete = has_incomplete_speeches
                    active.save(update_fields=['is_incomplete'])
                
                fixed += 1
                if has_incomplete_speeches:
                    set_true += 1
                else:
                    set_false += 1
        
        # Clear progress line and show final result
        self.stdout.write(' ' * 100, ending='\r')  # Clear the progress line
        self.stdout.write(f"   ✅ Checked {total:,}, fixed {fixed:,}")
        return {'checked': total, 'fixed': fixed, 'set_true': set_true, 'set_false': set_false}

    def fix_politician_profile_flags(self):
        """Fix is_incomplete flags for PoliticianProfilePart entities"""
        self.stdout.write("\n🎯 Fixing PoliticianProfilePart flags...")
        
        profiles = PoliticianProfilePart.objects.select_related('politician').all()
        total = profiles.count()
        fixed = 0
        set_true = 0
        set_false = 0
        checked = 0
        
        self.stdout.write(f"   Checking {total:,} politician profiles...")
        
        # Show progress every N records
        progress_interval = 1000 if total > 10000 else 100
        
        for profile in profiles.iterator(chunk_size=100):
            checked += 1
            
            # Show progress
            if checked % progress_interval == 0:
                percentage = (checked / total) * 100
                self.stdout.write(
                    f"   📊 Progress: {checked:,}/{total:,} ({percentage:.1f}%) - "
                    f"Fixed so far: {fixed:,}",
                    ending='\r'
                )
                self.stdout.flush()
            
            # Check if profile has incomplete speeches based on period type
            has_incomplete_speeches = self._check_profile_has_incomplete_speeches(profile)
            
            if profile.is_incomplete != has_incomplete_speeches:
                if self.verbose:
                    status = "incomplete" if has_incomplete_speeches else "complete"
                    period_desc = self._get_profile_period_description(profile)
                    self.stdout.write(
                        f"\n   🔧 Profile {profile.id}: {profile.politician.full_name} - "
                        f"{profile.category} - {period_desc} → {status}"
                    )
                
                if not self.dry_run:
                    profile.is_incomplete = has_incomplete_speeches
                    profile.save(update_fields=['is_incomplete'])
                
                fixed += 1
                if has_incomplete_speeches:
                    set_true += 1
                else:
                    set_false += 1
        
        # Clear progress line and show final result
        self.stdout.write(' ' * 100, ending='\r')  # Clear the progress line
        self.stdout.write(f"   ✅ Checked {total:,}, fixed {fixed:,}")
        return {'checked': total, 'fixed': fixed, 'set_true': set_true, 'set_false': set_false}

    def _check_profile_has_incomplete_speeches(self, profile):
        """Check if a profile's period contains incomplete speeches"""
        base_query = Speech.objects.filter(
            politician=profile.politician,
            event_type='SPEECH',
            is_incomplete=True
        )
        
        if profile.period_type == 'AGENDA' and profile.agenda_item:
            return base_query.filter(agenda_item=profile.agenda_item).exists()
        
        elif profile.period_type == 'PLENARY_SESSION' and profile.plenary_session:
            return base_query.filter(
                agenda_item__plenary_session=profile.plenary_session
            ).exists()
        
        elif profile.period_type == 'MONTH' and profile.month:
            try:
                month_num, year = profile.month.split('.')
                month_num, year = int(month_num), int(year)
                return base_query.filter(
                    date__month=month_num,
                    date__year=year
                ).exists()
            except (ValueError, AttributeError):
                return False
        
        elif profile.period_type == 'YEAR' and profile.year:
            return base_query.filter(date__year=profile.year).exists()
        
        elif profile.period_type == 'ALL':
            return base_query.exists()
        
        return False

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

