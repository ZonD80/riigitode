"""
Management command to generate AI summaries for agenda items using batch XML processing with encrypted politician IDs
Supports Gemini Batch API for cost-effective processing.
"""
import logging
import hashlib
import base64
import secrets
from xml.sax.saxutils import escape
import re
import tiktoken
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from django.core.management.base import BaseCommand, CommandError
from django.db import models

from parliament_speeches.models import AgendaItem, AgendaSummary, AgendaDecision, AgendaActivePolitician, Politician
from parliament_speeches.ai_service import AIService
from .batch_api_mixin import GeminiBatchAPIMixin


logger = logging.getLogger(__name__)


class Command(GeminiBatchAPIMixin, BaseCommand):
    help = 'Generate summaries for agenda items using individual per-agenda requests in batches. Supports multiple providers (Claude, OpenAI, Ollama).'

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.ai_service = None  # Will be initialized with provider selection
        self.session_key = None
        self.politician_id_mapping = {}  # encrypted_id -> real_politician_id
        self.politician_reverse_mapping = {}        # real_politician_id -> encrypted_id
        self.agenda_id_mapping = {}      # encrypted_id -> real_agenda_id
        self.agenda_reverse_mapping = {}  # real_agenda_id -> encrypted_id
        self._auto_approve_remaining = False

    def add_arguments(self, parser):
        parser.add_argument(
            '--limit',
            type=int,
            default=None,
            help='Number of agenda items to process (default: all eligible agendas)'
        )
        parser.add_argument(
            '--agenda-id',
            type=int,
            help='Process specific agenda item by ID'
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
            '--delay',
            type=float,
            default=1.0,
            help='Delay between API calls in seconds (default: 1.0)'
        )
        parser.add_argument(
            '--batch-size',
            type=int,
            default=10,
            help='Number of agendas to process in parallel per batch (default: 10)'
        )
        parser.add_argument(
            '--ai-provider',
            type=str,
            choices=['claude', 'openai', 'ollama', 'gemini'],
            help='Provider to use (claude, openai, ollama, gemini). Default: gemini (recommended for agenda summaries).'
        )
        
        # Add batch API arguments from mixin
        self.add_batch_api_arguments(parser)

    def handle(self, *args, **options):
        self.dry_run = options['dry_run']
        self.delay = options['delay']
        self.batch_size = options['batch_size']
        
        # Initialize AI service with selected provider (default to Gemini for agenda summaries)
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
            self.stdout.write(self.style.SUCCESS(f"Using Provider: {provider_info['provider']} ({provider_info['model']}) [Default for Agenda Summaries]"))
        
        if self.dry_run:
            self.stdout.write(self.style.WARNING("🔍 DRY RUN MODE - No summaries will be saved"))
        
        # Check if we're resuming a batch job
        if self.resume_from_batch_id:
            self.stdout.write(self.style.HTTP_INFO(f"RESUMING Google Gemini BATCH API job: {self.resume_from_batch_id}"))
            self.stdout.write("=" * 80)
            self.resume_batch_job_only(
                self.resume_from_batch_id,
                AgendaItem,
                self._update_agenda_with_summary
            )
            return

        self.stdout.write(f"🚀 Parallel processing: {self.batch_size} agendas per batch")
        self.stdout.write(f"⏱️  Delay between batches: {self.delay}s")

        # Skip API connection test to save tokens

        # Generate session key for encryption
        self.session_key = secrets.token_bytes(16)
        self.stdout.write(f"🔐 Generated session encryption key: {self.session_key.hex()[:16]}...")

        try:
            if options['agenda_id']:
                # Process specific agenda
                self.process_specific_agenda(options['agenda_id'], options['overwrite'])
            else:
                # Process multiple agendas in batches
                self.process_agendas_in_batches(options['limit'], options['overwrite'])
                
            self.stdout.write(self.style.SUCCESS("✅ Successfully completed AI agenda summary generation"))
            
        except Exception as e:
            logger.exception("Error during AI agenda summary generation")
            raise CommandError(f"Error during processing: {str(e)}")

    def process_specific_agenda(self, agenda_id, overwrite):
        """Process a specific agenda by ID"""
        self.stdout.write(f"\n📍 STEP 1: Getting specific agenda {agenda_id}")
        
        try:
            agenda = AgendaItem.objects.get(pk=agenda_id)
        except AgendaItem.DoesNotExist:
            raise CommandError(f"Agenda item with ID {agenda_id} not found")

        if not overwrite:
            try:
                if agenda.structured_summary:
                    self.stdout.write(f"Agenda {agenda_id} already has structured summary (use --overwrite to replace)")
                    return
            except AgendaSummary.DoesNotExist:
                # No summary exists, continue processing
                pass

        self.stdout.write(f"Found agenda: {agenda.title[:100]}...")
        
        # Process as single agenda
        self.process_single_agenda(agenda)

    def process_agendas_in_batches(self, limit, overwrite):
        """Process multiple agenda items in batches"""
        self.stdout.write(f"\n📍 STEP 1: Getting agenda items (limit: {limit})")
        
        # Get agenda items that have speeches
        queryset = AgendaItem.objects.filter(
            speeches__isnull=False
        ).distinct().select_related('plenary_session').prefetch_related('speeches')
        
        total_with_speeches = queryset.count()
        self.stdout.write(f"📊 Total agendas with speeches: {total_with_speeches}")
        
        if not overwrite:
            # Include agendas without summaries OR agendas with summaries but no decisions/activity (failed processing)
            # OR agendas where:
            # 1) Any speeches were parsed after the summary was generated
            # AND
            # 2) The agenda has no incomplete speeches
            
            # First, get agendas with speeches parsed after summary
            agendas_with_new_speeches = queryset.filter(
                structured_summary__isnull=False,
                speeches__parsed_at__gt=models.F('structured_summary__ai_summary_generated_at')
            ).distinct()
            
            # From those, exclude agendas that have any incomplete speeches
            agendas_with_new_complete_speeches = agendas_with_new_speeches.exclude(
                speeches__is_incomplete=True
            ).distinct()
            
            # Combine all conditions
            queryset = queryset.filter(
                models.Q(structured_summary__isnull=True) | 
                models.Q(structured_summary__isnull=False, decisions__isnull=True) |
                models.Q(structured_summary__isnull=False, active_politician__isnull=True) |
                models.Q(pk__in=agendas_with_new_complete_speeches.values('pk'))
            ).distinct()
            
            total_without_summaries = queryset.filter(structured_summary__isnull=True).count()
            total_without_decisions = queryset.filter(structured_summary__isnull=False, decisions__isnull=True).count()
            total_without_activity = queryset.filter(structured_summary__isnull=False, active_politician__isnull=True).count()
            total_with_new_speeches = agendas_with_new_complete_speeches.count()
            self.stdout.write(f"📊 Agendas without summaries: {total_without_summaries}")
            self.stdout.write(f"📊 Agendas with summaries but without decisions (failed): {total_without_decisions}")
            self.stdout.write(f"📊 Agendas with summaries but without active politician (failed): {total_without_activity}")
            self.stdout.write(f"📊 Agendas with speeches parsed after last summary (and no incomplete speeches): {total_with_new_speeches}")
        
        # Order by date to process newer agendas first
        agendas = list(queryset.order_by('-date')[:limit] if limit else queryset.order_by('-date'))
        
        if not agendas:
            self.stdout.write("No agenda items found that need summaries")
            return

        total_agendas = len(agendas)
        self.stdout.write(f"Found {total_agendas} agenda items to process")
        
        # Use Gemini Batch API if enabled
        if self.should_use_batch_api():
            self.stdout.write(self.style.HTTP_INFO(f"Using Google Gemini BATCH API for agenda summaries"))
            self.stdout.write("=" * 80)
            self.process_batch_with_chunking(
                agendas,
                "agenda summaries",
                self._create_agenda_summary_prompt,
                self._update_agenda_with_summary
            )
            return
        
        # Process agendas in batches (original parallel processing)
        total_processed = 0
        total_batches = (total_agendas + self.batch_size - 1) // self.batch_size
        
        for batch_num in range(total_batches):
            start_idx = batch_num * self.batch_size
            end_idx = min(start_idx + self.batch_size, total_agendas)
            batch_agendas = agendas[start_idx:end_idx]
            
            self.stdout.write(f"\n🔄 BATCH {batch_num + 1}/{total_batches}: Processing agendas {start_idx + 1}-{end_idx} of {total_agendas}")
            
            batch_processed = self.process_agenda_batch(batch_agendas)
            total_processed += batch_processed
            
            # Add delay between batches (except for the last batch)
            if batch_num < total_batches - 1 and self.delay > 0:
                import time
                self.stdout.write(f"⏱️  Waiting {self.delay}s before next batch...")
                time.sleep(self.delay)
        
        self.stdout.write(f"\n📊 FINAL SUMMARY: Processed {total_processed}/{total_agendas} agendas across {total_batches} batches")
    
    def process_agenda_batch(self, agendas):
        """Process a batch of agenda items in parallel"""
        processed_count = 0
        
        self.stdout.write(f"🚀 Processing {len(agendas)} agendas in parallel...")
        
        # Use ThreadPoolExecutor to process agendas in parallel
        with ThreadPoolExecutor(max_workers=self.batch_size) as executor:
            # Submit all agendas for processing
            future_to_agenda = {
                executor.submit(self.process_single_agenda, agenda): agenda 
                for agenda in agendas
            }
            
            # Process completed futures as they finish
            for future in as_completed(future_to_agenda):
                agenda = future_to_agenda[future]
                try:
                    success = future.result()
                    if success:
                        processed_count += 1
                        self.stdout.write(f"  ✅ Completed agenda {agenda.pk}: {agenda.title[:50]}...")
                    else:
                        self.stdout.write(f"  ❌ Failed agenda {agenda.pk}: {agenda.title[:50]}...")
                        
                except Exception as e:
                    logger.exception(f"Error processing agenda {agenda.pk}")
                    self.stdout.write(self.style.ERROR(f"  ❌ Exception in agenda {agenda.pk}: {str(e)}"))
        
        self.stdout.write(f"📊 Batch complete: {processed_count}/{len(agendas)} agendas processed successfully")
        return processed_count
    
    def process_single_agenda(self, agenda):
        """Process a single agenda item"""
        try:
            # Generate encrypted IDs for this agenda and its politicians
            self._generate_encrypted_ids_for_agenda(agenda)
            
            # Generate XML for single agenda
            xml_content = self._generate_single_agenda_xml(agenda)
            
            # Count tokens
            token_count = self._count_tokens(xml_content)
            
            # Get confirmation for high token count agendas
            if not self._should_process_agenda(token_count, agenda):
                return False
            
            # Send to AI
            ai_response = self._send_single_agenda_request(xml_content, agenda)
            
            if ai_response:
                return True
            else:
                return False
                
        except Exception as e:
            logger.exception(f"Error processing single agenda {agenda.pk}")
            return False
    
    def _generate_encrypted_ids_for_agenda(self, agenda):
        """Generate encrypted IDs for a single agenda and its politicians"""
        # Generate encrypted agenda ID
        encrypted_agenda_id = self._encrypt_agenda_id(agenda.pk)
        self.agenda_id_mapping[encrypted_agenda_id] = agenda.pk
        self.agenda_reverse_mapping[agenda.pk] = encrypted_agenda_id
        
        # Collect unique politicians from this agenda's speeches (exclude incomplete speeches)
        politicians = set()
        speeches = agenda.speeches.filter(event_type='SPEECH', politician__isnull=False, is_incomplete=False)
        for speech in speeches:
            if speech.politician:
                politicians.add(speech.politician.pk)
        
        # Generate encrypted IDs for politicians (only if not already done)
        for politician_id in politicians:
            if politician_id not in self.politician_reverse_mapping:
                encrypted_id = self._encrypt_politician_id(politician_id)
                self.politician_id_mapping[encrypted_id] = politician_id
                self.politician_reverse_mapping[politician_id] = encrypted_id
    
    def _generate_single_agenda_xml(self, agenda):
        """Generate XML document for a single agenda"""
        xml_lines = ['<?xml version="1.0" encoding="UTF-8"?>']
        
        # Use encrypted agenda ID
        encrypted_agenda_id = self.agenda_reverse_mapping.get(agenda.pk, "")
        xml_lines.append(f'<agenda id="{encrypted_agenda_id}">')
        
        # Get all speeches for this agenda item, excluding incomplete ones
        speeches = agenda.speeches.filter(event_type='SPEECH', is_incomplete=False).order_by('date')
        
        for speech in speeches:
            if not speech.text or not speech.text.strip():
                continue
            
            # Get encrypted politician ID or empty if no politician
            encrypted_pid = ""
            if speech.politician:
                encrypted_pid = self.politician_reverse_mapping.get(speech.politician.pk, "")
            
            # Escape XML special characters in text
            escaped_text = escape(speech.text)
            xml_lines.append(f'  <speech pid="{encrypted_pid}">{escaped_text}</speech>')
        
        xml_lines.append('</agenda>')
        return '\n'.join(xml_lines)
    
    def _should_process_agenda(self, token_count, agenda):
        """Determine if agenda should be processed based on token count and user preference"""
        if self.dry_run:
            return True
            
        # Auto-approve if flag is set
        if self._auto_approve_remaining:
            return True
            
        # Auto-approve if token count is reasonable (under 50k tokens)
        if token_count < 50000:
            return True
            
        # For parallel processing, we can't do interactive prompts
        # So we auto-approve high token agendas but log a warning
        logger.warning(f"High token count ({token_count:,}) for agenda {agenda.pk}: {agenda.title[:50]}... - Auto-approving for parallel processing")
        return True
    
    def _send_single_agenda_request(self, xml_content, agenda):
        """Send XML content for single agenda to AI and get response"""
        if self.dry_run:
            return self._generate_single_agenda_mock_response(agenda)

        # Create prompt for single agenda
        prompt = f"""Please write a detailed report of the following agenda in Estonian language, speak like native estonian. Provide response in the EXACT structured XML format shown below.

INPUT DATA:
{xml_content}

CRITICAL REQUIREMENTS:
1. ALL tags (summary, decisions, activity) MUST be INSIDE the <agenda> tag
2. You MUST include at least one <decision> tag (even if no decisions were made)
3. If no decisions were made, write: <decision pid="">Otsuseid ei tehtud</decision>
4. You MUST include an <activity> tag (even if no one was particularly active)
5. If no politician was particularly active, write: <activity pid="">Ei olnud eriti aktiivset kõnelejat</activity>
6. The response must be valid XML with proper nesting
7. Do NOT output <speech> tags - only output <agenda>, <summary>, <decisions>, <decision>, and <activity> tags

REQUIRED RESPONSE FORMAT (copy this structure EXACTLY):
<agenda id="{{agenda_id}}">
<summary>{{Write a detailed summary of the agenda in Estonian, couple paragraphs max}}</summary>
<decisions>
<decision pid="{{politician_id or empty string}}">{{Describe what decisions were made. If no decisions, write "Otsuseid ei tehtud" with empty pid}}</decision>
</decisions>
<activity pid="{{politician_id or empty string}}">{{Describe the most active speaker and their position (vasak, parem or muu). If no one was particularly active, write "Ei olnud eriti aktiivset kõnelejat" with empty pid}}</activity>
</agenda>

IMPORTANT: The closing </agenda> tag must come AFTER all other tags (summary, decisions, activity)."""

        try:
            # Use non-streaming API to wait for full response
            # Note: Gemini 2.5 Flash supports up to 8192 output tokens, Claude supports up to 4096
            # Using 8000 to allow for larger agenda summaries
            response = self.ai_service.generate_summary(prompt, max_tokens=8000, temperature=0.3)
            
            # Debug: Log the raw response
            if response is None:
                logger.error(f"AI service returned None response for agenda {agenda.pk}")
                self.stdout.write(self.style.ERROR(f"❌ AI service returned None response for agenda {agenda.pk}"))
                return None
            
            # Debug: Log response length and first 500 chars
            logger.info(f"AI response for agenda {agenda.pk}: {len(response)} chars")
            logger.info(f"AI response preview: {response[:500]}...")
            self.stdout.write(f"📝 AI response length: {len(response)} chars")
            
            # Parse and save the response for single agenda
            self._parse_and_update_single_agenda(response, agenda)
            
            return response
            
        except Exception as e:
            provider_info = self.ai_service.get_provider_info()
            logger.exception(f"Error calling {provider_info['provider']} API for agenda {agenda.pk}")
            self.stdout.write(self.style.ERROR(f"❌ API Error for agenda {agenda.pk}: {str(e)}"))
            return None
    
    def _generate_single_agenda_mock_response(self, agenda):
        """Generate mock response for single agenda in dry run"""
        encrypted_agenda_id = self.agenda_reverse_mapping.get(agenda.pk, "")
        
        # Get a sample politician ID for mock response (exclude incomplete speeches)
        sample_politician = ""
        speeches = agenda.speeches.filter(event_type='SPEECH', politician__isnull=False, is_incomplete=False).first()
        if speeches and speeches.politician:
            sample_politician = self.politician_reverse_mapping.get(speeches.politician.pk, "")
        
        return f'''<agenda id="{encrypted_agenda_id}">
<summary>Mock kokkuvõte päevakorrapunktist - arutati erinevaid küsimusi ja tehti ettepanekuid.</summary>
<decisions>
<decision pid="{sample_politician}">Mock otsus - otsustati jätkata arutelu järgmisel istungil</decision>
</decisions>
<activity pid="{sample_politician}">Mock aktiivseim kõneleja - oli väga aktiivne ja esindas vasakpoolseid seisukohti</activity>
</agenda>'''
    
    def _parse_and_update_single_agenda(self, ai_response, agenda):
        """Parse AI response and save structured data for single agenda"""
        try:
            # Debug: Check if response is None
            if ai_response is None:
                logger.error(f"AI response is None for agenda {agenda.pk}")
                self.stdout.write(self.style.ERROR(f"❌ AI response is None for agenda {agenda.pk}"))
                return False
            
            # Debug: Log the raw response for debugging
            logger.info(f"Raw AI response for agenda {agenda.pk}: {ai_response}")
            self.stdout.write(f"🔍 Raw AI response: {ai_response[:200]}...")
            
            # First, create XML with decrypted IDs for storage
            decrypted_xml = self._decrypt_xml_response(ai_response)
            
            # Extract agenda section with encrypted ID
            agenda_pattern = r'<agenda id="([^"]*)">(.*?)</agenda>'
            agenda_match = re.search(agenda_pattern, ai_response, re.DOTALL)
            
            if not agenda_match:
                logger.error(f"Could not parse AI response for agenda {agenda.pk}")
                logger.error(f"Response content: {ai_response}")
                self.stdout.write(self.style.ERROR(f"❌ Could not parse AI response for agenda {agenda.pk}"))
                self.stdout.write(f"Response content: {ai_response}")
                return False
            
            encrypted_agenda_id, agenda_content = agenda_match.groups()
            
            # Verify this matches our expected agenda
            expected_encrypted_id = self.agenda_reverse_mapping.get(agenda.pk, "")
            if encrypted_agenda_id != expected_encrypted_id:
                logger.warning(f"Agenda ID mismatch for {agenda.pk}: expected {expected_encrypted_id}, got {encrypted_agenda_id}")
            
            if not self.dry_run:
                # Save structured data and check if decisions and activity were saved
                decisions_saved, activity_saved = self._save_structured_data(agenda, agenda_content, decrypted_xml)
                
                # Check if both required fields were saved
                if not decisions_saved or not activity_saved:
                    missing = []
                    if not decisions_saved:
                        missing.append("decisions")
                    if not activity_saved:
                        missing.append("active politician")
                    
                    missing_str = " and ".join(missing)
                    logger.error(f"Failed to save {missing_str} for agenda {agenda.pk}. Marking as failed.")
                    self.stdout.write(self.style.ERROR(f"❌ Failed: No {missing_str} saved for agenda {agenda.pk}"))
                    return False
            
            return True
            
        except Exception as e:
            logger.exception(f"Error parsing AI response for agenda {agenda.pk}")
            logger.error(f"AI response that caused error: {ai_response}")
            self.stdout.write(self.style.ERROR(f"❌ Error parsing AI response for agenda {agenda.pk}: {str(e)}"))
            self.stdout.write(f"Response that caused error: {ai_response}")
            return False
    
    def _encrypt_agenda_id(self, agenda_id):
        """Create a reversible encrypted ID for an agenda"""
        # Convert agenda ID to bytes with prefix to differentiate from politician IDs
        id_bytes = f"agenda_{agenda_id}".encode('utf-8')
        
        # Create a simple reversible hash using the session key
        hasher = hashlib.blake2b(id_bytes, key=self.session_key, digest_size=8)
        hash_bytes = hasher.digest()
        
        # Encode as base64 and make it URL-safe
        encrypted_id = base64.urlsafe_b64encode(hash_bytes).decode('utf-8').rstrip('=')
        
        return encrypted_id

    def _encrypt_politician_id(self, politician_id):
        """Create a reversible encrypted ID for a politician"""
        # Convert politician ID to bytes with prefix to differentiate from agenda IDs
        id_bytes = f"politician_{politician_id}".encode('utf-8')
        
        # Create a simple reversible hash using the session key
        hasher = hashlib.blake2b(id_bytes, key=self.session_key, digest_size=8)
        hash_bytes = hasher.digest()
        
        # Encode as base64 and make it URL-safe
        encrypted_id = base64.urlsafe_b64encode(hash_bytes).decode('utf-8').rstrip('=')
        
        return encrypted_id


    def _count_tokens(self, text):
        """Count tokens using tiktoken"""
        try:
            # Try to get encoding for the current AI provider
            provider_info = self.ai_service.get_provider_info()
            
            if provider_info['provider'] == 'openai':
                # Use the model-specific encoding
                model = provider_info['model']
                if 'gpt-4' in model:
                    encoding = tiktoken.encoding_for_model("gpt-4")
                elif 'gpt-3.5' in model:
                    encoding = tiktoken.encoding_for_model("gpt-3.5-turbo")
                else:
                    # Fallback to cl100k_base for newer models
                    encoding = tiktoken.get_encoding("cl100k_base")
            else:
                # For Claude and other providers, use cl100k_base as approximation
                encoding = tiktoken.get_encoding("cl100k_base")
            
            tokens = encoding.encode(text)
            return len(tokens)
        except Exception as e:
            logger.warning(f"Failed to count tokens with tiktoken: {e}")
            # Fallback to word count approximation
            word_count = len(text.split())
            return int(word_count * 1.3)  # Rough approximation

    def _decrypt_xml_response(self, ai_response):
        """Replace encrypted IDs with real IDs in the XML response"""
        if ai_response is None:
            logger.error("Cannot decrypt None response")
            return None
            
        decrypted_xml = ai_response
        
        # Replace encrypted agenda IDs with real IDs
        for encrypted_id, real_id in self.agenda_id_mapping.items():
            decrypted_xml = decrypted_xml.replace(f'id="{encrypted_id}"', f'id="{real_id}"')
        
        # Replace encrypted politician IDs with real IDs
        for encrypted_id, real_id in self.politician_id_mapping.items():
            decrypted_xml = decrypted_xml.replace(f'pid="{encrypted_id}"', f'pid="{real_id}"')
        
        return decrypted_xml

    def _save_structured_data(self, agenda, agenda_content, decrypted_xml):
        """Save structured data to AgendaSummary, AgendaDecision, and AgendaActivePolitician models
        
        Returns:
            tuple: (decisions_saved: bool, activity_saved: bool)
        """
        from django.utils import timezone
        
        # Extract summary
        summary_match = re.search(r'<summary>(.*?)</summary>', agenda_content, re.DOTALL)
        summary_text = summary_match.group(1).strip() if summary_match else ""
        
        # Check if agenda has any incomplete speeches
        has_incomplete_speeches = agenda.speeches.filter(
            event_type='SPEECH',
            is_incomplete=True
        ).exists()
        
        # Create or update AgendaSummary
        current_time = timezone.now()
        agenda_summary, created = AgendaSummary.objects.get_or_create(
            agenda_item=agenda,
            defaults={
                'summary_text': summary_text,
                'xml_response': decrypted_xml,
                'is_incomplete': has_incomplete_speeches,
                'ai_summary_generated_at': current_time
            }
        )
        if not created:
            # Clear translations if content changed
            if agenda_summary.summary_text != summary_text:
                agenda_summary.summary_text_en = None
                agenda_summary.summary_text_ru = None
            
            agenda_summary.summary_text = summary_text
            agenda_summary.xml_response = decrypted_xml
            agenda_summary.is_incomplete = has_incomplete_speeches
            agenda_summary.ai_summary_generated_at = current_time
            agenda_summary.save()
        
        # Clear existing decisions for this agenda
        AgendaDecision.objects.filter(agenda_item=agenda).delete()
        
        # Extract and save decisions
        decisions_section = re.search(r'<decisions>(.*?)</decisions>', agenda_content, re.DOTALL)
        decisions_saved = 0
        if decisions_section:
            decision_matches = re.findall(r'<decision pid="([^"]*)">(.*?)</decision>', decisions_section.group(1), re.DOTALL)
            for pid, decision_text in decision_matches:
                # Skip empty decisions
                decision_text_clean = decision_text.strip()
                if not decision_text_clean:
                    logger.warning(f"Skipping empty decision for agenda {agenda.pk}")
                    continue
                
                # Get real politician ID and object
                politician = None
                if pid:  # Not empty, so not a collective decision
                    real_pid = self.politician_id_mapping.get(pid)
                    if real_pid:
                        try:
                            politician = Politician.objects.get(pk=real_pid)
                        except Politician.DoesNotExist:
                            logger.warning(f"Politician {real_pid} not found for agenda {agenda.pk}")
                
                AgendaDecision.objects.create(
                    agenda_item=agenda,
                    politician=politician,
                    decision_text=decision_text_clean,
                    is_incomplete=has_incomplete_speeches,
                    ai_summary_generated_at=current_time
                )
                decisions_saved += 1
        
        # Log warning if no decisions were saved
        if decisions_saved == 0:
            logger.warning(f"No decisions were saved for agenda {agenda.pk}. AI response may be malformed.")
            self.stdout.write(self.style.WARNING(f"⚠️  No decisions saved for agenda {agenda.pk}"))
        
        # Clear existing active politician for this agenda
        AgendaActivePolitician.objects.filter(agenda_item=agenda).delete()
        
        # Extract and save active politician
        activity_saved = False
        activity_match = re.search(r'<activity pid="([^"]*)">(.*?)</activity>', agenda_content, re.DOTALL)
        if activity_match:
            pid, activity_text = activity_match.groups()
            # Save activity even with empty pid (no active politician identified)
            politician = None
            if pid:  # Not empty, try to get the politician
                real_pid = self.politician_id_mapping.get(pid)
                if real_pid:
                    try:
                        politician = Politician.objects.get(pk=real_pid)
                    except Politician.DoesNotExist:
                        logger.warning(f"Active politician {real_pid} not found for agenda {agenda.pk}")
            
            # Create activity record (with or without politician)
            AgendaActivePolitician.objects.create(
                agenda_item=agenda,
                politician=politician,
                activity_description=activity_text.strip(),
                is_incomplete=has_incomplete_speeches,
                ai_summary_generated_at=current_time
            )
            activity_saved = True
        
        # Log warning if no activity was saved
        if not activity_saved:
            logger.warning(f"No active politician was saved for agenda {agenda.pk}. AI response may be malformed.")
            self.stdout.write(self.style.WARNING(f"⚠️  No active politician saved for agenda {agenda.pk}"))
        
        # No longer updating legacy ai_summary field - using only structured summaries
        
        # Return True if both decisions and activity were saved
        return (decisions_saved > 0, activity_saved)
    
    # ========================================================================
    # BATCH API HELPER METHODS
    # NOTE: Full batch API support for agenda summaries requires refactoring
    # due to encrypted IDs and complex XML parsing. These are simplified versions.
    # ========================================================================
    
    def _create_agenda_summary_prompt(self, agenda):
        """Create summary generation prompt for agenda using batch API"""
        # NOTE: This is a simplified version. Full implementation would require
        # handling encrypted IDs and maintaining state across batch items.
        # For now, batch API for agenda summaries should be used with caution.
        
        # Generate encrypted IDs for this agenda
        self._generate_encrypted_ids_for_agenda(agenda)
        
        # Generate XML content
        xml_content = self._generate_single_agenda_xml(agenda)
        
        # Create the prompt
        prompt = f"""Please write a detailed report of the following agenda in Estonian language, speak like native estonian. Provide response in the EXACT structured XML format shown below.

INPUT DATA:
{xml_content}

CRITICAL REQUIREMENTS:
1. ALL tags (summary, decisions, activity) MUST be INSIDE the <agenda> tag
2. You MUST include at least one <decision> tag (even if no decisions were made)
3. If no decisions were made, write: <decision pid="">Otsuseid ei tehtud</decision>
4. You MUST include an <activity> tag (even if no one was particularly active)
5. If no politician was particularly active, write: <activity pid="">Ei olnud eriti aktiivset kõnelejat</activity>
6. The response must be valid XML with proper nesting
7. Do NOT output <speech> tags - only output <agenda>, <summary>, <decisions>, <decision>, and <activity> tags

REQUIRED RESPONSE FORMAT (copy this structure EXACTLY):
<agenda id="{{agenda_id}}">
<summary>{{Write a detailed summary of the agenda in Estonian, couple paragraphs max}}</summary>
<decisions>
<decision pid="{{politician_id or empty string}}">{{Describe what decisions were made. If no decisions, write "Otsuseid ei tehtud" with empty pid}}</decision>
</decisions>
<activity pid="{{politician_id or empty string}}">{{Describe the most active speaker and their position (vasak, parem or muu). If no one was particularly active, write "Ei olnud eriti aktiivset kõnelejat" with empty pid}}</activity>
</agenda>

IMPORTANT: The closing </agenda> tag must come AFTER all other tags (summary, decisions, activity)."""
        
        return prompt
    
    def _update_agenda_with_summary(self, agenda, summary_xml):
        """Update agenda with AI-generated summary from batch API"""
        try:
            # Parse and save the AI response
            success = self._parse_and_update_single_agenda(summary_xml, agenda)
            if success:
                return True
            else:
                self.stdout.write(self.style.ERROR(f"Failed to parse summary for agenda {agenda.pk}"))
                return False
        except Exception as e:
            logger.exception(f"Error updating agenda {agenda.pk} with batch API summary")
            self.stdout.write(self.style.ERROR(f"Failed to update agenda {agenda.pk}: {str(e)}"))
            return False

