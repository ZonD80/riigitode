"""
Management command to translate plenary session titles using AI providers with Batch API support
"""
import time
import logging
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed
from django.core.management.base import BaseCommand, CommandError
from django.conf import settings
from django.db import models

from parliament_speeches.models import PlenarySession
from .batch_api_mixin import GeminiBatchAPIMixin

logger = logging.getLogger(__name__)


class Command(GeminiBatchAPIMixin, BaseCommand):
    help = 'Translate plenary session titles to English and Russian using AI providers (OpenAI, Gemini, Ollama)'

    def add_arguments(self, parser):
        parser.add_argument(
            '--limit',
            type=int,
            default=None,
            help='Number of plenary sessions to process (default: all eligible sessions)'
        )
        parser.add_argument(
            '--session-id',
            type=int,
            help='Process specific plenary session by ID'
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
            help='Number of sessions to process in parallel (default: 5)'
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
                PlenarySession,
                self._update_session_with_translation
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
            if options['session_id']:
                # Process specific session
                self.process_specific_session(options['session_id'])
            else:
                # Process multiple sessions
                self.process_sessions(options['limit'])
                
            self.stdout.write(self.style.SUCCESS("Successfully completed plenary session title translation"))
            
        except Exception as e:
            logger.exception("Error during plenary session title translation")
            raise CommandError(f"Error during processing: {str(e)}")

    def process_specific_session(self, session_id):
        """Process a specific plenary session by ID"""
        try:
            session = PlenarySession.objects.get(pk=session_id)
        except PlenarySession.DoesNotExist:
            raise CommandError(f"Plenary session with ID {session_id} not found")

        if not session.title:
            raise CommandError(f"Plenary session {session_id} has no title to translate")

        self.stdout.write(f"Processing plenary session {session_id}: {session.title[:100]}...")
        success = self.translate_plenary_session(session)
        
        if success:
            self.stdout.write(self.style.SUCCESS(f"Successfully translated plenary session {session_id}"))
        else:
            self.stdout.write(self.style.ERROR(f"Failed to translate plenary session {session_id}"))

    def process_sessions(self, limit):
        """Process multiple plenary sessions"""
        # Get plenary sessions that need title translation
        queryset = PlenarySession.objects.all()
        
        if not self.overwrite:
            if self.target_language == 'en':
                queryset = queryset.filter(title_en__isnull=True)
            elif self.target_language == 'ru':
                queryset = queryset.filter(title_ru__isnull=True)
            else:  # both
                queryset = queryset.filter(
                    models.Q(title_en__isnull=True) | models.Q(title_ru__isnull=True)
                )
        
        sessions = queryset.order_by('-date')
        if limit is not None:
            sessions = sessions[:limit]
        
        if not sessions.exists():
            self.stdout.write("No plenary sessions found that need translation")
            return

        total_count = sessions.count()
        self.stdout.write(f"Found {total_count} plenary sessions to translate")
        self.stdout.write("=" * 60)
        
        self._process_items_in_batches(list(sessions), "plenary sessions", self.translate_plenary_session)

    def _process_items_in_batches(self, items_list, item_type, translate_func):
        """Generic method to process items in batches"""
        # Use Gemini Batch API if enabled
        if self.should_use_batch_api():
            self.stdout.write(self.style.HTTP_INFO(f"Using Google Gemini BATCH API for {item_type}"))
            self.stdout.write("=" * 80)
            self.process_batch_with_chunking(
                items_list,
                item_type,
                self._create_session_translation_prompt,
                self._update_session_with_translation
            )
            return
        
        # Original parallel processing logic
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
            
            # Display item info
            if hasattr(item, 'title'):
                self.stdout.write(f"Processing: {item.title[:80]}... - ID: {item.pk}")
                
                if self.verbose:
                    # Show what will be translated
                    translate_info = []
                    if self.target_language in ['en', 'both'] and (not getattr(item, 'title_en', None) or self.overwrite):
                        translate_info.append("title→EN")
                    if self.target_language in ['ru', 'both'] and (not getattr(item, 'title_ru', None) or self.overwrite):
                        translate_info.append("title→RU")
                    
                    translation_method = self.ai_provider.upper()
                    if translate_info:
                        self.stdout.write(f"   └─ {translation_method} | Tasks: {', '.join(translate_info)}")
                    else:
                        self.stdout.write(f"   └─ ⚠️  No translations needed (already exists)")
            else:
                self.stdout.write(f"Processing item ID: {item.pk}")
            
            item_start_time = time.time()
            success = translate_func(item)
            item_duration = time.time() - item_start_time
            
            if success:
                self.stdout.write(self.style.SUCCESS(f"✓ Translated session ({item_duration:.1f}s)"))
                return True
            else:
                self.stdout.write(self.style.ERROR(f"✗ Failed to translate session ({item_duration:.1f}s)"))
                return False
                
        except Exception as e:
            logger.exception(f"Error processing item {item.pk}")
            self.stdout.write(self.style.ERROR(f"✗ Error processing item {item.pk}: {str(e)}"))
            return False

    def translate_plenary_session(self, session):
        """Translate title for a plenary session"""
        try:
            translations_made = False
            
            if session.title:
                # For OpenAI and Gemini, translate both at once if target is 'both'
                if self.target_language == 'both' and self.ai_provider in ['openai', 'gemini']:
                    needs_en = not session.title_en or self.overwrite
                    needs_ru = not session.title_ru or self.overwrite
                    
                    if needs_en or needs_ru:
                        translations = self.call_ai_translation(session.title, 'both')
                        if translations:
                            if needs_en and 'en' in translations:
                                if not self.dry_run:
                                    session.title_en = translations['en']
                                    translations_made = True
                                else:
                                    self.stdout.write(f"English session title translation (DRY RUN): {translations['en'][:100]}...")
                                    translations_made = True
                            if needs_ru and 'ru' in translations:
                                if not self.dry_run:
                                    session.title_ru = translations['ru']
                                    translations_made = True
                                else:
                                    self.stdout.write(f"Russian session title translation (DRY RUN): {translations['ru'][:100]}...")
                                    translations_made = True
                else:
                    # Fall back to separate translations for local service or single language
                    if self.target_language in ['en', 'both']:
                        if not session.title_en or self.overwrite:
                            en_translation = self.call_ai_translation(session.title, 'en')
                            if en_translation and not self.dry_run:
                                session.title_en = en_translation
                                translations_made = True
                            elif en_translation and self.dry_run:
                                self.stdout.write(f"English session title translation (DRY RUN): {en_translation[:100]}...")
                                translations_made = True
                    
                    if self.target_language in ['ru', 'both']:
                        if not session.title_ru or self.overwrite:
                            ru_translation = self.call_ai_translation(session.title, 'ru')
                            if ru_translation and not self.dry_run:
                                session.title_ru = ru_translation
                                translations_made = True
                            elif ru_translation and self.dry_run:
                                self.stdout.write(f"Russian session title translation (DRY RUN): {ru_translation[:100]}...")
                                translations_made = True
            
            # Save the session if translations were made
            if translations_made and not self.dry_run:
                session.save(update_fields=['title_en', 'title_ru'])
            
            return translations_made
            
        except Exception as e:
            logger.exception(f"Error translating plenary session {session.pk}")
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
    
    def _create_session_translation_prompt(self, session):
        """Create translation prompt for plenary session using batch API"""
        text = session.title
        if not text:
            return None
        
        # Check if needs translation
        needs_en = self.target_language in ['en', 'both'] and (not session.title_en or self.overwrite)
        needs_ru = self.target_language in ['ru', 'both'] and (not session.title_ru or self.overwrite)
        
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
    
    def _update_session_with_translation(self, session, translation_text):
        """Update plenary session with translation from batch API"""
        if self.target_language == 'both':
            # Parse tagged translation
            translations = self.parse_tagged_translation(translation_text)
            if translations:
                if 'en' in translations:
                    session.title_en = translations['en']
                if 'ru' in translations:
                    session.title_ru = translations['ru']
                session.save(update_fields=['title_en', 'title_ru'])
            else:
                logger.error(f"Failed to parse tagged translations for session {session.pk}")
        elif self.target_language == 'en':
            session.title_en = translation_text
            session.save(update_fields=['title_en'])
        elif self.target_language == 'ru':
            session.title_ru = translation_text
            session.save(update_fields=['title_ru'])
    
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
