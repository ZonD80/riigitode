"""
Management command to generate AI summaries for speeches using parallel processing with Batch API support
"""
import time
import logging
import tiktoken
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed
from django.core.management.base import BaseCommand, CommandError
from django.conf import settings

from parliament_speeches.models import Speech, AgendaItem, PlenarySession, Politician
from parliament_speeches.ai_service import AIService
from .batch_api_mixin import GeminiBatchAPIMixin


logger = logging.getLogger(__name__)


class Command(GeminiBatchAPIMixin, BaseCommand):
    help = 'Generate summaries for speeches using parallel batch processing. Can process all speeches, specific speech by ID, agenda item, plenary session, or politician. Supports multiple providers (Claude, OpenAI, Ollama, Gemini).'

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.ai_service = None  # Will be initialized with provider selection

    def add_arguments(self, parser):
        parser.add_argument(
            '--batch-size',
            type=int,
            default=10,
            help='Number of speeches to process in parallel per batch (default: 10)'
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
            help='Process all speeches for specific plenary session by ID'
        )
        parser.add_argument(
            '--politician-id',
            type=int,
            help='Process all speeches for specific politician by ID'
        )
        parser.add_argument(
            '--overwrite',
            action='store_true',
            help='Overwrite existing summaries'
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Run without saving summaries to database'
        )
        parser.add_argument(
            '--ai-provider',
            type=str,
            choices=['claude', 'openai', 'ollama', 'gemini'],
            help='Provider to use (claude, openai, ollama, gemini). Default: gemini (recommended for speech summaries).'
        )
        
        # Add batch API arguments from mixin
        self.add_batch_api_arguments(parser)

    def handle(self, *args, **options):
        self.dry_run = options['dry_run']
        self.batch_size = options['batch_size']
        
        # Initialize AI service with selected provider (default to Gemini for speech summaries)
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
            self.stdout.write(self.style.SUCCESS(f"Using Provider: {provider_info['provider']} ({provider_info['model']}) [Default for Speech Summaries]"))
        
        if self.dry_run:
            self.stdout.write(self.style.WARNING("🔍 DRY RUN MODE - No summaries will be saved"))
        
        # Check if we're resuming a batch job
        if self.resume_from_batch_id:
            self.stdout.write(self.style.HTTP_INFO(f"RESUMING Google Gemini BATCH API job: {self.resume_from_batch_id}"))
            self.stdout.write("=" * 80)
            self.resume_batch_job_only(
                self.resume_from_batch_id,
                Speech,
                self._update_speech_with_summary
            )
            return
        
        self.stdout.write(self.style.SUCCESS(f"Batch Size: {self.batch_size} parallel requests per batch"))

        try:
            if options['speech_id']:
                # Process specific speech
                self.process_specific_speech(options['speech_id'], options['overwrite'])
            elif options['agenda_id']:
                # Process speeches for specific agenda item
                self.process_agenda_speeches(options['agenda_id'], options['overwrite'])
            elif options['plenary_session_id']:
                # Process speeches for specific plenary session
                self.process_plenary_session_speeches(options['plenary_session_id'], options['overwrite'])
            elif options['politician_id']:
                # Process speeches for specific politician
                self.process_politician_speeches(options['politician_id'], options['overwrite'])
            else:
                # Process multiple speeches
                self.process_speeches(options['overwrite'])
                
            self.stdout.write(self.style.SUCCESS("✅ Successfully completed AI summary generation"))
            
        except Exception as e:
            logger.exception("Error during AI summary generation")
            raise CommandError(f"Error during processing: {str(e)}")

    def process_specific_speech(self, speech_id, overwrite):
        """Process a specific speech by ID"""
        self.stdout.write(f"\n📍 Getting specific speech {speech_id}")
        
        try:
            speech = Speech.objects.get(pk=speech_id)
        except Speech.DoesNotExist:
            raise CommandError(f"Speech with ID {speech_id} not found")

        if not overwrite and speech.ai_summary:
            self.stdout.write(f"Speech {speech_id} already has summary (use --overwrite to replace)")
            return

        self.stdout.write(f"Found speech: {speech.speaker} ({speech.date.date()}) - ID: {speech.pk}")
        
        # Process as batch with single speech
        self.process_speech_batch([speech])
            
    def process_agenda_speeches(self, agenda_id, overwrite):
        """Process all speeches for a specific agenda item"""
        self.stdout.write(f"\n📍 Getting speeches for agenda item {agenda_id}")
        
        try:
            agenda = AgendaItem.objects.get(pk=agenda_id)
        except AgendaItem.DoesNotExist:
            raise CommandError(f"Agenda item with ID {agenda_id} not found")

        self.stdout.write(f"Found agenda item: {agenda.title[:100]}...")

        # Get speeches for this agenda item
        speeches = self._get_filtered_speeches(
            agenda.speeches.filter(event_type='SPEECH'), 
            overwrite
        )
        
        if not speeches:
            self.stdout.write("No speeches found that need summaries")
            return

        self.stdout.write(f"Found {len(speeches)} speeches to process")
        
        # Use Gemini Batch API if enabled
        if self.should_use_batch_api():
            self.stdout.write(self.style.HTTP_INFO(f"Using Google Gemini BATCH API for speech summaries"))
            self.stdout.write("=" * 80)
            self.process_batch_with_chunking(
                speeches,
                "speech summaries",
                self._create_speech_summary_prompt,
                self._update_speech_with_summary
            )
            return
        
        # Original parallel processing
        self.process_speech_batch(speeches)

    def process_plenary_session_speeches(self, session_id, overwrite):
        """Process all speeches for a specific plenary session"""
        self.stdout.write(f"\n📍 Getting speeches for plenary session {session_id}")
        
        try:
            session = PlenarySession.objects.get(pk=session_id)
        except PlenarySession.DoesNotExist:
            raise CommandError(f"Plenary session with ID {session_id} not found")

        self.stdout.write(f"Found plenary session: {session.title[:100]}... ({session.date.date()})")

        # Get all speeches from all agenda items in this session
        speeches_queryset = Speech.objects.filter(
            agenda_item__plenary_session=session,
            event_type='SPEECH'
        ).select_related('politician', 'agenda_item')
        
        speeches = self._get_filtered_speeches(speeches_queryset, overwrite)
        
        if not speeches:
            self.stdout.write("No speeches found that need summaries")
            return

        self.stdout.write(f"Found {len(speeches)} speeches to process across all agenda items")
        
        # Use Gemini Batch API if enabled
        if self.should_use_batch_api():
            self.stdout.write(self.style.HTTP_INFO(f"Using Google Gemini BATCH API for speech summaries"))
            self.stdout.write("=" * 80)
            self.process_batch_with_chunking(
                speeches,
                "speech summaries",
                self._create_speech_summary_prompt,
                self._update_speech_with_summary
            )
            return
        
        # Original parallel processing
        self.process_speech_batch(speeches)

    def process_politician_speeches(self, politician_id, overwrite):
        """Process all speeches for a specific politician"""
        self.stdout.write(f"\n📍 Getting speeches for politician {politician_id}")
        
        try:
            politician = Politician.objects.get(pk=politician_id)
        except Politician.DoesNotExist:
            raise CommandError(f"Politician with ID {politician_id} not found")

        self.stdout.write(f"Found politician: {politician.full_name}")

        # Get speeches for this politician
        speeches_queryset = politician.speeches.filter(event_type='SPEECH').select_related('politician', 'agenda_item')
        speeches = self._get_filtered_speeches(speeches_queryset, overwrite)
        
        if not speeches:
            self.stdout.write("No speeches found that need summaries")
            return

        self.stdout.write(f"Found {len(speeches)} speeches to process")
        
        # Use Gemini Batch API if enabled
        if self.should_use_batch_api():
            self.stdout.write(self.style.HTTP_INFO(f"Using Google Gemini BATCH API for speech summaries"))
            self.stdout.write("=" * 80)
            self.process_batch_with_chunking(
                speeches,
                "speech summaries",
                self._create_speech_summary_prompt,
                self._update_speech_with_summary
            )
            return
        
        # Original parallel processing
        self.process_speech_batch(speeches)

    def process_speeches(self, overwrite):
        """Process multiple speeches"""
        self.stdout.write(f"\n📍 Getting all speeches")
        
        # Get speeches that need summaries
        speeches_queryset = Speech.objects.filter(event_type='SPEECH').select_related('politician', 'agenda_item')
        speeches = self._get_filtered_speeches(speeches_queryset, overwrite)
        
        if not speeches:
            self.stdout.write("No speeches found that need summaries")
            return

        self.stdout.write(f"Found {len(speeches)} speeches to process")
        
        # Use Gemini Batch API if enabled
        if self.should_use_batch_api():
            self.stdout.write(self.style.HTTP_INFO(f"Using Google Gemini BATCH API for speech summaries"))
            self.stdout.write("=" * 80)
            self.process_batch_with_chunking(
                speeches,
                "speech summaries",
                self._create_speech_summary_prompt,
                self._update_speech_with_summary
            )
            return
        
        # Original parallel processing
        self.process_speech_batch(speeches)

    def _get_filtered_speeches(self, queryset, overwrite):
        """Filter speeches based on overwrite flag"""
        # Always exclude incomplete speeches (stenogram being prepared)
        queryset = queryset.filter(is_incomplete=False)
        
        if not overwrite:
            queryset = queryset.filter(ai_summary__isnull=True)
        
        # Order by date to process newer speeches first
        speeches = list(queryset.order_by('-date'))
        
        return speeches

    def process_speech_batch(self, speeches):
        """Process speeches in parallel batches with auto-retry until all are processed"""
        original_speech_count = len(speeches)
        remaining_speeches = speeches.copy()
        attempt = 1
        
        try:
            while remaining_speeches:
                self.stdout.write(f"\n{'🔄 RETRY ' + str(attempt) if attempt > 1 else '📍 ATTEMPT 1'}: Processing {len(remaining_speeches)} speeches")
                
                # Safety check: if we've been trying for too long without progress, ask user
                if attempt > 10:
                    processed_count = original_speech_count - len(remaining_speeches)
                    self.stdout.write(f"⚠️  After {attempt-1} attempts: {processed_count}/{original_speech_count} speeches completed")
                    
                    if not self.dry_run:
                        response = input("Continue trying? (Y/N): ").strip().upper()
                        if response not in ['Y', 'YES']:
                            self.stdout.write("❌ Processing stopped by user")
                            break
                    else:
                        self.stdout.write("🔍 DRY RUN: Would ask user to continue after 10 attempts")
                
                # Estimate tokens and ask for confirmation (only on first attempt)
                if attempt == 1:
                    total_tokens = self._estimate_batch_tokens(remaining_speeches)
                    if not self._get_user_confirmation(total_tokens, len(remaining_speeches)):
                        self.stdout.write("❌ Operation cancelled by user")
                        raise KeyboardInterrupt("User cancelled operation")
                
                # Process this batch
                success_count = self._process_parallel_batches(remaining_speeches)
                
                self.stdout.write(f"✅ Successfully processed {success_count} speeches in this attempt")
                
                # Check which speeches still need processing
                remaining_speeches = self._get_unprocessed_speeches(speeches)
                
                if not remaining_speeches:
                    self.stdout.write(f"🎉 All speeches have been processed!")
                    break
                else:
                    processed_count = original_speech_count - len(remaining_speeches)
                    self.stdout.write(f"📊 Progress: {processed_count}/{original_speech_count} speeches completed")
                    self.stdout.write(f"🔄 Continuing with remaining {len(remaining_speeches)} speeches...")
                
                attempt += 1
                
                # Add a small delay between retries
                if remaining_speeches:
                    self.stdout.write(f"⏳ Waiting 2 seconds before retry...")
                    time.sleep(2)
        
        except KeyboardInterrupt:
            self.stdout.write(f"\n❌ Operation cancelled by user")
            processed_count = original_speech_count - len(remaining_speeches)
            if processed_count > 0:
                self.stdout.write(f"📊 Partial progress: {processed_count}/{original_speech_count} speeches were completed before cancellation")
            return
        
        # Final summary
        self.stdout.write(f"\n📍 FINAL PROCESSING SUMMARY")
        self._show_final_processing_summary(speeches)

    def _process_parallel_batches(self, speeches):
        """Process speeches in parallel batches"""
        total_speeches = len(speeches)
        success_count = 0
        
        # Split speeches into batches
        for batch_start in range(0, total_speeches, self.batch_size):
            batch_end = min(batch_start + self.batch_size, total_speeches)
            batch = speeches[batch_start:batch_end]
            
            self.stdout.write(f"\n📦 Processing batch {batch_start//self.batch_size + 1}: speeches {batch_start+1}-{batch_end} of {total_speeches}")
            
            # Process this batch in parallel
            batch_success = self._process_single_parallel_batch(batch)
            success_count += batch_success
            
            self.stdout.write(f"   ✅ Batch complete: {batch_success}/{len(batch)} successful")
        
        return success_count

    def _process_single_parallel_batch(self, batch):
        """Process a single batch of speeches in parallel"""
        success_count = 0
        
        # Use ThreadPoolExecutor for parallel processing
        with ThreadPoolExecutor(max_workers=len(batch)) as executor:
            # Submit all speeches in this batch
            future_to_speech = {
                executor.submit(self._process_single_speech, speech): speech 
                for speech in batch
            }
            
            # Process results as they complete
            for future in as_completed(future_to_speech):
                speech = future_to_speech[future]
                try:
                    success = future.result()
                    if success:
                        success_count += 1
                        self.stdout.write(f"      ✅ Speech {speech.pk}: {speech.speaker[:30]}...")
                    else:
                        self.stdout.write(f"      ❌ Speech {speech.pk}: Failed")
                except Exception as e:
                    logger.exception(f"Error processing speech {speech.pk}")
                    self.stdout.write(f"      ❌ Speech {speech.pk}: Error - {str(e)}")
        
        return success_count

    def _process_single_speech(self, speech):
        """Process a single speech and return success status"""
        try:
            # Skip incomplete speeches (stenogram being prepared)
            if speech.is_incomplete:
                return False
            
            if not speech.text or not speech.text.strip():
                return False
            
            # Generate prompt for this speech
            prompt = f"""Please write a short summary of the following speech, one sentence or paragraph max, in Estonian language, speak like native estonian, start with "Sõnavõtja".

Speech text:
{speech.text}

Provide the summary in Estonian, starting with "Sõnavõtja", wrapped in <summary></summary> tags.

Format:
<summary>Sõnavõtja ...</summary>"""

            if self.dry_run:
                # Mock processing
                ai_response = "<summary>Sõnavõtja rääkis teemal ja tegi ettepaneku</summary>"
            else:
                # Send to AI
                ai_response = self.ai_service.generate_summary(prompt, max_tokens=8000, temperature=0.3)
                
                if not ai_response:
                    return False
            
            # Parse summary from XML tags
            import re
            summary_match = re.search(r'<summary>(.*?)</summary>', ai_response.strip(), re.DOTALL)
            if not summary_match:
                # Fallback: if no tags found, use the entire response
                logger.warning(f"No <summary> tags found in response for speech {speech.pk}, using full response")
                summary = ai_response.strip()
            else:
                summary = summary_match.group(1).strip()
            
            # Replace "Sõnavõtja" with actual speaker name
            if summary.startswith("Sõnavõtja "):
                # Remove "Sõnavõtja " and replace with speaker name
                remaining_text = summary[10:]  # "Sõnavõtja " is 10 characters
                summary = f"{speech.speaker} {remaining_text}"
            elif summary.startswith("Sõnavõtja"):
                # Handle case without space after "Sõnavõtja"
                remaining_text = summary[9:]  # "Sõnavõtja" is 9 characters
                summary = f"{speech.speaker}{remaining_text}"
            
            # Save to database if not dry run
            if not self.dry_run:
                from django.utils import timezone
                # Clear translations if content changed
                update_fields = ['ai_summary', 'ai_summary_generated_at']
                if speech.ai_summary != summary:
                    speech.ai_summary_en = None
                    speech.ai_summary_ru = None
                    update_fields.extend(['ai_summary_en', 'ai_summary_ru'])
                
                speech.ai_summary = summary
                speech.ai_summary_generated_at = timezone.now()
                speech.save(update_fields=update_fields)
            
            return True
            
        except (ConnectionError, TimeoutError, requests.exceptions.RequestException) as e:
            logger.warning(f"Network error processing speech {speech.pk}: {str(e)}")
            return False
        except Exception as e:
            logger.exception(f"Error processing speech {speech.pk}")
            return False

    def _get_unprocessed_speeches(self, original_speeches):
        """Get list of speeches that still need processing"""
        unprocessed = []
        for speech in original_speeches:
            # Refresh from database to get latest state
            speech.refresh_from_db()
            if not speech.ai_summary:
                unprocessed.append(speech)
        return unprocessed

    def _estimate_batch_tokens(self, speeches):
        """Estimate total tokens for all speeches"""
        try:
            # Use tiktoken for estimation
            encoding = tiktoken.get_encoding("cl100k_base")
            
            total_tokens = 0
            for speech in speeches:
                if speech.text:
                    # Count tokens for speech text + prompt overhead (~50 tokens)
                    tokens = len(encoding.encode(speech.text)) + 50
                    total_tokens += tokens
            
            return total_tokens
        except Exception as e:
            logger.warning(f"Failed to count tokens with tiktoken: {e}")
            # Fallback to word count approximation
            total_words = sum(len(s.text.split()) if s.text else 0 for s in speeches)
            return int(total_words * 1.3)  # Rough approximation

    def _get_user_confirmation(self, token_count, speech_count):
        """Ask user for confirmation before sending to AI"""
        self.stdout.write(f"\n🤔 CONFIRMATION REQUIRED")
        self.stdout.write(f"   Speeches to process: {speech_count}")
        self.stdout.write(f"   Batch size: {self.batch_size} parallel requests")
        self.stdout.write(f"   Total batches: {(speech_count + self.batch_size - 1) // self.batch_size}")
        self.stdout.write(f"   Estimated tokens: {token_count:,}")
        
        # Estimate cost based on current pricing
        provider_info = self.ai_service.get_provider_info()
        if provider_info['provider'] == 'claude':
            # Claude Sonnet 4 pricing (standard mode)
            if token_count <= 200_000:
                input_cost = (token_count / 1_000_000) * 3.00  # $3/MTok for ≤200K tokens
                output_cost_per_mtok = 15.00  # $15/MTok for output
            else:
                input_cost = (token_count / 1_000_000) * 6.00  # $6/MTok for >200K tokens  
                output_cost_per_mtok = 22.50  # $22.50/MTok for output
            
            # Estimate output tokens (typically 10-20 tokens per summary)
            estimated_output_tokens = speech_count * 15  # Conservative estimate
            output_cost = (estimated_output_tokens / 1_000_000) * output_cost_per_mtok
            total_cost = input_cost + output_cost
            
            self.stdout.write(f"   Estimated cost (Standard): ~${total_cost:.4f}")
            
        elif provider_info['provider'] == 'openai':
            # OpenAI GPT-4o pricing (approximate)
            input_cost = (token_count / 1_000_000) * 2.50  # ~$2.50/MTok input
            estimated_output_tokens = speech_count * 15
            output_cost = (estimated_output_tokens / 1_000_000) * 10.00  # ~$10/MTok output
            total_cost = input_cost + output_cost
            self.stdout.write(f"   Estimated cost: ~${total_cost:.4f}")
        elif provider_info['provider'] == 'ollama':
            self.stdout.write(f"   Cost: Free (local model)")
        elif provider_info['provider'] == 'gemini':
            self.stdout.write(f"   Cost: Variable (check Gemini pricing)")
        
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

    def _show_final_processing_summary(self, speeches):
        """Show final summary of processed speeches"""
        # Count how many speeches now have summaries
        processed_count = 0
        for speech in speeches:
            # Refresh from database to get latest state
            speech.refresh_from_db()
            if speech.ai_summary:
                processed_count += 1
        
        self.stdout.write(f"\n📊 FINAL SUMMARY:")
        self.stdout.write(f"   ✅ Speeches with summaries: {processed_count}/{len(speeches)}")
        
        if processed_count == len(speeches):
            self.stdout.write(self.style.SUCCESS(f"   🎉 All speeches processed successfully!"))
        elif processed_count > 0:
            remaining = len(speeches) - processed_count
            self.stdout.write(self.style.WARNING(f"   ⚠️  {remaining} speeches still need processing"))
        else:
            self.stdout.write(self.style.ERROR(f"   ❌ No speeches were processed"))
        
        if self.dry_run:
            self.stdout.write(self.style.WARNING("   (DRY RUN - no summaries were actually saved)"))
    
    # ========================================================================
    # BATCH API HELPER METHODS
    # NOTE: Simplified implementation for batch API
    # ========================================================================
    
    def _create_speech_summary_prompt(self, speech):
        """Create summary generation prompt for speech using batch API"""
        # Skip incomplete speeches (stenogram being prepared)
        if speech.is_incomplete or not speech.text:
            return None
        
        prompt = f"""Please write a short summary of the following speech, one sentence or paragraph max, in Estonian language, speak like native estonian, start with "Sõnavõtja".

Speech text:
{speech.text}

Provide the summary in Estonian, starting with "Sõnavõtja", wrapped in <summary></summary> tags.

Format:
<summary>Sõnavõtja ...</summary>"""
        
        return prompt
    
    def _update_speech_with_summary(self, speech, summary_text):
        """Update speech with AI-generated summary from batch API"""
        try:
            # Parse summary from XML tags
            import re
            summary_match = re.search(r'<summary>(.*?)</summary>', summary_text.strip(), re.DOTALL)
            if not summary_match:
                # Fallback: if no tags found, use the entire response
                logger.warning(f"No <summary> tags found in response for speech {speech.pk}, using full response")
                summary = summary_text.strip()
            else:
                summary = summary_match.group(1).strip()
            
            # Replace "Sõnavõtja" with actual speaker name
            if summary.startswith("Sõnavõtja "):
                # Remove "Sõnavõtja " and replace with speaker name
                remaining_text = summary[10:]  # "Sõnavõtja " is 10 characters
                summary = f"{speech.speaker} {remaining_text}"
            elif summary.startswith("Sõnavõtja"):
                # Handle case without space after "Sõnavõtja"
                remaining_text = summary[9:]  # "Sõnavõtja" is 9 characters
                summary = f"{speech.speaker}{remaining_text}"
            
            # Save to database
            from django.utils import timezone
            # Clear translations if content changed
            update_fields = ['ai_summary', 'ai_summary_generated_at']
            if speech.ai_summary != summary:
                speech.ai_summary_en = None
                speech.ai_summary_ru = None
                update_fields.extend(['ai_summary_en', 'ai_summary_ru'])
            
            speech.ai_summary = summary
            speech.ai_summary_generated_at = timezone.now()
            speech.save(update_fields=update_fields)
            
            return True
        except Exception as e:
            logger.exception(f"Error updating speech {speech.pk} with batch API summary")
            return False