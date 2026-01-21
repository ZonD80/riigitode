"""
Management command to run daily routine: parse speeches, generate summaries, translate, profile politicians, and sync data
"""
import logging
from datetime import datetime
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = '''Run daily routine to process parliament data.
    
    This command executes the following steps in order:
    1. Parse speeches from Estonian Parliament API
    2. Generate AI summaries for speeches
    3. Generate AI summaries for agendas
    4. Translate agendas
    5. Translate plenary session titles
    6. Translate speech AI summaries
    7. Profile all politicians
    8. Translate politician profiles
    9. Sync everything (total times, profiling counts, statistics)
    
    Usage examples:
    - Basic usage: python manage.py daily_routine
    - With custom date: python manage.py daily_routine --start-date 2025-01-01
    - With custom batch size: python manage.py daily_routine --batch-size 100
    - With specific AI provider: python manage.py daily_routine --ai-provider openai
    - Dry run: python manage.py daily_routine --dry-run
    '''

    def add_arguments(self, parser):
        # Get current year for default start date
        current_year = datetime.now().year
        default_start_date = f"{current_year}-01-01"
        
        parser.add_argument(
            '--start-date',
            type=str,
            default=default_start_date,
            help=f'Start date in YYYY-MM-DD format (default: {default_start_date} - first day of current year)'
        )
        parser.add_argument(
            '--batch-size',
            type=int,
            default=1000,
            help='Number of items to process in parallel per batch (default: 1000)'
        )
        parser.add_argument(
            '--ai-provider',
            type=str,
            default='gemini',
            choices=['claude', 'openai', 'ollama', 'gemini'],
            help='AI provider to use for summaries and translations (default: gemini)'
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Run without saving data to database (applies to all commands)'
        )
        parser.add_argument(
            '--skip-parse',
            action='store_true',
            help='Skip parsing speeches (useful if you only want to process existing data)'
        )
        parser.add_argument(
            '--verbose',
            action='store_true',
            help='Enable verbose logging for all commands'
        )

    def handle(self, *args, **options):
        start_date = options['start_date']
        batch_size = options['batch_size']
        ai_provider = options['ai_provider']
        dry_run = options['dry_run']
        skip_parse = options['skip_parse']
        verbose = options['verbose']
        
        # Validate start date format
        try:
            datetime.strptime(start_date, '%Y-%m-%d')
        except ValueError:
            raise CommandError(f"Invalid date format: {start_date}. Use YYYY-MM-DD format.")
        
        # Print header
        self.stdout.write(self.style.SUCCESS('=' * 80))
        self.stdout.write(self.style.SUCCESS('🚀 DAILY ROUTINE - Parliament Data Processing Pipeline (9 Steps)'))
        self.stdout.write(self.style.SUCCESS('=' * 80))
        self.stdout.write(f'📅 Start Date: {start_date}')
        self.stdout.write(f'📦 Batch Size: {batch_size}')
        self.stdout.write(f'🤖 AI Provider: {ai_provider}')
        
        if dry_run:
            self.stdout.write(self.style.WARNING('🔍 DRY RUN MODE - No data will be saved'))
        if skip_parse:
            self.stdout.write(self.style.WARNING('⏭️  SKIPPING PARSE - Will only process existing data'))
        if verbose:
            self.stdout.write('📢 VERBOSE MODE - Detailed logging enabled')
        
        self.stdout.write('')
        
        # Step 1: Parse speeches
        if not skip_parse:
            self.stdout.write('\n' + '=' * 80)
            self.stdout.write(self.style.SUCCESS('📝 STEP 1/9: Parsing speeches from Parliament API...'))
            self.stdout.write('=' * 80 + '\n')
            try:
                call_command(
                    'parse_speeches',
                    start_date=start_date,
                    dry_run=dry_run,
                    verbose=verbose
                )
                self.stdout.write(self.style.SUCCESS('\n✅ Step 1 completed: Speeches parsed\n'))
            except Exception as e:
                self.stdout.write(self.style.ERROR(f'\n❌ Error parsing speeches: {str(e)}\n'))
                logger.exception("Error in parse_speeches")
                return
        else:
            self.stdout.write(self.style.WARNING('\n⏭️  STEP 1/9: SKIPPED - Parsing speeches\n'))
        
        # Step 2: Generate AI summaries for speeches
        self.stdout.write('\n' + '=' * 80)
        self.stdout.write(self.style.SUCCESS('🤖 STEP 2/9: Generating AI summaries for speeches...'))
        self.stdout.write('=' * 80 + '\n')
        try:
            call_command(
                'generate_ai_summaries_for_speeches',
                batch_size=batch_size,
                ai_provider=ai_provider,
                dry_run=dry_run
            )
            self.stdout.write(self.style.SUCCESS('\n✅ Step 2 completed: Speech summaries generated\n'))
        except Exception as e:
            self.stdout.write(self.style.ERROR(f'\n❌ Error generating speech summaries: {str(e)}\n'))
            logger.exception("Error in generate_ai_summaries_for_speeches")
            return
        
        # Step 3: Generate AI summaries for agendas
        self.stdout.write('\n' + '=' * 80)
        self.stdout.write(self.style.SUCCESS('🤖 STEP 3/9: Generating AI summaries for agendas...'))
        self.stdout.write('=' * 80 + '\n')
        try:
            call_command(
                'generate_ai_summaries_for_agendas',
                batch_size=batch_size,
                ai_provider=ai_provider,
                dry_run=dry_run
            )
            self.stdout.write(self.style.SUCCESS('\n✅ Step 3 completed: Agenda summaries generated\n'))
        except Exception as e:
            self.stdout.write(self.style.ERROR(f'\n❌ Error generating agenda summaries: {str(e)}\n'))
            logger.exception("Error in generate_ai_summaries_for_agendas")
            return
        
        # Step 4: Translate agendas
        self.stdout.write('\n' + '=' * 80)
        self.stdout.write(self.style.SUCCESS('🌐 STEP 4/9: Translating agendas...'))
        self.stdout.write('=' * 80 + '\n')
        try:
            call_command(
                'translate_agendas',
                batch_size=batch_size,
                ai_provider=ai_provider,
                target_language='both',
                translate_type='all',
                dry_run=dry_run,
                verbose=verbose
            )
            self.stdout.write(self.style.SUCCESS('\n✅ Step 4 completed: Agendas translated\n'))
        except Exception as e:
            self.stdout.write(self.style.ERROR(f'\n❌ Error translating agendas: {str(e)}\n'))
            logger.exception("Error in translate_agendas")
            return
        
        # Step 5: Translate plenary session titles
        self.stdout.write('\n' + '=' * 80)
        self.stdout.write(self.style.SUCCESS('🌐 STEP 5/9: Translating plenary session titles...'))
        self.stdout.write('=' * 80 + '\n')
        try:
            call_command(
                'translate_plenary_session_titles',
                batch_size=batch_size,
                ai_provider=ai_provider,
                target_language='both',
                dry_run=dry_run,
                verbose=verbose
            )
            self.stdout.write(self.style.SUCCESS('\n✅ Step 5 completed: Plenary session titles translated\n'))
        except Exception as e:
            self.stdout.write(self.style.ERROR(f'\n❌ Error translating plenary session titles: {str(e)}\n'))
            logger.exception("Error in translate_plenary_session_titles")
            return
        
        # Step 6: Translate speech AI summaries
        self.stdout.write('\n' + '=' * 80)
        self.stdout.write(self.style.SUCCESS('🌐 STEP 6/9: Translating speech AI summaries...'))
        self.stdout.write('=' * 80 + '\n')
        try:
            call_command(
                'translate_speech_ai_summaries',
                batch_size=batch_size,
                ai_provider=ai_provider,
                target_language='both',
                dry_run=dry_run,
                verbose=verbose
            )
            self.stdout.write(self.style.SUCCESS('\n✅ Step 6 completed: Speech summaries translated\n'))
        except Exception as e:
            self.stdout.write(self.style.ERROR(f'\n❌ Error translating speech summaries: {str(e)}\n'))
            logger.exception("Error in translate_speech_ai_summaries")
            return
        
        # Step 7: Profile all politicians
        self.stdout.write('\n' + '=' * 80)
        self.stdout.write(self.style.SUCCESS('👤 STEP 7/9: Profiling all politicians...'))
        self.stdout.write('=' * 80 + '\n')
        try:
            call_command(
                'profile_all_politicians',
                batch_size=batch_size,
                ai_provider=ai_provider,
                dry_run=dry_run
            )
            self.stdout.write(self.style.SUCCESS('\n✅ Step 7 completed: Politicians profiled\n'))
        except Exception as e:
            self.stdout.write(self.style.ERROR(f'\n❌ Error profiling politicians: {str(e)}\n'))
            logger.exception("Error in profile_all_politicians")
            return
        
        # Step 8: Translate politician profiles
        self.stdout.write('\n' + '=' * 80)
        self.stdout.write(self.style.SUCCESS('🌐 STEP 8/9: Translating politician profiles...'))
        self.stdout.write('=' * 80 + '\n')
        try:
            call_command(
                'translate_politician_profiles',
                batch_size=batch_size,
                ai_provider=ai_provider,
                target_language='both',
                dry_run=dry_run,
                verbose=verbose
            )
            self.stdout.write(self.style.SUCCESS('\n✅ Step 8 completed: Politician profiles translated\n'))
        except Exception as e:
            self.stdout.write(self.style.ERROR(f'\n❌ Error translating politician profiles: {str(e)}\n'))
            logger.exception("Error in translate_politician_profiles")
            return
        
        # Step 9: Sync everything
        self.stdout.write('\n' + '=' * 80)
        self.stdout.write(self.style.SUCCESS('🔄 STEP 9/9: Syncing all data...'))
        self.stdout.write('=' * 80 + '\n')
        try:
            call_command(
                'sync_everything',
                dry_run=dry_run
            )
            self.stdout.write(self.style.SUCCESS('\n✅ Step 9 completed: All data synced\n'))
        except Exception as e:
            self.stdout.write(self.style.ERROR(f'\n❌ Error syncing data: {str(e)}\n'))
            logger.exception("Error in sync_everything")
            return
        
        # Final summary
        self.stdout.write('\n' + '=' * 80)
        self.stdout.write(self.style.SUCCESS('🎉 DAILY ROUTINE COMPLETED SUCCESSFULLY!'))
        self.stdout.write(self.style.SUCCESS('=' * 80))
        self.stdout.write('\n📊 Summary:')
        self.stdout.write('  ✅ Step 1: Speeches parsed' if not skip_parse else '  ⏭️  Step 1: Parsing skipped')
        self.stdout.write('  ✅ Step 2: Speech summaries generated')
        self.stdout.write('  ✅ Step 3: Agenda summaries generated')
        self.stdout.write('  ✅ Step 4: Agendas translated')
        self.stdout.write('  ✅ Step 5: Plenary session titles translated')
        self.stdout.write('  ✅ Step 6: Speech summaries translated')
        self.stdout.write('  ✅ Step 7: Politicians profiled')
        self.stdout.write('  ✅ Step 8: Politician profiles translated')
        self.stdout.write('  ✅ Step 9: All data synced')
        
        if dry_run:
            self.stdout.write(self.style.WARNING('\n🔍 DRY RUN MODE - No changes were saved to the database'))
        else:
            self.stdout.write(self.style.SUCCESS('\n✅ All changes have been saved to the database'))
        
        self.stdout.write('\n' + '=' * 80 + '\n')

