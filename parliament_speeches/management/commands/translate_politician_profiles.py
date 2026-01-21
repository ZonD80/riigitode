"""
Management command to translate politician profile analyses using AI providers

Supports:
- OpenAI (gpt-4o-mini)
- Google Gemini (gemini-2.5-flash-lite-preview-09-2025)
- Ollama (local models)

Batch API Support:
- Use --use-batch-api flag with --ai-provider=gemini to enable Gemini Batch API
- Provides 50% cost reduction compared to standard API
- Processes translations asynchronously in batch jobs
- Use --batch-size=1000 to control items per batch (avoids rate limits)
- Polls for completion and automatically updates database with results

Resume Feature:
- Use --resume-from-batch-id=batches/xxx to resume from an existing batch job
- Useful if script crashes during polling or downloading results
- The batch ID is displayed when the batch job is created
- Skips file creation/upload and jumps directly to polling for results
"""
import time
import logging
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed
from django.core.management.base import BaseCommand, CommandError
from django.conf import settings
from django.db import models

from parliament_speeches.models import PoliticianProfilePart, Politician

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = 'Translate politician profile analyses to English and Russian using AI providers (OpenAI, Gemini, Ollama)'

    def add_arguments(self, parser):
        parser.add_argument(
            '--limit',
            type=int,
            default=None,
            help='Number of profiles to process (default: all eligible profiles)'
        )
        parser.add_argument(
            '--profile-id',
            type=int,
            help='Process specific profile by ID'
        )
        parser.add_argument(
            '--politician-id',
            type=int,
            help='Process all profiles for specific politician by ID'
        )
        parser.add_argument(
            '--period-type',
            choices=['AGENDA', 'PLENARY_SESSION', 'MONTH', 'YEAR', 'ALL'],
            help='Filter by period type'
        )
        parser.add_argument(
            '--category',
            choices=[
                'POLITICAL_POSITION', 'TOPIC_EXPERTISE', 'RHETORICAL_STYLE',
                'ACTIVITY_PATTERNS', 'OPPOSITION_STANCE', 'COLLABORATION_STYLE',
                'REGIONAL_FOCUS', 'ECONOMIC_VIEWS', 'SOCIAL_ISSUES', 'LEGISLATIVE_FOCUS'
            ],
            help='Filter by profile category'
        )
        parser.add_argument(
            '--target-language',
            choices=['en', 'ru', 'both'],
            default='both',
            help='Target language for translation (default: both)'
        )
        parser.add_argument(
            '--overwrite',
            action='store_true',
            help='Overwrite existing translations'
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Run without saving translations to database'
        )
        parser.add_argument(
            '--delay',
            type=float,
            default=1.0,
            help='Delay between API calls in seconds (default: 1.0)'
        )
        parser.add_argument(
            '--batch-size',
            type=int,
            default=5,
            help='Number of profiles to process in parallel (default: 5). When using --use-batch-api, this limits items per batch job to avoid rate limits (recommended: 1000)'
        )
        parser.add_argument(
            '--ai-provider',
            type=str,
            choices=['ollama', 'openai', 'gemini'],
            default='gemini',
            help='AI provider to use for translation (ollama, openai, gemini). Default: gemini.'
        )
        parser.add_argument(
            '--use-batch-api',
            type=lambda x: x.lower() in ['true', '1', 'yes'],
            default=None,
            help='Use Gemini Batch API for cost-effective batch processing (50%% cost reduction, only works with gemini provider). Defaults to True for gemini provider.'
        )
        parser.add_argument(
            '--resume-from-batch-id',
            type=str,
            help='Resume from an existing Gemini batch job (e.g., "batches/abc123"). Skips file upload and job creation, only polls for results.'
        )
        parser.add_argument(
            '--verbose',
            action='store_true',
            help='Show detailed progress and streaming translation results'
        )

    def handle(self, *args, **options):
        self.dry_run = options['dry_run']
        self.delay = options['delay']
        self.batch_size = options['batch_size']
        self.target_language = options['target_language']
        self.overwrite = options['overwrite']
        self.ai_provider = options['ai_provider']
        self.resume_from_batch_id = options['resume_from_batch_id']
        self.verbose = options['verbose']
        
        # Set smart default for use_batch_api based on provider
        if options['use_batch_api'] is None:
            # Default to True for gemini, False for other providers
            self.use_batch_api = (self.ai_provider == 'gemini')
        else:
            self.use_batch_api = options['use_batch_api']
        
        if self.dry_run:
            self.stdout.write(self.style.WARNING("DRY RUN MODE - No translations will be saved"))

        # Validate batch API usage
        if self.use_batch_api and self.ai_provider != 'gemini':
            raise CommandError("--use-batch-api flag is only supported with --ai-provider=gemini")
        
        # Validate resume batch ID usage
        if self.resume_from_batch_id and self.ai_provider != 'gemini':
            raise CommandError("--resume-from-batch-id flag is only supported with --ai-provider=gemini")
        
        # If resuming, force use_batch_api mode
        if self.resume_from_batch_id:
            self.use_batch_api = True

        # Display AI provider being used
        if self.ai_provider == 'ollama':
            ollama_url = getattr(settings, 'OLLAMA_BASE_URL', 'http://localhost:11434')
            ollama_model = getattr(settings, 'OLLAMA_MODEL', 'gemma3:12b')
            self.stdout.write(f"Using Ollama for translations ({ollama_model} at {ollama_url})")
        elif self.ai_provider == 'openai':
            self.stdout.write("Using OpenAI for translations")
        elif self.ai_provider == 'gemini':
            if self.resume_from_batch_id:
                self.stdout.write(f"RESUMING Google Gemini BATCH API job: {self.resume_from_batch_id}")
            elif self.use_batch_api:
                self.stdout.write("Using Google Gemini BATCH API for translations (50% cost reduction)")
                if self.batch_size < 100:
                    self.stdout.write(self.style.WARNING(f"⚠️  Batch size is {self.batch_size}, which is quite small for batch API. Consider using --batch-size=1000"))
            else:
                self.stdout.write("Using Google Gemini for translations")

        try:
            # If resuming from batch ID, handle it separately and exit
            if self.resume_from_batch_id:
                self.resume_batch_job_only(self.resume_from_batch_id)
                self.stdout.write(self.style.SUCCESS("Successfully completed batch resume"))
            elif options['profile_id']:
                # Process specific profile
                self.process_specific_profile(options['profile_id'])
                self.stdout.write(self.style.SUCCESS("Successfully completed profile translation"))
            elif options['politician_id']:
                # Process all profiles for specific politician
                self.process_politician_profiles(options['politician_id'], options['period_type'], options['category'])
                self.stdout.write(self.style.SUCCESS("Successfully completed profile translation"))
            else:
                # Process multiple profiles
                self.process_profiles(options['limit'], options['period_type'], options['category'])
                self.stdout.write(self.style.SUCCESS("Successfully completed profile translation"))
            
        except Exception as e:
            logger.exception("Error during profile translation")
            raise CommandError(f"Error during processing: {str(e)}")

    def resume_batch_job_only(self, batch_job_id):
        """Resume a specific batch job without processing any other items"""
        self.stdout.write("=" * 80)
        self.stdout.write(self.style.HTTP_INFO(f"RESUMING BATCH JOB: {batch_job_id}"))
        self.stdout.write("=" * 80)
        
        start_time = time.time()
        
        try:
            # Step 1: Poll for completion
            self.stdout.write("\nStep 1: Waiting for batch job to complete...")
            result_file_uri = self._poll_batch_job(batch_job_id)
            
            if not result_file_uri:
                raise CommandError("Batch job failed or timed out")
            
            # Step 2: Download and process results
            self.stdout.write("\nStep 2: Downloading and processing results...")
            results = self._download_batch_results(result_file_uri, batch_job_id)
            
            # Step 3: Update database with results
            self.stdout.write("\nStep 3: Updating database with translations...")
            processed, errors = self._update_items_from_batch_results(results)
            
            # Summary
            total_time = time.time() - start_time
            self.stdout.write("\n" + "=" * 80)
            self.stdout.write(self.style.SUCCESS("BATCH RESUME COMPLETE"))
            self.stdout.write(f"Batch job ID: {batch_job_id}")
            self.stdout.write(f"Total time: {total_time/60:.1f} minutes ({total_time:.1f} seconds)")
            self.stdout.write(f"Successfully processed: {processed} items")
            if errors > 0:
                self.stdout.write(self.style.ERROR(f"Errors encountered: {errors}"))
            else:
                self.stdout.write(self.style.SUCCESS("No errors encountered!"))
            
            if self.dry_run:
                self.stdout.write(self.style.WARNING("Note: This was a dry run - no translations were saved to database"))
            
            self.stdout.write("=" * 80)
                
        except Exception as e:
            logger.exception("Error resuming batch job")
            raise CommandError(f"Error resuming batch job: {str(e)}")
    
    def process_specific_profile(self, profile_id):
        """Process a specific profile by ID"""
        try:
            profile = PoliticianProfilePart.objects.select_related('politician').get(pk=profile_id)
        except PoliticianProfilePart.DoesNotExist:
            raise CommandError(f"Profile with ID {profile_id} not found")

        if not profile.analysis:
            raise CommandError(f"Profile {profile_id} does not have an analysis to translate")

        self.stdout.write(f"Processing profile {profile_id}: {profile}")
        success = self.translate_profile(profile)
        
        if success:
            self.stdout.write(self.style.SUCCESS(f"Successfully translated profile {profile_id}"))
        else:
            self.stdout.write(self.style.ERROR(f"Failed to translate profile {profile_id}"))

    def process_politician_profiles(self, politician_id, period_type, category):
        """Process all profiles for a specific politician"""
        try:
            politician = Politician.objects.get(pk=politician_id)
        except Politician.DoesNotExist:
            raise CommandError(f"Politician with ID {politician_id} not found")

        # Get all profiles for this politician
        queryset = PoliticianProfilePart.objects.filter(politician=politician)
        
        # Apply filters
        if period_type:
            queryset = queryset.filter(period_type=period_type)
        if category:
            queryset = queryset.filter(category=category)
        
        # Filter based on analysis existence
        queryset = queryset.filter(analysis__isnull=False).exclude(analysis='')
        
        if not self.overwrite:
            # Filter out items that already have the requested translations
            if self.target_language == 'en':
                queryset = queryset.filter(analysis_en__isnull=True)
            elif self.target_language == 'ru':
                queryset = queryset.filter(analysis_ru__isnull=True)
            else:  # both
                queryset = queryset.filter(
                    models.Q(analysis_en__isnull=True) | models.Q(analysis_ru__isnull=True)
                )
        
        profiles = queryset.order_by('-created_at')
        
        if not profiles.exists():
            self.stdout.write(f"No profiles found for politician {politician_id} that need translation")
            return

        total_count = profiles.count()
        self.stdout.write(f"Processing politician: {politician.full_name}")
        self.stdout.write(f"Found {total_count} profiles to translate")
        self.stdout.write("=" * 60)
        
        self._process_items_in_batches(list(profiles), "profiles", self.translate_profile)

    def process_profiles(self, limit, period_type, category):
        """Process multiple profiles"""
        # Define hierarchical order: ALL -> YEAR -> MONTH -> PLENARY_SESSION -> AGENDA
        if period_type:
            # If specific period type is provided, only process that type
            period_types = [period_type]
        else:
            # Process in hierarchical order
            period_types = ['ALL', 'YEAR', 'MONTH', 'PLENARY_SESSION', 'AGENDA']
        
        total_processed = 0
        overall_start_time = time.time()
        
        for p_type in period_types:
            self.stdout.write("\n" + "=" * 80)
            self.stdout.write(self.style.HTTP_INFO(f"PROCESSING PERIOD TYPE: {p_type}"))
            self.stdout.write("=" * 80)
            
            # Build the base queryset
            queryset = PoliticianProfilePart.objects.filter(
                analysis__isnull=False,
                period_type=p_type
            ).exclude(analysis='').select_related('politician', 'agenda_item', 'plenary_session')
            
            # Apply category filter
            if category:
                queryset = queryset.filter(category=category)
            
            if not self.overwrite:
                # Filter out items that already have the requested translations
                if self.target_language == 'en':
                    queryset = queryset.filter(analysis_en__isnull=True)
                elif self.target_language == 'ru':
                    queryset = queryset.filter(analysis_ru__isnull=True)
                else:  # both
                    queryset = queryset.filter(
                        models.Q(analysis_en__isnull=True) | models.Q(analysis_ru__isnull=True)
                    )
            
            # Order by date to process newer profiles first
            profiles = queryset.order_by('-created_at')
            
            # Apply limit only if processing a single period type
            if limit is not None and len(period_types) == 1:
                profiles = profiles[:limit]
            
            if not profiles.exists():
                self.stdout.write(f"No {p_type} profiles found that need translation")
                continue

            total_count = profiles.count()
            self.stdout.write(f"Found {total_count} {p_type} profiles to translate")
            if category:
                self.stdout.write(f"Category filter: {category}")
            self.stdout.write("-" * 80)
            
            self._process_items_in_batches(list(profiles), f"{p_type} profiles", self.translate_profile)
            total_processed += total_count
        
        # Overall summary
        if len(period_types) > 1:
            overall_time = time.time() - overall_start_time
            self.stdout.write("\n" + "=" * 80)
            self.stdout.write(self.style.SUCCESS("ALL PERIOD TYPES COMPLETE"))
            self.stdout.write(f"Total profiles processed: {total_processed}")
            self.stdout.write(f"Total time: {overall_time/60:.1f} minutes ({overall_time:.1f} seconds)")
            self.stdout.write("=" * 80)

    def _process_items_in_batches(self, items_list, item_type, translate_func):
        """Generic method to process items in batches"""
        # If using Gemini Batch API, process items in chunks to avoid rate limits
        if self.use_batch_api and self.ai_provider == 'gemini':
            return self._process_items_with_batch_api_chunked(items_list, item_type)
        
        # Otherwise use parallel processing
        processed = 0
        errors = 0
        start_time = time.time()
        
        total_batches = (len(items_list) + self.batch_size - 1) // self.batch_size
        
        for batch_num in range(total_batches):
            batch_start = batch_num * self.batch_size
            batch_end = min(batch_start + self.batch_size, len(items_list))
            batch_items = items_list[batch_start:batch_end]
            
            self.stdout.write(f"\n{'='*20} BATCH {batch_num + 1}/{total_batches} {'='*20}")
            self.stdout.write(f"Processing {len(batch_items)} {item_type} in parallel...")
            
            batch_processed, batch_errors = self._process_batch(
                batch_items, batch_start, len(items_list), start_time, translate_func
            )
            
            processed += batch_processed
            errors += batch_errors

        # Final summary with timing
        total_time = time.time() - start_time
        avg_time_per_item = total_time / len(items_list) if items_list else 0
        
        self.stdout.write("\n" + "=" * 60)
        self.stdout.write(self.style.SUCCESS("PROCESSING COMPLETE"))
        self.stdout.write(f"Total time: {total_time/60:.1f} minutes ({total_time:.1f} seconds)")
        self.stdout.write(f"Average time per {item_type[:-1]}: {avg_time_per_item:.1f} seconds")
        self.stdout.write(f"Batch size: {self.batch_size} parallel requests")
        self.stdout.write(f"Successfully processed: {processed}/{len(items_list)} {item_type}")
        if errors > 0:
            self.stdout.write(self.style.ERROR(f"Errors encountered: {errors}"))
        else:
            self.stdout.write(self.style.SUCCESS("No errors encountered!"))
        
        if self.dry_run:
            self.stdout.write(self.style.WARNING("Note: This was a dry run - no translations were saved to database"))
    
    def _process_batch(self, batch_items, batch_start_idx, total_count, overall_start_time, translate_func):
        """Process a batch of items in parallel"""
        processed = 0
        errors = 0
        batch_start_time = time.time()
        
        # Create futures for parallel processing
        with ThreadPoolExecutor(max_workers=self.batch_size) as executor:
            # Submit all items in the batch
            future_to_item = {
                executor.submit(self._process_single_item, item, idx + batch_start_idx + 1, total_count, overall_start_time, translate_func): item 
                for idx, item in enumerate(batch_items)
            }
            
            # Process completed futures
            for future in as_completed(future_to_item):
                item = future_to_item[future]
                try:
                    success = future.result()
                    if success:
                        processed += 1
                    else:
                        errors += 1
                except Exception as e:
                    errors += 1
                    logger.exception(f"Error in parallel processing for item {item.pk}")
                    self.stdout.write(self.style.ERROR(f"✗ Error processing item {item.pk}: {str(e)}"))
        
        batch_time = time.time() - batch_start_time
        self.stdout.write(f"Batch completed in {batch_time:.1f}s - {processed} successful, {errors} errors")
        
        return processed, errors
    
    def _process_single_item(self, item, current_idx, total_count, overall_start_time, translate_func):
        """Process a single item (for parallel execution)"""
        try:
            # Progress information
            current_time = time.time()
            elapsed_time = current_time - overall_start_time
            progress_percent = (current_idx / total_count) * 100
            
            # Calculate ETA
            if current_idx > 1:
                avg_time_per_item = elapsed_time / (current_idx - 1)
                remaining_items = total_count - current_idx + 1
                eta_seconds = avg_time_per_item * remaining_items
                eta_minutes = eta_seconds / 60
                eta_display = f"{eta_minutes:.1f}m" if eta_minutes >= 1 else f"{eta_seconds:.0f}s"
            else:
                eta_display = "calculating..."
            
            self.stdout.write(f"[{current_idx}/{total_count}] ({progress_percent:.1f}%) ETA: {eta_display}")
            self.stdout.write(f"Processing: {item} - ID: {item.pk}")
            
            if self.verbose:
                # Show what will be translated
                translate_info = []
                if self.target_language in ['en', 'both'] and (not item.analysis_en or self.overwrite):
                    translate_info.append("analysis→EN")
                if self.target_language in ['ru', 'both'] and (not item.analysis_ru or self.overwrite):
                    translate_info.append("analysis→RU")
                
                translation_method = self.ai_provider.upper()
                if translate_info:
                    self.stdout.write(f"   └─ {translation_method} | Tasks: {', '.join(translate_info)}")
                else:
                    self.stdout.write(f"   └─ ⚠️  No translations needed (already exists)")
            
            item_start_time = time.time()
            success = translate_func(item)
            item_duration = time.time() - item_start_time
            
            if success:
                self.stdout.write(self.style.SUCCESS(f"✓ Translated item ({item_duration:.1f}s)"))
                return True
            else:
                self.stdout.write(self.style.ERROR(f"✗ Failed to translate item ({item_duration:.1f}s)"))
                return False
                
        except Exception as e:
            logger.exception(f"Error processing item {item.pk}")
            self.stdout.write(self.style.ERROR(f"✗ Error processing item {item.pk}: {str(e)}"))
            return False

    def translate_profile(self, profile):
        """Translate analysis for a single profile"""
        if not profile.analysis:
            self.stdout.write(f"Skipping profile {profile.pk} - no analysis to translate")
            return False

        try:
            translations_made = False
            
            # For OpenAI and Gemini, translate both at once if target is 'both'
            if self.target_language == 'both' and self.ai_provider in ['openai', 'gemini']:
                needs_en = not profile.analysis_en or self.overwrite
                needs_ru = not profile.analysis_ru or self.overwrite
                
                if needs_en or needs_ru:
                    translations = self.call_ai_translation(profile.analysis, 'both')
                    if translations:
                        if needs_en and 'en' in translations:
                            if not self.dry_run:
                                profile.analysis_en = translations['en']
                                translations_made = True
                            else:
                                self.stdout.write(f"English translation (DRY RUN): {translations['en'][:100]}...")
                                translations_made = True
                        if needs_ru and 'ru' in translations:
                            if not self.dry_run:
                                profile.analysis_ru = translations['ru']
                                translations_made = True
                            else:
                                self.stdout.write(f"Russian translation (DRY RUN): {translations['ru'][:100]}...")
                                translations_made = True
            else:
                # Fall back to separate translations for local service or single language
                # Translate to English if needed
                if self.target_language in ['en', 'both']:
                    if not profile.analysis_en or self.overwrite:
                        en_translation = self.call_ai_translation(profile.analysis, 'en')
                        if en_translation and not self.dry_run:
                            profile.analysis_en = en_translation
                            translations_made = True
                        elif en_translation and self.dry_run:
                            self.stdout.write(f"English translation (DRY RUN): {en_translation[:100]}...")
                            translations_made = True
                
                # Translate to Russian if needed
                if self.target_language in ['ru', 'both']:
                    if not profile.analysis_ru or self.overwrite:
                        ru_translation = self.call_ai_translation(profile.analysis, 'ru')
                        if ru_translation and not self.dry_run:
                            profile.analysis_ru = ru_translation
                            translations_made = True
                        elif ru_translation and self.dry_run:
                            self.stdout.write(f"Russian translation (DRY RUN): {ru_translation[:100]}...")
                            translations_made = True
            
            # Save the profile if translations were made
            if translations_made and not self.dry_run:
                profile.save(update_fields=['analysis_en', 'analysis_ru'])
            
            return translations_made
            
        except Exception as e:
            logger.exception(f"Error translating profile {profile.pk}")
            self.stdout.write(self.style.ERROR(f"Translation error: {str(e)}"))
            return False

    def call_ai_translation(self, text, target_language):
        """Call AI service for translation based on selected provider"""
        if self.ai_provider == 'ollama':
            return self.call_ollama_translation(text, target_language)
        elif self.ai_provider == 'openai':
            return self.call_openai_translation(text, target_language)
        elif self.ai_provider == 'gemini':
            return self.call_gemini_translation(text, target_language)
        else:
            self.stdout.write(self.style.ERROR(f"Unsupported AI provider: {self.ai_provider}"))
            return None
    
    def parse_tagged_translation(self, text):
        """Parse translation response with <en> and <ru> tags"""
        import re
        
        en_match = re.search(r'<en>(.*?)</en>', text, re.DOTALL)
        ru_match = re.search(r'<ru>(.*?)</ru>', text, re.DOTALL)
        
        result = {}
        if en_match:
            result['en'] = en_match.group(1).strip()
        if ru_match:
            result['ru'] = ru_match.group(1).strip()
        
        return result if result else None
    
    def call_ollama_translation(self, text, target_language):
        """Call Ollama API for translation"""
        import requests
        
        try:
            # Get Ollama configuration
            ollama_base_url = getattr(settings, 'OLLAMA_BASE_URL', 'http://localhost:11434')
            ollama_model = getattr(settings, 'OLLAMA_MODEL', 'gemma3:12b')
            
            # Create translation prompt
            if target_language == 'both':
                prompt = f"""Translate the following Estonian text to English and Russian like you are a native speaker of each language. Do not summarize, translate everything.

Provide the translations in this exact format:
<en>English translation here</en>
<ru>Russian translation here</ru>

Estonian text:
{text}"""
                lang_name = "English and Russian"
            elif target_language == 'en':
                prompt = f"Translate the following Estonian text to English like you are a native English speaker. Do not summarize, translate everything. Provide only the translation, no explanations:\n\n{text}"
                lang_name = "English"
            elif target_language == 'ru':
                prompt = f"Translate the following Estonian text to Russian like you are a native Russian speaker. Do not summarize, translate everything. Provide only the translation, no explanations:\n\n{text}"
                lang_name = "Russian"
            else:
                self.stdout.write(self.style.ERROR(f"Unsupported target language: {target_language}"))
                return None
            
            if self.verbose:
                text_preview = text[:100] + "..." if len(text) > 100 else text
                self.stdout.write(f"   📝 Original text: {text_preview}")
                self.stdout.write(f"   Requesting {lang_name} translation from Ollama ({ollama_model})...")
            
            data = {
                'model': ollama_model,
                'prompt': prompt,
                'stream': True
            }
            
            start_time = time.time()
            response = requests.post(
                f'{ollama_base_url}/api/generate',
                json=data,
                timeout=120,
                stream=True
            )
            api_time = time.time() - start_time
            
            if response.status_code == 200:
                # Handle streaming response
                content = ""
                if self.verbose:
                    self.stdout.write(f"   📤 Streaming translation:", ending='')
                    self.stdout.flush()
                
                for line in response.iter_lines():
                    if line:
                        try:
                            import json
                            chunk = line.decode('utf-8')
                            result = json.loads(chunk)  # Parse JSON line
                            if 'response' in result:
                                chunk_text = result['response']
                                content += chunk_text
                                if self.verbose:
                                    self.stdout.write(chunk_text, ending='')
                                    self.stdout.flush()
                            if result.get('done', False):
                                break
                        except Exception as e:
                            logger.error(f"Error parsing streaming chunk: {e}")
                            continue
                
                if self.verbose:
                    self.stdout.write('')  # New line after streaming
                
                api_time = time.time() - start_time
                content = content.strip()
                
                if content:
                    if target_language == 'both':
                        # Parse tagged response
                        translations = self.parse_tagged_translation(content)
                        if translations:
                            if self.verbose:
                                self.stdout.write(f"   ✅ Translations received ({api_time:.1f}s): EN={len(translations.get('en', ''))} chars, RU={len(translations.get('ru', ''))} chars")
                            return translations
                        else:
                            self.stdout.write(self.style.ERROR("Failed to parse tagged translations from Ollama response"))
                            return None
                    else:
                        if self.verbose:
                            self.stdout.write(f"   ✅ Translation received ({api_time:.1f}s): {content}")
                        return content
                        
                self.stdout.write(self.style.ERROR("No translation content in Ollama response"))
                return None
            else:
                self.stdout.write(self.style.ERROR(f"Ollama API error: {response.status_code} - {response.text}"))
                return None
                
        except requests.exceptions.RequestException as e:
            self.stdout.write(self.style.ERROR(f"Ollama request error: {str(e)}"))
            return None
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"Ollama translation error: {str(e)}"))
            return None
    
    def call_openai_translation(self, text, target_language):
        """Call OpenAI API for translation"""
        import requests
        
        try:
            # Get OpenAI configuration
            openai_api_key = getattr(settings, 'OPENAI_API_KEY', '')
            openai_model = getattr(settings, 'OPENAI_MODEL', 'gpt-4o-mini')
            
            if not openai_api_key:
                self.stdout.write(self.style.ERROR("OPENAI_API_KEY not configured"))
                return None
            
            # Create translation prompt
            if target_language == 'both':
                prompt = f"""Translate the following Estonian text to English and Russian like you are a native speaker of each language. Do not summarize, translate everything.

Provide the translations in this exact format:
<en>English translation here</en>
<ru>Russian translation here</ru>

Estonian text:
{text}"""
                lang_name = "English and Russian"
            elif target_language == 'en':
                prompt = f"Translate the following Estonian text to English like you are a native English speaker. Do not summarize, translate everything. Provide only the translation, no explanations:\n\n{text}"
                lang_name = "English"
            elif target_language == 'ru':
                prompt = f"Translate the following Estonian text to Russian like you are a native Russian speaker. Do not summarize, translate everything. Provide only the translation, no explanations:\n\n{text}"
                lang_name = "Russian"
            else:
                self.stdout.write(self.style.ERROR(f"Unsupported target language: {target_language}"))
                return None
            
            if self.verbose:
                text_preview = text[:100] + "..." if len(text) > 100 else text
                self.stdout.write(f"   📝 Original text: {text_preview}")
                self.stdout.write(f"   Requesting {lang_name} translation from OpenAI ({openai_model})...")
            
            headers = {
                'Content-Type': 'application/json',
                'Authorization': f'Bearer {openai_api_key}'
            }
            
            data = {
                'model': openai_model,
                'messages': [
                    {
                        'role': 'user',
                        'content': prompt
                    }
                ]
            }
            
            start_time = time.time()
            response = requests.post(
                'https://api.openai.com/v1/chat/completions',
                headers=headers,
                json=data,
                timeout=60
            )
            api_time = time.time() - start_time
            
            if response.status_code == 200:
                result = response.json()
                if 'choices' in result and len(result['choices']) > 0:
                    message = result['choices'][0].get('message', {})
                    content = message.get('content', '')
                    if content:
                        if target_language == 'both':
                            # Parse tagged response
                            translations = self.parse_tagged_translation(content)
                            if translations:
                                if self.verbose:
                                    self.stdout.write(f"   ✅ Translations received ({api_time:.1f}s): EN={len(translations.get('en', ''))} chars, RU={len(translations.get('ru', ''))} chars")
                                return translations
                            else:
                                self.stdout.write(self.style.ERROR("Failed to parse tagged translations from OpenAI response"))
                                return None
                        else:
                            if self.verbose:
                                self.stdout.write(f"   ✅ Translation received ({api_time:.1f}s): {content}")
                            return content.strip()
                self.stdout.write(self.style.ERROR("No translation content in OpenAI response"))
                return None
            else:
                self.stdout.write(self.style.ERROR(f"OpenAI API error: {response.status_code} - {response.text}"))
                return None
                
        except requests.exceptions.RequestException as e:
            self.stdout.write(self.style.ERROR(f"OpenAI request error: {str(e)}"))
            return None
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"OpenAI translation error: {str(e)}"))
            return None
    
    def call_gemini_translation(self, text, target_language):
        """Call Google Gemini API for translation"""
        import requests
        
        try:
            # Get Gemini configuration
            gemini_api_key = getattr(settings, 'GEMINI_API_KEY', '')
            gemini_model = getattr(settings, 'GEMINI_MODEL', 'gemini-2.5-flash-lite-preview-09-2025')
            
            if not gemini_api_key:
                self.stdout.write(self.style.ERROR("GEMINI_API_KEY not configured"))
                return None
            
            # Create translation prompt
            if target_language == 'both':
                prompt = f"""Translate the following Estonian text to English and Russian like you are a native speaker of each language. Do not summarize, translate everything.

Provide the translations in this exact format:
<en>English translation here</en>
<ru>Russian translation here</ru>

Estonian text:
{text}"""
                lang_name = "English and Russian"
            elif target_language == 'en':
                prompt = f"Translate the following Estonian text to English like you are a native English speaker. Do not summarize, translate everything. Provide only the translation, no explanations:\n\n{text}"
                lang_name = "English"
            elif target_language == 'ru':
                prompt = f"Translate the following Estonian text to Russian like you are a native Russian speaker. Do not summarize, translate everything. Provide only the translation, no explanations:\n\n{text}"
                lang_name = "Russian"
            else:
                self.stdout.write(self.style.ERROR(f"Unsupported target language: {target_language}"))
                return None
            
            if self.verbose:
                text_preview = text[:100] + "..." if len(text) > 100 else text
                self.stdout.write(f"   📝 Original text: {text_preview}")
                self.stdout.write(f"   Requesting {lang_name} translation from Gemini ({gemini_model})...")
            
            headers = {
                'Content-Type': 'application/json'
            }
            
            data = {
                'contents': [
                    {
                        'parts': [
                            {
                                'text': prompt
                            }
                        ]
                    }
                ],
                'generationConfig': {
                    'temperature': 0.3,
                    'topK': 40,
                    'topP': 0.95
                }
            }
            
            # Use the Gemini REST API endpoint with API key as query parameter
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{gemini_model}:generateContent?key={gemini_api_key}"
            
            start_time = time.time()
            response = requests.post(
                url,
                headers=headers,
                json=data,
                timeout=60
            )
            api_time = time.time() - start_time
            
            if response.status_code == 200:
                result = response.json()
                if 'candidates' in result and len(result['candidates']) > 0:
                    candidate = result['candidates'][0]
                    if 'content' in candidate and 'parts' in candidate['content']:
                        parts = candidate['content']['parts']
                        if len(parts) > 0 and 'text' in parts[0]:
                            content = parts[0]['text'].strip()
                            if content:
                                if target_language == 'both':
                                    # Parse tagged response
                                    translations = self.parse_tagged_translation(content)
                                    if translations:
                                        if self.verbose:
                                            self.stdout.write(f"   ✅ Translations received ({api_time:.1f}s): EN={len(translations.get('en', ''))} chars, RU={len(translations.get('ru', ''))} chars")
                                        return translations
                                    else:
                                        self.stdout.write(self.style.ERROR("Failed to parse tagged translations from Gemini response"))
                                        return None
                                else:
                                    if self.verbose:
                                        self.stdout.write(f"   ✅ Translation received ({api_time:.1f}s): {content}")
                                    return content
                self.stdout.write(self.style.ERROR("No translation content in Gemini response"))
                return None
            else:
                self.stdout.write(self.style.ERROR(f"Gemini API error: {response.status_code} - {response.text}"))
                return None
                
        except requests.exceptions.RequestException as e:
            self.stdout.write(self.style.ERROR(f"Gemini request error: {str(e)}"))
            return None
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"Gemini translation error: {str(e)}"))
            return None
    
    def _process_items_with_batch_api_chunked(self, items_list, item_type):
        """Process items in chunks using Gemini Batch API to avoid rate limits"""
        total_items = len(items_list)
        
        # If batch_size is too small for batch API, use a reasonable default
        chunk_size = max(self.batch_size, 100) if self.use_batch_api else self.batch_size
        
        # Calculate number of chunks
        num_chunks = (total_items + chunk_size - 1) // chunk_size
        
        if num_chunks > 1:
            self.stdout.write(self.style.HTTP_INFO(f"Processing {total_items} {item_type} in {num_chunks} batch chunks (max {chunk_size} items per batch)"))
        else:
            self.stdout.write(self.style.HTTP_INFO(f"Processing {total_items} {item_type} in single batch"))
        
        total_processed = 0
        total_errors = 0
        overall_start_time = time.time()
        
        # Process each chunk
        for chunk_idx in range(num_chunks):
            chunk_start = chunk_idx * chunk_size
            chunk_end = min(chunk_start + chunk_size, total_items)
            chunk_items = items_list[chunk_start:chunk_end]
            
            self.stdout.write("\n" + "=" * 80)
            self.stdout.write(self.style.HTTP_INFO(f"BATCH CHUNK {chunk_idx + 1}/{num_chunks}: Processing items {chunk_start + 1}-{chunk_end}"))
            self.stdout.write("=" * 80)
            
            # Process this chunk
            self._process_items_with_batch_api(chunk_items, item_type)
            
            # Add delay between chunks to avoid rate limits (except for last chunk)
            if chunk_idx < num_chunks - 1:
                delay_seconds = max(self.delay, 2.0)  # At least 2 seconds between batches
                self.stdout.write(f"\nWaiting {delay_seconds}s before next batch chunk to avoid rate limits...")
                time.sleep(delay_seconds)
        
        # Overall summary
        overall_time = time.time() - overall_start_time
        self.stdout.write("\n" + "=" * 80)
        self.stdout.write(self.style.SUCCESS(f"ALL BATCH CHUNKS COMPLETE"))
        self.stdout.write(f"Total chunks processed: {num_chunks}")
        self.stdout.write(f"Total time: {overall_time/60:.1f} minutes ({overall_time:.1f} seconds)")
        self.stdout.write("=" * 80)
    
    def _process_items_with_batch_api(self, items_list, item_type):
        """Process all items using Gemini Batch API"""
        import json
        import tempfile
        import os
        
        start_time = time.time()
        
        try:
            # Step 1: Create JSONL file with all requests
            self.stdout.write("Step 1: Creating batch request file...")
            jsonl_data = self._create_batch_jsonl(items_list)
            
            if not jsonl_data:
                self.stdout.write(self.style.WARNING("No items need translation"))
                return
            
            # Write to temporary file
            with tempfile.NamedTemporaryFile(mode='w', suffix='.jsonl', delete=False) as f:
                for line in jsonl_data:
                    f.write(json.dumps(line) + '\n')
                temp_file_path = f.name
            
            self.stdout.write(f"Created batch file with {len(jsonl_data)} requests")
            
            try:
                # Step 2: Upload file to Gemini
                self.stdout.write("\nStep 2: Uploading batch file to Gemini...")
                file_uri = self._upload_batch_file(temp_file_path)
                self.stdout.write(f"Uploaded file: {file_uri}")
                
                # Step 3: Create batch job
                self.stdout.write("\nStep 3: Creating batch job...")
                batch_job_name = self._create_batch_job(file_uri)
                self.stdout.write(f"Created batch job: {batch_job_name}")
                
                # Generate resume command with all relevant parameters
                resume_cmd = self._generate_resume_command(batch_job_name)
                self.stdout.write(self.style.WARNING(f"\n⚠️  IMPORTANT: If this script crashes, resume with this command:"))
                self.stdout.write(self.style.WARNING(f"━" * 80))
                self.stdout.write(self.style.SUCCESS(resume_cmd))
                self.stdout.write(self.style.WARNING(f"━" * 80 + "\n"))
                
                # Step 4: Poll for completion
                self.stdout.write("Step 4: Waiting for batch job to complete...")
                result_file_uri = self._poll_batch_job(batch_job_name)
                
                if not result_file_uri:
                    self.stdout.write(self.style.ERROR("Batch job failed or timed out"))
                    return
                
                # Step 5: Download and process results
                self.stdout.write("\nStep 5: Downloading and processing results...")
                results = self._download_batch_results(result_file_uri, batch_job_name)
                
                # Step 6: Update database with results
                self.stdout.write("\nStep 6: Updating database with translations...")
                processed, errors = self._update_items_with_results(items_list, results)
                
                # Summary
                total_time = time.time() - start_time
                self.stdout.write("\n" + "=" * 60)
                self.stdout.write(self.style.SUCCESS("BATCH PROCESSING COMPLETE"))
                self.stdout.write(f"Total time: {total_time/60:.1f} minutes ({total_time:.1f} seconds)")
                self.stdout.write(f"Successfully processed: {processed}/{len(items_list)} {item_type}")
                if errors > 0:
                    self.stdout.write(self.style.ERROR(f"Errors encountered: {errors}"))
                else:
                    self.stdout.write(self.style.SUCCESS("No errors encountered!"))
                
                if self.dry_run:
                    self.stdout.write(self.style.WARNING("Note: This was a dry run - no translations were saved to database"))
                    
            finally:
                # Clean up temporary file
                if os.path.exists(temp_file_path):
                    os.unlink(temp_file_path)
                
        except Exception as e:
            logger.exception("Error in batch API processing")
            self.stdout.write(self.style.ERROR(f"Batch processing error: {str(e)}"))
            raise
    
    def _create_batch_jsonl(self, items_list):
        """Create JSONL data for batch API requests"""
        jsonl_data = []
        gemini_model = getattr(settings, 'GEMINI_MODEL', 'gemini-2.5-flash-lite-preview-09-2025')
        
        for idx, item in enumerate(items_list):
            if not item.analysis:
                continue
            
            # Check what translations are needed
            needs_en = (self.target_language in ['en', 'both']) and (not item.analysis_en or self.overwrite)
            needs_ru = (self.target_language in ['ru', 'both']) and (not item.analysis_ru or self.overwrite)
            
            if not needs_en and not needs_ru:
                continue
            
            # Create translation prompt
            if needs_en and needs_ru:
                prompt = f"""Translate the following Estonian text to English and Russian like you are a native speaker of each language. Do not summarize, translate everything.

Provide the translations in this exact format:
<en>English translation here</en>
<ru>Russian translation here</ru>

Estonian text:
{item.analysis}"""
            elif needs_en:
                prompt = f"Translate the following Estonian text to English like you are a native English speaker. Do not summarize, translate everything. Provide only the translation, no explanations:\n\n{item.analysis}"
            elif needs_ru:
                prompt = f"Translate the following Estonian text to Russian like you are a native Russian speaker. Do not summarize, translate everything. Provide only the translation, no explanations:\n\n{item.analysis}"
            else:
                # This should never happen due to the continue above, but satisfies linter
                continue
            
            # Create batch request
            request_data = {
                "key": f"item_{item.pk}",
                "request": {
                    "contents": [
                        {
                            "parts": [
                                {
                                    "text": prompt
                                }
                            ]
                        }
                    ],
                    "generationConfig": {
                        "temperature": 0.3,
                        "topK": 40,
                        "topP": 0.95
                    }
                }
            }
            
            jsonl_data.append(request_data)
        
        return jsonl_data
    
    def _upload_batch_file(self, file_path):
        """Upload JSONL file to Gemini API"""
        gemini_api_key = getattr(settings, 'GEMINI_API_KEY', '')
        
        if not gemini_api_key:
            raise CommandError("GEMINI_API_KEY not configured")
        
        # Read file content
        with open(file_path, 'rb') as f:
            file_content = f.read()
        
        # Upload file to Gemini Files API
        url = f"https://generativelanguage.googleapis.com/upload/v1beta/files?key={gemini_api_key}"
        
        headers = {
            'X-Goog-Upload-Protocol': 'multipart'
        }
        
        # Create multipart form data manually
        import json
        metadata = json.dumps({"file": {"displayName": "batch_translations"}})
        
        files = {
            'metadata': (None, metadata, 'application/json'),
            'file': ('batch_requests.jsonl', file_content, 'application/json')
        }
        
        response = requests.post(url, headers=headers, files=files, timeout=120)
        
        if response.status_code == 200:
            result = response.json()
            # The response should contain 'file' with 'name' field
            if 'file' in result and 'name' in result['file']:
                return result['file']['name']
            elif 'name' in result:
                return result['name']
            else:
                raise CommandError(f"Unexpected upload response format: {result}")
        else:
            raise CommandError(f"File upload failed: {response.status_code} - {response.text}")
    
    def _create_batch_job(self, file_uri):
        """Create a batch job with the uploaded file"""
        gemini_api_key = getattr(settings, 'GEMINI_API_KEY', '')
        gemini_model = getattr(settings, 'GEMINI_MODEL', 'gemini-2.5-flash-lite-preview-09-2025')
        
        # Use the correct batch API endpoint
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{gemini_model}:batchGenerateContent?key={gemini_api_key}"
        
        headers = {
            'Content-Type': 'application/json'
        }
        
        # Correct payload format for batch creation
        data = {
            "batch": {
                "display_name": "translate_politician_profiles",
                "input_config": {
                    "file_name": file_uri
                }
            }
        }
        
        response = requests.post(url, headers=headers, json=data, timeout=120)
        
        if response.status_code == 200:
            result = response.json()
            if 'name' in result:
                return result['name']
            else:
                raise CommandError(f"Unexpected batch creation response format: {result}")
        else:
            raise CommandError(f"Batch job creation failed: {response.status_code} - {response.text}")
    
    def _poll_batch_job(self, batch_job_name, max_wait_seconds=3600, poll_interval=30):
        """Poll batch job status until completion or timeout"""
        gemini_api_key = getattr(settings, 'GEMINI_API_KEY', '')
        
        url = f"https://generativelanguage.googleapis.com/v1beta/{batch_job_name}?key={gemini_api_key}"
        
        start_time = time.time()
        elapsed_time = 0
        first_unknown = True  # Flag to show response once if status is unknown
        
        while elapsed_time < max_wait_seconds:
            response = requests.get(url, timeout=30)
            
            if response.status_code == 200:
                result = response.json()
                
                # Get state - it's nested in metadata.state
                metadata = result.get('metadata', {})
                state = metadata.get('state', 'UNKNOWN')
                
                # Get batch statistics for progress info
                batch_stats = metadata.get('batchStats', {})
                total_requests = int(batch_stats.get('requestCount', 0))
                pending_requests = int(batch_stats.get('pendingRequestCount', 0))
                completed_requests = total_requests - pending_requests
                
                elapsed_time = time.time() - start_time
                
                # If status is UNKNOWN, show the full response for debugging
                if state == 'UNKNOWN' and first_unknown:
                    self.stdout.write(self.style.WARNING(f"⚠️  Received UNKNOWN status. Full API response:"))
                    import json
                    self.stdout.write(json.dumps(result, indent=2))
                    first_unknown = False
                
                # Show progress with request counts
                if total_requests > 0:
                    progress_pct = (completed_requests / total_requests) * 100
                    self.stdout.write(f"Status: {state} | Progress: {completed_requests}/{total_requests} ({progress_pct:.1f}%) | Elapsed: {elapsed_time/60:.1f}m")
                else:
                    self.stdout.write(f"Status: {state} (elapsed: {elapsed_time/60:.1f}m)")
                
                if state == 'BATCH_STATE_SUCCEEDED':
                    self.stdout.write(self.style.SUCCESS("Batch job completed successfully!"))
                    # Get output file - try multiple locations
                    # 1. Check metadata.output.responsesFile
                    output = metadata.get('output', {})
                    file_name = output.get('responsesFile')
                    
                    # 2. If not found, check response.responsesFile at top level
                    if not file_name:
                        response_data = result.get('response', {})
                        file_name = response_data.get('responsesFile')
                    
                    # 3. If still not found, check metadata.outputConfig.fileName (legacy)
                    if not file_name:
                        output_config = metadata.get('outputConfig', {})
                        file_name = output_config.get('fileName')
                    
                    if file_name:
                        return file_name
                    else:
                        self.stdout.write(self.style.WARNING("Full response for debugging:"))
                        import json
                        self.stdout.write(json.dumps(result, indent=2))
                        raise CommandError(f"No result file in completed batch")
                
                elif state in ['BATCH_STATE_FAILED', 'BATCH_STATE_CANCELLED']:
                    error_msg = result.get('error', {}).get('message', 'Unknown error')
                    self.stdout.write(self.style.ERROR(f"Full error response:"))
                    import json
                    self.stdout.write(json.dumps(result, indent=2))
                    raise CommandError(f"Batch job {state}: {error_msg}")
                
                elif state == 'UNKNOWN':
                    # If still unknown after showing response, it might be an API issue
                    if elapsed_time > 300:  # After 5 minutes
                        raise CommandError(f"Batch job status remains UNKNOWN after {elapsed_time/60:.1f} minutes. Check API response above.")
                
                # Job still in progress (or unknown), wait before polling again
                time.sleep(poll_interval)
                elapsed_time = time.time() - start_time
            else:
                raise CommandError(f"Failed to check batch status: {response.status_code} - {response.text}")
        
        raise CommandError(f"Batch job timed out after {max_wait_seconds/60:.1f} minutes")
    
    def _download_batch_results(self, result_file_uri, batch_job_name=None):
        """Download and parse batch results using google-genai SDK"""
        import json
        
        gemini_api_key = getattr(settings, 'GEMINI_API_KEY', '')
        
        self.stdout.write(f"Result file URI: {result_file_uri}")
        
        # Use the google-genai SDK to handle batch file downloads properly
        try:
            from google import genai
            from google.genai import types
            
            # Initialize the client
            client = genai.Client(api_key=gemini_api_key)
            
            # Download the file using the SDK
            self.stdout.write(f"Downloading using google-genai SDK...")
            file_content_bytes = client.files.download(file=result_file_uri)
            file_content = file_content_bytes.decode('utf-8')
            
            self.stdout.write(f"Successfully downloaded batch results")
            
            # Parse JSONL response
            results = {}
            for line in file_content.strip().split('\n'):
                if line:
                    try:
                        result_item = json.loads(line)
                        key = result_item.get('key', '')
                        response_data = result_item.get('response', {})
                        
                        # Extract text from response
                        if 'candidates' in response_data and len(response_data['candidates']) > 0:
                            candidate = response_data['candidates'][0]
                            if 'content' in candidate and 'parts' in candidate['content']:
                                parts = candidate['content']['parts']
                                if len(parts) > 0 and 'text' in parts[0]:
                                    text = parts[0]['text'].strip()
                                    results[key] = text
                    except json.JSONDecodeError as e:
                        logger.error(f"Failed to parse result line: {e}")
                        continue
            
            self.stdout.write(f"Downloaded {len(results)} translation results")
            return results
            
        except ImportError:
            # Fallback to requests if SDK is not installed
            self.stdout.write(self.style.WARNING("google-genai SDK not found, falling back to requests"))
            return self._download_batch_results_with_requests(result_file_uri)
        except Exception as e:
            logger.exception(f"Error downloading with SDK: {e}")
            self.stdout.write(self.style.ERROR(f"SDK download failed: {str(e)}"))
            self.stdout.write("Falling back to requests...")
            return self._download_batch_results_with_requests(result_file_uri)
    
    def _download_batch_results_with_requests(self, result_file_uri):
        """Fallback method using raw requests"""
        import json
        
        gemini_api_key = getattr(settings, 'GEMINI_API_KEY', '')
        
        # Try direct download
        download_url = f"https://generativelanguage.googleapis.com/v1beta/{result_file_uri}?alt=media&key={gemini_api_key}"
        response = requests.get(download_url, timeout=120)
        
        if response.status_code == 200:
            # Parse JSONL response
            results = {}
            for line in response.text.strip().split('\n'):
                if line:
                    try:
                        result_item = json.loads(line)
                        key = result_item.get('key', '')
                        response_data = result_item.get('response', {})
                        
                        # Extract text from response
                        if 'candidates' in response_data and len(response_data['candidates']) > 0:
                            candidate = response_data['candidates'][0]
                            if 'content' in candidate and 'parts' in candidate['content']:
                                parts = candidate['content']['parts']
                                if len(parts) > 0 and 'text' in parts[0]:
                                    text = parts[0]['text'].strip()
                                    results[key] = text
                    except json.JSONDecodeError as e:
                        logger.error(f"Failed to parse result line: {e}")
                        continue
            
            self.stdout.write(f"Downloaded {len(results)} translation results")
            return results
        else:
            raise CommandError(f"Failed to download results: {response.status_code} - {response.text}")
    
    def _update_items_with_results(self, items_list, results):
        """Update database items with translation results"""
        processed = 0
        errors = 0
        
        for item in items_list:
            key = f"item_{item.pk}"
            
            if key not in results:
                self.stdout.write(f"No result for item {item.pk}")
                errors += 1
                continue
            
            try:
                translation_text = results[key]
                
                # Check if we need to parse both translations
                needs_en = (self.target_language in ['en', 'both']) and (not item.analysis_en or self.overwrite)
                needs_ru = (self.target_language in ['ru', 'both']) and (not item.analysis_ru or self.overwrite)
                
                if needs_en and needs_ru:
                    # Parse tagged response
                    translations = self.parse_tagged_translation(translation_text)
                    if translations:
                        if needs_en and 'en' in translations:
                            if not self.dry_run:
                                item.analysis_en = translations['en']
                            else:
                                self.stdout.write(f"[DRY RUN] Would save EN translation for item {item.pk}")
                        if needs_ru and 'ru' in translations:
                            if not self.dry_run:
                                item.analysis_ru = translations['ru']
                            else:
                                self.stdout.write(f"[DRY RUN] Would save RU translation for item {item.pk}")
                        
                        if not self.dry_run:
                            item.save(update_fields=['analysis_en', 'analysis_ru'])
                        processed += 1
                    else:
                        self.stdout.write(self.style.ERROR(f"Failed to parse translations for item {item.pk}"))
                        errors += 1
                else:
                    # Single translation
                    if needs_en:
                        if not self.dry_run:
                            item.analysis_en = translation_text
                            item.save(update_fields=['analysis_en'])
                        else:
                            self.stdout.write(f"[DRY RUN] Would save EN translation for item {item.pk}")
                    elif needs_ru:
                        if not self.dry_run:
                            item.analysis_ru = translation_text
                            item.save(update_fields=['analysis_ru'])
                        else:
                            self.stdout.write(f"[DRY RUN] Would save RU translation for item {item.pk}")
                    
                    processed += 1
                    
            except Exception as e:
                logger.exception(f"Error updating item {item.pk}")
                self.stdout.write(self.style.ERROR(f"Error updating item {item.pk}: {str(e)}"))
                errors += 1
        
        return processed, errors
    
    def _generate_resume_command(self, batch_job_name):
        """Generate a complete resume command with all relevant parameters"""
        import sys
        
        # Start with the base command
        # Get the management command path (works in both docker and local)
        cmd_parts = ["python manage.py translate_politician_profiles"]
        
        # Add essential parameters
        cmd_parts.append(f"--ai-provider=gemini")
        cmd_parts.append(f"--resume-from-batch-id={batch_job_name}")
        
        # Add target language if not default
        if self.target_language != 'both':
            cmd_parts.append(f"--target-language={self.target_language}")
        
        # Add overwrite flag if set
        if self.overwrite:
            cmd_parts.append("--overwrite")
        
        # Add dry-run if set
        if self.dry_run:
            cmd_parts.append("--dry-run")
        
        # Add verbose if set
        if self.verbose:
            cmd_parts.append("--verbose")
        
        # Join with line continuation for readability
        return " \\\n    ".join(cmd_parts)
    
    def _update_items_from_batch_results(self, results):
        """Update database items with translation results (used when resuming)"""
        processed = 0
        errors = 0
        
        # Extract item PKs from result keys (format: "item_{pk}")
        item_pks = []
        for key in results.keys():
            if key.startswith('item_'):
                try:
                    pk = int(key.replace('item_', ''))
                    item_pks.append(pk)
                except ValueError:
                    self.stdout.write(self.style.WARNING(f"Could not parse PK from key: {key}"))
                    continue
        
        if not item_pks:
            self.stdout.write(self.style.ERROR("No valid item keys found in batch results"))
            return 0, len(results)
        
        # Query for all items
        items = PoliticianProfilePart.objects.filter(pk__in=item_pks)
        items_dict = {item.pk: item for item in items}
        
        self.stdout.write(f"Found {len(items_dict)} items in database matching {len(item_pks)} result keys")
        
        # Process each result
        for key, translation_text in results.items():
            if not key.startswith('item_'):
                continue
            
            try:
                pk = int(key.replace('item_', ''))
            except ValueError:
                errors += 1
                continue
            
            if pk not in items_dict:
                self.stdout.write(f"Item {pk} not found in database")
                errors += 1
                continue
            
            item = items_dict[pk]
            
            try:
                # Check if we need to parse both translations
                needs_en = (self.target_language in ['en', 'both']) and (not item.analysis_en or self.overwrite)
                needs_ru = (self.target_language in ['ru', 'both']) and (not item.analysis_ru or self.overwrite)
                
                if needs_en and needs_ru:
                    # Parse tagged response
                    translations = self.parse_tagged_translation(translation_text)
                    if translations:
                        if needs_en and 'en' in translations:
                            if not self.dry_run:
                                item.analysis_en = translations['en']
                            else:
                                self.stdout.write(f"[DRY RUN] Would save EN translation for item {item.pk}")
                        if needs_ru and 'ru' in translations:
                            if not self.dry_run:
                                item.analysis_ru = translations['ru']
                            else:
                                self.stdout.write(f"[DRY RUN] Would save RU translation for item {item.pk}")
                        
                        if not self.dry_run:
                            item.save(update_fields=['analysis_en', 'analysis_ru'])
                        processed += 1
                    else:
                        self.stdout.write(self.style.ERROR(f"Failed to parse translations for item {item.pk}"))
                        errors += 1
                else:
                    # Single translation
                    if needs_en:
                        if not self.dry_run:
                            item.analysis_en = translation_text
                            item.save(update_fields=['analysis_en'])
                        else:
                            self.stdout.write(f"[DRY RUN] Would save EN translation for item {item.pk}")
                    elif needs_ru:
                        if not self.dry_run:
                            item.analysis_ru = translation_text
                            item.save(update_fields=['analysis_ru'])
                        else:
                            self.stdout.write(f"[DRY RUN] Would save RU translation for item {item.pk}")
                    
                    processed += 1
                    
            except Exception as e:
                logger.exception(f"Error updating item {item.pk}")
                self.stdout.write(self.style.ERROR(f"Error updating item {item.pk}: {str(e)}"))
                errors += 1
        
        return processed, errors
