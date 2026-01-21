"""
Management command to sync all data: total times, profiling counts, and statistics
"""
from django.core.management import call_command
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = 'Execute all sync commands: sync_total_times, sync_profiling_counts, and sync_stats'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Run all sync commands in dry-run mode without saving data'
        )

    def handle(self, *args, **options):
        dry_run = options.get('dry_run', False)
        
        self.stdout.write(self.style.SUCCESS('=' * 80))
        self.stdout.write(self.style.SUCCESS('🚀 Starting synchronization of all data...'))
        self.stdout.write(self.style.SUCCESS('=' * 80))
        
        if dry_run:
            self.stdout.write(self.style.WARNING('⚠️  DRY RUN MODE - No data will be saved\n'))
        
        # Step 1: Sync total times
        self.stdout.write('\n' + '=' * 80)
        self.stdout.write(self.style.SUCCESS('📝 STEP 1/3: Syncing total times...'))
        self.stdout.write('=' * 80 + '\n')
        try:
            call_command('sync_total_times', dry_run=dry_run)
            self.stdout.write(self.style.SUCCESS('✅ Total times sync completed\n'))
        except Exception as e:
            self.stdout.write(self.style.ERROR(f'❌ Error syncing total times: {str(e)}\n'))
            return
        
        # Step 2: Sync profiling counts
        self.stdout.write('\n' + '=' * 80)
        self.stdout.write(self.style.SUCCESS('📊 STEP 2/3: Syncing profiling counts...'))
        self.stdout.write('=' * 80 + '\n')
        try:
            call_command('sync_profiling_counts', dry_run=dry_run)
            self.stdout.write(self.style.SUCCESS('✅ Profiling counts sync completed\n'))
        except Exception as e:
            self.stdout.write(self.style.ERROR(f'❌ Error syncing profiling counts: {str(e)}\n'))
            return
        
        # Step 3: Sync statistics
        self.stdout.write('\n' + '=' * 80)
        self.stdout.write(self.style.SUCCESS('📈 STEP 3/3: Syncing statistics...'))
        self.stdout.write('=' * 80 + '\n')
        try:
            call_command('sync_stats', dry_run=dry_run)
            self.stdout.write(self.style.SUCCESS('✅ Statistics sync completed\n'))
        except Exception as e:
            self.stdout.write(self.style.ERROR(f'❌ Error syncing statistics: {str(e)}\n'))
            return
        
        # Final summary
        self.stdout.write('\n' + '=' * 80)
        self.stdout.write(self.style.SUCCESS('🎉 ALL SYNCHRONIZATION COMPLETED SUCCESSFULLY!'))
        self.stdout.write('=' * 80)
        
        if dry_run:
            self.stdout.write(self.style.WARNING('\n⚠️  DRY RUN MODE - No changes were saved to the database'))
        else:
            self.stdout.write(self.style.SUCCESS('\n✅ All data has been synchronized and saved to the database'))

