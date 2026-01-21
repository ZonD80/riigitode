"""
Management command to profile all politicians with speeches
"""
from django.core.management.base import BaseCommand
from django.core.management import call_command
from django.db.models import Count
from concurrent.futures import ThreadPoolExecutor, as_completed
from parliament_speeches.models import Politician


class Command(BaseCommand):
    help = '''Profile all politicians with speeches using the profile_politician command.
    
    This command will iterate through all politicians who have at least one speech
    and run the profile_politician command for each one.
    
    Usage examples:
    - Profile all politicians: python manage.py profile_all_politicians
    - With specific AI provider: python manage.py profile_all_politicians --ai-provider gemini
    - With custom batch size: python manage.py profile_all_politicians --batch-size 50
    - Parallel processing: python manage.py profile_all_politicians --max-workers 5
    - Dry run to see what would be done: python manage.py profile_all_politicians --dry-run
    - Overwrite existing profiles: python manage.py profile_all_politicians --overwrite
    - Start from specific politician ID: python manage.py profile_all_politicians --start-from-id 10
    '''

    def add_arguments(self, parser):
        parser.add_argument(
            '--ai-provider',
            type=str,
            default='gemini',
            choices=['claude', 'openai', 'ollama', 'gemini'],
            help='AI provider to use (default: gemini)'
        )
        parser.add_argument(
            '--batch-size',
            type=int,
            default=50,
            help='Number of periods to process in parallel (default: 50)'
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
            '--start-from-id',
            type=int,
            help='Start from a specific politician ID (useful for resuming)'
        )
        parser.add_argument(
            '--limit',
            type=int,
            help='Limit the number of politicians to process'
        )
        parser.add_argument(
            '--max-workers',
            type=int,
            default=5,
            help='Number of parallel workers (default: 5)'
        )

    def _profile_politician(self, politician, ai_provider, batch_size, overwrite, dry_run):
        """Helper method to profile a single politician"""
        try:
            # Prepare command options
            command_options = {
                'id': politician.id,
                'batch_size': batch_size,
                'ai_provider': ai_provider,
                'dry_run': dry_run,
            }
            
            # Add overwrite flag if specified
            if overwrite:
                command_options['overwrite'] = True
            
            # Call the profile_politician command
            call_command('profile_politician', **command_options)
            
            return {'success': True, 'politician': politician}
            
        except Exception as e:
            return {'success': False, 'politician': politician, 'error': str(e)}

    def handle(self, *args, **options):
        ai_provider = options['ai_provider']
        batch_size = options['batch_size']
        overwrite = options['overwrite']
        dry_run = options['dry_run']
        start_from_id = options.get('start_from_id')
        limit = options.get('limit')
        max_workers = options['max_workers']

        self.stdout.write(self.style.SUCCESS("=" * 80))
        self.stdout.write(self.style.SUCCESS("🚀 Starting Politician Profiling for All Politicians"))
        self.stdout.write(self.style.SUCCESS("=" * 80))
        
        if dry_run:
            self.stdout.write(self.style.WARNING("🔍 DRY RUN MODE - No profiles will be saved"))
        
        # Get all politicians with speeches, ordered by ID
        politicians_query = Politician.objects.annotate(
            speech_count=Count('speeches')
        ).filter(speech_count__gt=0).order_by('id')
        
        # Apply start-from-id filter if provided
        if start_from_id:
            politicians_query = politicians_query.filter(id__gte=start_from_id)
            self.stdout.write(self.style.WARNING(f"📍 Starting from politician ID: {start_from_id}"))
        
        # Apply limit if provided
        if limit:
            politicians_query = politicians_query[:limit]
            self.stdout.write(self.style.WARNING(f"⚠️  Limited to {limit} politicians"))
        
        politicians = list(politicians_query)
        total_politicians = len(politicians)
        
        self.stdout.write(self.style.SUCCESS(f"\n📊 Found {total_politicians} politicians with speeches"))
        self.stdout.write(f"⚙️  AI Provider: {ai_provider}")
        self.stdout.write(f"⚙️  Batch Size: {batch_size}")
        self.stdout.write(f"⚙️  Max Workers: {max_workers}")
        self.stdout.write(f"⚙️  Overwrite: {overwrite}")
        self.stdout.write("")
        
        # Process politicians in parallel
        success_count = 0
        error_count = 0
        completed_count = 0
        
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            # Submit all tasks
            future_to_politician = {
                executor.submit(
                    self._profile_politician,
                    politician,
                    ai_provider,
                    batch_size,
                    overwrite,
                    dry_run
                ): politician for politician in politicians
            }
            
            # Process completed tasks as they finish
            for future in as_completed(future_to_politician):
                politician = future_to_politician[future]
                completed_count += 1
                
                try:
                    result = future.result()
                    if result['success']:
                        success_count += 1
                        self.stdout.write(self.style.SUCCESS(
                            f"✅ [{completed_count}/{total_politicians}] Successfully profiled {politician.full_name} (ID: {politician.id})"
                        ))
                    else:
                        error_count += 1
                        self.stdout.write(self.style.ERROR(
                            f"❌ [{completed_count}/{total_politicians}] Failed to profile {politician.full_name} (ID: {politician.id}): {result['error']}"
                        ))
                except Exception as e:
                    error_count += 1
                    self.stdout.write(self.style.ERROR(
                        f"❌ [{completed_count}/{total_politicians}] Unexpected error for {politician.full_name} (ID: {politician.id}): {str(e)}"
                    ))
        
        # Final summary
        self.stdout.write("\n" + "=" * 80)
        self.stdout.write(self.style.SUCCESS("📊 PROFILING COMPLETE - SUMMARY"))
        self.stdout.write("=" * 80)
        self.stdout.write(f"Total Politicians: {total_politicians}")
        self.stdout.write(self.style.SUCCESS(f"✅ Successfully Profiled: {success_count}"))
        
        if error_count > 0:
            self.stdout.write(self.style.ERROR(f"❌ Errors: {error_count}"))
        
        if dry_run:
            self.stdout.write(self.style.WARNING("\n🔍 This was a DRY RUN - No profiles were saved"))
        
        self.stdout.write(self.style.SUCCESS("\n🎉 All done!"))
        self.stdout.write("=" * 80 + "\n")
