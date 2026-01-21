"""
Management command to translate speech summaries using AI providers with Batch API support
"""
import time
import logging
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed
from django.core.management.base import BaseCommand, CommandError
from django.conf import settings
from django.db import models

from parliament_speeches.models import Speech, AgendaItem
from .batch_api_mixin import GeminiBatchAPIMixin

logger = logging.getLogger(__name__)


class Command(GeminiBatchAPIMixin, BaseCommand):
    help = 'Translate speech AI summaries to English and Russian using AI providers (OpenAI, Gemini, Ollama)'

    def add_arguments(self, parser):
        parser.add_argument(
            '--limit',
            type=int,
            default=None,
            help='Number of speeches to process (default: all eligible speeches)'
        )
        parser.add_argument(
            '--speech-id',
            type=int,
            help='Process specific speech by ID'
        )
        parser.add_argument(
            '--agenda-id',
            type=int,
            help='Process all speeches for specific agenda item by ID'
        )
        parser.add_argument(
            '--plenary-session-id',
            type=int,
            help='Process all speeches from specific plenary session by ID'
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
            help='Number of speeches to process in parallel (default: 5)'
        )
        parser.add_argument(
            '--ai-provider',
            type=str,
            choices=['ollama', 'openai', 'gemini'],
            default='gemini',
            help='AI provider to use for translation (ollama, openai, gemini). Default: gemini.'
        )
        parser.add_argument(
            '--verbose',
            action='store_true',
            help='Show detailed progress and streaming translation results'
        )
        
        # Add batch API arguments from mixin
        self.add_batch_api_arguments(parser)

    def handle(self, *args, **options):
        self.dry_run = options['dry_run']
        self.delay = options['delay']
        self.batch_size = options['batch_size']
        self.target_language = options['target_language']
        self.overwrite = options['overwrite']
        self.ai_provider = options['ai_provider']
        self.verbose = options['verbose']
        
        # Initialize batch API settings
        self.initialize_batch_api(options)
        
        if self.dry_run:
            self.stdout.write(self.style.WARNING("DRY RUN MODE - No translations will be saved"))
        
        # Check if we're resuming a batch job
        if self.resume_from_batch_id:
            self.stdout.write(self.style.HTTP_INFO(f"RESUMING Google Gemini BATCH API job: {self.resume_from_batch_id}"))
            self.stdout.write("=" * 80)
            self.resume_batch_job_only(
                self.resume_from_batch_id,
                Speech,
                self._update_speech_with_translation
            )
            return

        # Display AI provider being used
        if self.ai_provider == 'ollama':
            ollama_url = getattr(settings, 'OLLAMA_BASE_URL', 'http://localhost:11434')
            ollama_model = getattr(settings, 'OLLAMA_MODEL', 'gemma3:12b')
            self.stdout.write(f"Using Ollama for translations ({ollama_model} at {ollama_url})")
        elif self.ai_provider == 'openai':
            self.stdout.write("Using OpenAI for translations")
        elif self.ai_provider == 'gemini':
            self.stdout.write("Using Google Gemini for translations")

        try:
            if options['speech_id']:
                # Process specific speech
                self.process_specific_speech(options['speech_id'], options['overwrite'])
            elif options['agenda_id']:
                # Process speeches for specific agenda item
                self.process_agenda_speeches(options['agenda_id'], options['overwrite'])
            elif options['plenary_session_id']:
                # Process speeches from specific plenary session
                self.process_session_speeches(options['plenary_session_id'], options['overwrite'])
            else:
                # Process multiple speeches
                self.process_speeches(options['limit'], options['overwrite'])
                
            self.stdout.write(self.style.SUCCESS("Successfully completed speech translation"))
            
        except Exception as e:
            logger.exception("Error during speech translation")
            raise CommandError(f"Error during processing: {str(e)}")

    def process_specific_speech(self, speech_id, overwrite):
        """Process a specific speech by ID"""
        try:
            speech = Speech.objects.get(pk=speech_id)
        except Speech.DoesNotExist:
            raise CommandError(f"Speech with ID {speech_id} not found")

        if not speech.ai_summary:
            raise CommandError(f"Speech {speech_id} does not have an AI summary to translate")

        # Check if translations already exist
        if not overwrite:
            if self.target_language in ['en', 'both'] and speech.ai_summary_en:
                self.stdout.write(f"Speech {speech_id} already has English translation (use --overwrite to replace)")
                return
            if self.target_language in ['ru', 'both'] and speech.ai_summary_ru:
                self.stdout.write(f"Speech {speech_id} already has Russian translation (use --overwrite to replace)")
                return
        
        self.stdout.write(f"Processing speech {speech_id}: {speech.speaker}")
        success = self.translate_speech(speech)
        
        if success:
            self.stdout.write(self.style.SUCCESS(f"Successfully translated speech {speech_id}"))
        else:
            self.stdout.write(self.style.ERROR(f"Failed to translate speech {speech_id}"))

    def process_agenda_speeches(self, agenda_id, overwrite):
        """Process all speeches for a specific agenda item"""
        try:
            agenda = AgendaItem.objects.get(pk=agenda_id)
        except AgendaItem.DoesNotExist:
            raise CommandError(f"Agenda item with ID {agenda_id} not found")

        # Get speeches for this agenda item that have AI summaries
        queryset = agenda.speeches.filter(
            ai_summary__isnull=False
        ).exclude(ai_summary='').select_related('politician', 'agenda_item')
        
        if not overwrite:
            if self.target_language == 'en':
                queryset = queryset.filter(ai_summary_en__isnull=True)
            elif self.target_language == 'ru':
                queryset = queryset.filter(ai_summary_ru__isnull=True)
            else:  # both
                queryset = queryset.filter(
                    models.Q(ai_summary_en__isnull=True) | models.Q(ai_summary_ru__isnull=True)
                )
        
        speeches = queryset.order_by('-date')
        
        if not speeches.exists():
            self.stdout.write(f"No speeches found for agenda item {agenda_id} that need translation")
            return

        total_count = speeches.count()
        self.stdout.write(f"Processing agenda item: {agenda.title[:100]}...")
        self.stdout.write(f"Found {total_count} speeches to translate for agenda item {agenda_id}")
        self.stdout.write("=" * 60)
        
        processed = 0
        errors = 0
        start_time = time.time()
        
        # Process speeches in batches
        speeches_list = list(speeches)
        total_batches = (len(speeches_list) + self.batch_size - 1) // self.batch_size
        
        for batch_num in range(total_batches):
            batch_start = batch_num * self.batch_size
            batch_end = min(batch_start + self.batch_size, len(speeches_list))
            batch_speeches = speeches_list[batch_start:batch_end]
            
            self.stdout.write(f"\n{'='*20} BATCH {batch_num + 1}/{total_batches} {'='*20}")
            self.stdout.write(f"Processing {len(batch_speeches)} speeches in parallel...")
            
            batch_processed, batch_errors = self._process_batch(
                batch_speeches, batch_start, len(speeches_list), start_time
            )
            
            processed += batch_processed
            errors += batch_errors
        
        # Final summary with timing
        total_time = time.time() - start_time
        avg_time_per_speech = total_time / total_count if total_count > 0 else 0
        
        self.stdout.write("\n" + "=" * 60)
        self.stdout.write(self.style.SUCCESS("PROCESSING COMPLETE"))
        self.stdout.write(f"Agenda item: {agenda.title[:100]}...")
        self.stdout.write(f"Total time: {total_time/60:.1f} minutes ({total_time:.1f} seconds)")
        self.stdout.write(f"Average time per speech: {avg_time_per_speech:.1f} seconds")
        self.stdout.write(f"Batch size: {self.batch_size} parallel requests")
        self.stdout.write(f"Successfully processed: {processed}/{total_count} speeches")
        if errors > 0:
            self.stdout.write(self.style.ERROR(f"Errors encountered: {errors}"))
        else:
            self.stdout.write(self.style.SUCCESS("No errors encountered!"))
        
        if self.dry_run:
            self.stdout.write(self.style.WARNING("Note: This was a dry run - no translations were saved to database"))

    def process_session_speeches(self, plenary_session_id, overwrite):
        """Process all speeches from a specific plenary session"""
        from parliament_speeches.models import PlenarySession
        
        try:
            session = PlenarySession.objects.get(pk=plenary_session_id)
        except PlenarySession.DoesNotExist:
            raise CommandError(f"Plenary session with ID {plenary_session_id} not found")

        # Get all speeches from this session that have AI summaries
        queryset = Speech.objects.filter(
            agenda_item__plenary_session=session,
            ai_summary__isnull=False
        ).exclude(ai_summary='').select_related('politician', 'agenda_item')
        
        if not overwrite:
            if self.target_language == 'en':
                queryset = queryset.filter(ai_summary_en__isnull=True)
            elif self.target_language == 'ru':
                queryset = queryset.filter(ai_summary_ru__isnull=True)
            else:  # both
                queryset = queryset.filter(
                    models.Q(ai_summary_en__isnull=True) | models.Q(ai_summary_ru__isnull=True)
                )
        
        speeches = queryset.order_by('-date')
        
        if not speeches.exists():
            self.stdout.write(f"No speeches found in plenary session {plenary_session_id} that need translation")
            return

        total_count = speeches.count()
        self.stdout.write(f"Processing plenary session: {session.title[:100]}...")
        self.stdout.write(f"Session date: {session.date}")
        self.stdout.write(f"Found {total_count} speeches to translate from plenary session {plenary_session_id}")
        self.stdout.write("=" * 60)
        
        processed = 0
        errors = 0
        start_time = time.time()
        
        # Process speeches in batches
        speeches_list = list(speeches)
        total_batches = (len(speeches_list) + self.batch_size - 1) // self.batch_size
        
        for batch_num in range(total_batches):
            batch_start = batch_num * self.batch_size
            batch_end = min(batch_start + self.batch_size, len(speeches_list))
            batch_speeches = speeches_list[batch_start:batch_end]
            
            self.stdout.write(f"\n{'='*20} BATCH {batch_num + 1}/{total_batches} {'='*20}")
            self.stdout.write(f"Processing {len(batch_speeches)} speeches in parallel...")
            
            batch_processed, batch_errors = self._process_batch(
                batch_speeches, batch_start, len(speeches_list), start_time
            )
            
            processed += batch_processed
            errors += batch_errors
        
        # Final summary with timing
        total_time = time.time() - start_time
        avg_time_per_speech = total_time / total_count if total_count > 0 else 0
        
        self.stdout.write("\n" + "=" * 60)
        self.stdout.write(self.style.SUCCESS("PROCESSING COMPLETE"))
        self.stdout.write(f"Plenary session: {session.title[:100]}...")
        self.stdout.write(f"Total time: {total_time/60:.1f} minutes ({total_time:.1f} seconds)")
        self.stdout.write(f"Average time per speech: {avg_time_per_speech:.1f} seconds")
        self.stdout.write(f"Batch size: {self.batch_size} parallel requests")
        self.stdout.write(f"Successfully processed: {processed}/{total_count} speeches")
        if errors > 0:
            self.stdout.write(self.style.ERROR(f"Errors encountered: {errors}"))
        else:
            self.stdout.write(self.style.SUCCESS("No errors encountered!"))
        
        if self.dry_run:
            self.stdout.write(self.style.WARNING("Note: This was a dry run - no translations were saved to database"))

    def process_speeches(self, limit, overwrite):
        """Process multiple speeches"""
        # Get speeches that have AI summaries but need translations
        queryset = Speech.objects.filter(
            ai_summary__isnull=False
        ).exclude(ai_summary='').select_related('politician', 'agenda_item')
        
        if not overwrite:
            if self.target_language == 'en':
                queryset = queryset.filter(ai_summary_en__isnull=True)
            elif self.target_language == 'ru':
                queryset = queryset.filter(ai_summary_ru__isnull=True)
            else:  # both
                queryset = queryset.filter(
                    models.Q(ai_summary_en__isnull=True) | models.Q(ai_summary_ru__isnull=True)
                )
        
        # Order by date to process newer speeches first
        speeches = queryset.order_by('-date')
        if limit is not None:
            speeches = speeches[:limit]
        
        if not speeches.exists():
            self.stdout.write("No speeches found that need translation")
            return

        total_count = speeches.count()
        self.stdout.write(f"Found {total_count} speeches to translate")
        self.stdout.write("=" * 60)
        
        speeches_list = list(speeches)
        
        # Use Gemini Batch API if enabled
        if self.should_use_batch_api():
            self.stdout.write(self.style.HTTP_INFO(f"Using Google Gemini BATCH API for speeches"))
            self.stdout.write("=" * 80)
            self.process_batch_with_chunking(
                speeches_list,
                "speeches",
                self._create_speech_translation_prompt,
                self._update_speech_with_translation
            )
            return
        
        # Original parallel processing logic
        processed = 0
        errors = 0
        start_time = time.time()
        
        # Process speeches in batches
        total_batches = (len(speeches_list) + self.batch_size - 1) // self.batch_size
        
        for batch_num in range(total_batches):
            batch_start = batch_num * self.batch_size
            batch_end = min(batch_start + self.batch_size, len(speeches_list))
            batch_speeches = speeches_list[batch_start:batch_end]
            
            self.stdout.write(f"\n{'='*20} BATCH {batch_num + 1}/{total_batches} {'='*20}")
            self.stdout.write(f"Processing {len(batch_speeches)} speeches in parallel...")
            
            batch_processed, batch_errors = self._process_batch(
                batch_speeches, batch_start, len(speeches_list), start_time
            )
            
            processed += batch_processed
            errors += batch_errors

        # Final summary with timing
        total_time = time.time() - start_time
        avg_time_per_speech = total_time / total_count if total_count > 0 else 0
        
        self.stdout.write("\n" + "=" * 60)
        self.stdout.write(self.style.SUCCESS("PROCESSING COMPLETE"))
        self.stdout.write(f"Total time: {total_time/60:.1f} minutes ({total_time:.1f} seconds)")
        self.stdout.write(f"Average time per speech: {avg_time_per_speech:.1f} seconds")
        self.stdout.write(f"Batch size: {self.batch_size} parallel requests")
        self.stdout.write(f"Successfully processed: {processed}/{total_count} speeches")
        if errors > 0:
            self.stdout.write(self.style.ERROR(f"Errors encountered: {errors}"))
        else:
            self.stdout.write(self.style.SUCCESS("No errors encountered!"))
        
        if self.dry_run:
            self.stdout.write(self.style.WARNING("Note: This was a dry run - no translations were saved to database"))
    
    def _process_batch(self, batch_speeches, batch_start_idx, total_count, overall_start_time):
        """Process a batch of speeches in parallel"""
        processed = 0
        errors = 0
        batch_start_time = time.time()
        
        # Create futures for parallel processing
        with ThreadPoolExecutor(max_workers=self.batch_size) as executor:
            # Submit all speeches in the batch
            future_to_speech = {
                executor.submit(self._process_single_speech, speech, idx + batch_start_idx + 1, total_count, overall_start_time): speech 
                for idx, speech in enumerate(batch_speeches)
            }
            
            # Process completed futures
            for future in as_completed(future_to_speech):
                speech = future_to_speech[future]
                try:
                    success = future.result()
                    if success:
                        processed += 1
                    else:
                        errors += 1
                except Exception as e:
                    errors += 1
                    logger.exception(f"Error in parallel processing for speech {speech.pk}")
                    self.stdout.write(self.style.ERROR(f"✗ Error processing speech {speech.pk}: {str(e)}"))
        
        batch_time = time.time() - batch_start_time
        self.stdout.write(f"Batch completed in {batch_time:.1f}s - {processed} successful, {errors} errors")
        
        return processed, errors
    
    def _process_single_speech(self, speech, current_idx, total_count, overall_start_time):
        """Process a single speech (for parallel execution)"""
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
            self.stdout.write(f"Processing: {speech.speaker} ({speech.date.date()}) - ID: {speech.pk}")
            
            if self.verbose:
                # Show what will be translated
                translate_info = []
                if hasattr(speech, 'ai_summary') and speech.ai_summary:
                    if self.target_language in ['en', 'both'] and (not getattr(speech, 'ai_summary_en', None) or self.overwrite):
                        translate_info.append("summary→EN")
                    if self.target_language in ['ru', 'both'] and (not getattr(speech, 'ai_summary_ru', None) or self.overwrite):
                        translate_info.append("summary→RU")
                
                translation_method = self.ai_provider.upper()
                if translate_info:
                    self.stdout.write(f"   └─ {translation_method} | Tasks: {', '.join(translate_info)}")
                else:
                    self.stdout.write(f"   └─ ⚠️  No translations needed (already exists or no AI summary)")
            
            speech_start_time = time.time()
            success = self.translate_speech(speech)
            speech_duration = time.time() - speech_start_time
            
            if success:
                self.stdout.write(self.style.SUCCESS(f"✓ Translated speech ({speech_duration:.1f}s)"))
                return True
            else:
                self.stdout.write(self.style.ERROR(f"✗ Failed to translate speech ({speech_duration:.1f}s)"))
                return False
                
        except Exception as e:
            logger.exception(f"Error processing speech {speech.pk}")
            self.stdout.write(self.style.ERROR(f"✗ Error processing speech {speech.pk}: {str(e)}"))
            return False

    def translate_speech(self, speech):
        """Translate AI summary for a single speech"""
        if not speech.ai_summary:
            self.stdout.write(f"Skipping speech {speech.pk} - no AI summary to translate")
            return False

        try:
            translations_made = False
            
            # For OpenAI and Gemini, translate both at once if target is 'both'
            if self.target_language == 'both' and self.ai_provider in ['openai', 'gemini']:
                needs_en = not speech.ai_summary_en or self.overwrite
                needs_ru = not speech.ai_summary_ru or self.overwrite
                
                if needs_en or needs_ru:
                    translations = self.call_ai_translation(speech.ai_summary, 'both')
                    if translations:
                        if needs_en and 'en' in translations:
                            if not self.dry_run:
                                speech.ai_summary_en = translations['en']
                                translations_made = True
                            else:
                                self.stdout.write(f"English translation (DRY RUN): {translations['en'][:100]}...")
                                translations_made = True
                        if needs_ru and 'ru' in translations:
                            if not self.dry_run:
                                speech.ai_summary_ru = translations['ru']
                                translations_made = True
                            else:
                                self.stdout.write(f"Russian translation (DRY RUN): {translations['ru'][:100]}...")
                                translations_made = True
            else:
                # Fall back to separate translations for local service or single language
                # Translate to English if needed
                if self.target_language in ['en', 'both']:
                    if not speech.ai_summary_en or self.overwrite:
                        en_translation = self.call_ai_translation(speech.ai_summary, 'en')
                        if en_translation and not self.dry_run:
                            speech.ai_summary_en = en_translation
                            translations_made = True
                        elif en_translation and self.dry_run:
                            self.stdout.write(f"English translation (DRY RUN): {en_translation[:100]}...")
                            translations_made = True
                
                # Translate to Russian if needed
                if self.target_language in ['ru', 'both']:
                    if not speech.ai_summary_ru or self.overwrite:
                        ru_translation = self.call_ai_translation(speech.ai_summary, 'ru')
                        if ru_translation and not self.dry_run:
                            speech.ai_summary_ru = ru_translation
                            translations_made = True
                        elif ru_translation and self.dry_run:
                            self.stdout.write(f"Russian translation (DRY RUN): {ru_translation[:100]}...")
                            translations_made = True
            
            # Save the speech if translations were made
            if translations_made and not self.dry_run:
                speech.save(update_fields=['ai_summary_en', 'ai_summary_ru'])
            
            return translations_made
            
        except Exception as e:
            logger.exception(f"Error translating speech {speech.pk}")
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
    
    # ========================================================================
    # BATCH API HELPER METHODS
    # ========================================================================
    
    def _create_speech_translation_prompt(self, speech):
        """Create translation prompt for speech AI summary using batch API"""
        text = speech.ai_summary
        if not text:
            return None
        
        # Check if needs translation
        needs_en = self.target_language in ['en', 'both'] and (not speech.ai_summary_en or self.overwrite)
        needs_ru = self.target_language in ['ru', 'both'] and (not speech.ai_summary_ru or self.overwrite)
        
        if not needs_en and not needs_ru:
            return None  # Skip, already translated
        
        # Create prompt based on target language
        if self.target_language == 'both':
            prompt = f"""Translate the following Estonian text to English and Russian like you are a native speaker of each language. Do not summarize, translate everything.

Provide the translations in this exact format:
<en>English translation here</en>
<ru>Russian translation here</ru>

Estonian text:
{text}"""
        elif self.target_language == 'en':
            prompt = f"Translate the following Estonian text to English like you are a native English speaker. Do not summarize, translate everything. Provide only the translation, no explanations:\n\n{text}"
        elif self.target_language == 'ru':
            prompt = f"Translate the following Estonian text to Russian like you are a native Russian speaker. Do not summarize, translate everything. Provide only the translation, no explanations:\n\n{text}"
        else:
            return None
        
        return prompt
    
    def _update_speech_with_translation(self, speech, translation_text):
        """Update speech with translation from batch API"""
        if self.target_language == 'both':
            # Parse tagged translation
            translations = self.parse_tagged_translation(translation_text)
            if translations:
                if 'en' in translations:
                    speech.ai_summary_en = translations['en']
                if 'ru' in translations:
                    speech.ai_summary_ru = translations['ru']
                speech.save(update_fields=['ai_summary_en', 'ai_summary_ru'])
            else:
                logger.error(f"Failed to parse tagged translations for speech {speech.pk}")
        elif self.target_language == 'en':
            speech.ai_summary_en = translation_text
            speech.save(update_fields=['ai_summary_en'])
        elif self.target_language == 'ru':
            speech.ai_summary_ru = translation_text
            speech.save(update_fields=['ai_summary_ru'])
    
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
