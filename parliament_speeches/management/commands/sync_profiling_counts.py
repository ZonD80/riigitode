"""
Management command to sync profiling counts for politicians
"""
from django.core.management.base import BaseCommand
from django.db.models import Count, Q
from parliament_speeches.models import (
    Politician, Speech, PoliticianProfilePart
)


class Command(BaseCommand):
    help = 'Sync profiling counts for all politicians based on their speeches and existing profiles'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show what would be updated without making changes'
        )
        parser.add_argument(
            '--politician-id',
            type=int,
            help='Update only specific politician by ID'
        )

    def handle(self, *args, **options):
        dry_run = options.get('dry_run', False)
        politician_id = options.get('politician_id')
        
        if dry_run:
            self.stdout.write(self.style.WARNING("🔍 DRY RUN MODE - No counts will be updated"))
        
        self.stdout.write("📊 Syncing profiling counts...")
        
        # Filter politicians if specific ID provided
        if politician_id:
            politicians = Politician.objects.filter(id=politician_id, active=True)
            if not politicians.exists():
                self.stdout.write(self.style.ERROR(f"Politician with ID {politician_id} not found or inactive"))
                return
        else:
            # Get active politicians who have speeches
            politicians = Politician.objects.filter(
                active=True,
                speeches__event_type='SPEECH'
            ).distinct()
        
        updated_count = 0
        
        for politician in politicians:
            # Calculate required profiles using the same logic as gather_stats.py
            profiles_required = self._calculate_required_profiles(politician)
            
            # Count existing profiles
            profiles_already_profiled = PoliticianProfilePart.objects.filter(
                politician=politician
            ).count()
            
            # Check if update is needed
            if (politician.profiles_required != profiles_required or 
                politician.profiles_already_profiled != profiles_already_profiled):
                
                if not dry_run:
                    politician.profiles_required = profiles_required
                    politician.profiles_already_profiled = profiles_already_profiled
                    politician.save(update_fields=['profiles_required', 'profiles_already_profiled'])
                
                percentage = round((profiles_already_profiled / profiles_required * 100), 1) if profiles_required > 0 else 0
                
                action = "Updated" if not dry_run else "Would update"
                self.stdout.write(
                    f"✅ {action}: {politician.full_name} - "
                    f"Required: {profiles_required}, "
                    f"Profiled: {profiles_already_profiled} "
                    f"({percentage}%)"
                )
                updated_count += 1
            else:
                self.stdout.write(
                    f"⏩ Skipped: {politician.full_name} - already up to date"
                )
        
        action_word = "Updated" if not dry_run else "Would update"
        self.stdout.write(
            self.style.SUCCESS(f"📊 Profiling counts sync completed! {action_word} {updated_count} politicians")
        )

    def _calculate_required_profiles(self, politician):
        """
        Calculate total required profiles for a politician using the same logic as gather_stats.py
        """
        # Get speeches for this politician
        speeches = Speech.objects.filter(
            politician=politician,
            event_type='SPEECH'
        ).select_related('agenda_item__plenary_session')
        
        if not speeches.exists():
            return 0
        
        # Collect unique periods (same as gather_stats.py logic)
        agenda_ids = set()
        plenary_ids = set()
        months = set()
        years = set()
        
        for speech in speeches:
            agenda_ids.add(speech.agenda_item.id)
            plenary_ids.add(speech.agenda_item.plenary_session.id)
            months.add(f"{speech.date.month:02d}.{speech.date.year}")
            years.add(speech.date.year)
        
        # Based on profile_politician.py, there are 10 profile categories
        profile_categories_count = len(PoliticianProfilePart.PROFILE_CATEGORIES)
        
        # Calculate profiles needed for this politician
        # For each category: agenda profiles + plenary profiles + month profiles + year profiles + 1 ALL profile
        profiles_per_category = len(agenda_ids) + len(plenary_ids) + len(months) + len(years) + 1
        total_required = profiles_per_category * profile_categories_count
        
        return total_required
