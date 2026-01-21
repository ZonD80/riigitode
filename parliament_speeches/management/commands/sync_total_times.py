"""
Management command to sync total times for existing agenda items and politicians
"""
import logging
from collections import defaultdict
from django.core.management.base import BaseCommand
from django.db import transaction
from parliament_speeches.models import AgendaItem, Politician

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Calculate and sync total times for existing agenda items and politicians"

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Run without saving data to database'
        )
        parser.add_argument(
            '--agenda-only',
            action='store_true',
            help='Only sync agenda item times'
        )
        parser.add_argument(
            '--politicians-only',
            action='store_true',
            help='Only sync politician times'
        )
        parser.add_argument(
            '--verbose',
            action='store_true',
            help='Enable verbose logging'
        )

    def handle(self, *args, **options):
        self.dry_run = options['dry_run']
        self.verbose = options['verbose']
        
        if options['verbose']:
            logger.setLevel(logging.DEBUG)
        
        self.stdout.write(self.style.SUCCESS('Starting total times synchronization...'))
        
        if self.dry_run:
            self.stdout.write(self.style.WARNING('DRY RUN MODE - No data will be saved'))
        
        agenda_updated = 0
        politician_updated = 0
        
        # Sync agenda items
        if not options['politicians_only']:
            self.stdout.write('\n=== Syncing Agenda Item Times ===')
            agenda_updated = self.sync_agenda_times()
        
        # Sync politicians
        if not options['agenda_only']:
            self.stdout.write('\n=== Syncing Politician Times ===')
            politician_updated = self.sync_politician_times()
        
        # Summary
        self.stdout.write('\n=== SUMMARY ===')
        self.stdout.write(f'Agenda items updated: {agenda_updated}')
        self.stdout.write(f'Politicians updated: {politician_updated}')
        
        if self.dry_run:
            self.stdout.write(self.style.WARNING('DRY RUN - No changes were saved'))
        else:
            self.stdout.write(self.style.SUCCESS('Synchronization completed successfully!'))

    def sync_agenda_times(self):
        """Sync total times for all agenda items"""
        agenda_items = AgendaItem.objects.all().prefetch_related('speeches')
        total_count = agenda_items.count()
        updated_count = 0
        
        self.stdout.write(f'Processing {total_count} agenda items...')
        
        for i, agenda_item in enumerate(agenda_items, 1):
            if i % 100 == 0 or self.verbose:
                progress = (i / total_count) * 100
                self.stdout.write(f'Progress: {i}/{total_count} ({progress:.1f}%)')
            
            old_time = agenda_item.total_time_seconds
            new_time = self.calculate_agenda_total_time(agenda_item)
            
            if new_time is not None and new_time != old_time:
                if not self.dry_run:
                    agenda_item.total_time_seconds = new_time
                    agenda_item.save(update_fields=['total_time_seconds'])
                
                updated_count += 1
                
                if self.verbose:
                    minutes = new_time // 60
                    self.stdout.write(f'  Updated agenda {agenda_item.pk}: {old_time} -> {new_time} seconds ({minutes} minutes)')
        
        return updated_count

    def sync_politician_times(self):
        """Sync total times for all politicians"""
        politicians = Politician.objects.all().prefetch_related('speeches__agenda_item')
        total_count = politicians.count()
        updated_count = 0
        
        self.stdout.write(f'Processing {total_count} politicians...')
        
        for i, politician in enumerate(politicians, 1):
            if i % 50 == 0 or self.verbose:
                progress = (i / total_count) * 100
                self.stdout.write(f'Progress: {i}/{total_count} ({progress:.1f}%)')
            
            old_time = politician.total_time_seconds
            new_time = self.calculate_politician_total_time(politician)
            
            if new_time is not None and new_time != old_time:
                if not self.dry_run:
                    politician.total_time_seconds = new_time
                    politician.save(update_fields=['total_time_seconds'])
                
                updated_count += 1
                
                if self.verbose:
                    minutes = new_time // 60
                    self.stdout.write(f'  Updated politician {politician.pk} ({politician.full_name}): {old_time} -> {new_time} seconds ({minutes} minutes)')
        
        return updated_count

    def calculate_agenda_total_time(self, agenda_item):
        """Calculate the total time for an agenda item based on speech intervals"""
        speeches = agenda_item.speeches.filter(event_type='SPEECH').order_by('date')
        
        if speeches.count() < 2:
            # Need at least 2 speeches to calculate time intervals
            logger.debug(f"Agenda item {agenda_item.pk} has less than 2 speeches, cannot calculate duration")
            return None
        
        # Calculate total time from first speech to last speech
        first_speech = speeches.first()
        last_speech = speeches.last()
        
        if first_speech and last_speech:
            duration_seconds = int((last_speech.date - first_speech.date).total_seconds())
            logger.debug(f"Calculated agenda item {agenda_item.pk} total time: {duration_seconds} seconds")
            return duration_seconds
        else:
            logger.warning(f"Could not calculate duration for agenda item {agenda_item.pk}")
            return None

    def calculate_politician_total_time(self, politician):
        """Calculate the total speaking time for a politician"""
        speeches = politician.speeches.filter(event_type='SPEECH').order_by('date')
        
        if not speeches.exists():
            logger.debug(f"Politician {politician.pk} has no speeches")
            return None
        
        # Group speeches by agenda item to calculate speaking time per agenda
        agenda_groups = defaultdict(list)
        
        for speech in speeches:
            agenda_groups[speech.agenda_item_id].append(speech)
        
        total_speaking_seconds = 0
        
        for agenda_id, agenda_speeches in agenda_groups.items():
            if len(agenda_speeches) < 2:
                # Single speech, estimate 30 seconds per speech
                total_speaking_seconds += 30 * len(agenda_speeches)
                continue
            
            # Sort speeches by date
            agenda_speeches.sort(key=lambda x: x.date)
            
            # Calculate intervals between consecutive speeches by this politician
            for i in range(len(agenda_speeches) - 1):
                current_speech = agenda_speeches[i]
                next_speech = agenda_speeches[i + 1]
                
                # Calculate time between speeches (assume this is speaking time)
                interval_seconds = (next_speech.date - current_speech.date).total_seconds()
                
                # Cap individual speech time at 30 minutes to avoid outliers
                if interval_seconds > 1800:  # 30 minutes
                    interval_seconds = 1800
                elif interval_seconds < 10:  # Minimum 10 seconds
                    interval_seconds = 10
                    
                total_speaking_seconds += interval_seconds
            
            # Add time for the last speech (estimate 30 seconds)
            total_speaking_seconds += 30
        
        logger.debug(f"Calculated politician {politician.pk} total speaking time: {int(total_speaking_seconds)} seconds")
        return int(total_speaking_seconds)
