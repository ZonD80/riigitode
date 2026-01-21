"""
Shared mixin for Gemini Batch API processing across management commands
"""
import time
import logging
import json
import tempfile
import os
import requests
from django.core.management.base import CommandError
from django.conf import settings

logger = logging.getLogger(__name__)


class GeminiBatchAPIMixin:
    """Mixin to add Gemini Batch API support to management commands"""
    
    def add_batch_api_arguments(self, parser):
        """Add common batch API arguments to argument parser"""
        parser.add_argument(
            '--use-batch-api',
            dest='use_batch_api',
            action='store_true',
            help='Use Gemini Batch API for cost-effective batch processing (50%% cost reduction)'
        )
        parser.add_argument(
            '--no-batch-api',
            dest='use_batch_api',
            action='store_false',
            help='Disable Gemini Batch API and use standard parallel processing (default)'
        )
        parser.set_defaults(use_batch_api=True)
        parser.add_argument(
            '--resume-from-batch-id',
            type=str,
            help='Resume from an existing Gemini batch job (e.g., "batches/abc123")'
        )
    
    def initialize_batch_api(self, options):
        """Initialize batch API settings from options"""
        self.use_batch_api = options.get('use_batch_api', True)
        self.resume_from_batch_id = options.get('resume_from_batch_id')
        
        # Validate
        if self.use_batch_api and self.ai_provider != 'gemini':
            raise CommandError("Batch API only supported with --ai-provider=gemini")
        
        if self.resume_from_batch_id:
            if self.ai_provider != 'gemini':
                raise CommandError("--resume-from-batch-id only works with --ai-provider=gemini")
            self.use_batch_api = True
    
    def should_use_batch_api(self):
        """Check if batch API should be used"""
        return self.use_batch_api and self.ai_provider == 'gemini' and not self.resume_from_batch_id
    
    def create_batch_jsonl_for_items(self, items_list, create_prompt_func):
        """
        Create JSONL data for batch API requests
        
        Args:
            items_list: List of items to process
            create_prompt_func: Function that takes an item and returns prompt text
            
        Returns:
            tuple: (jsonl_data, items_with_prompts) where items_with_prompts is the list of items that were included
        """
        jsonl_data = []
        items_with_prompts = []
        gemini_model = getattr(settings, 'GEMINI_MODEL', 'gemini-2.5-flash-lite-preview-09-2025')
        
        for item in items_list:
            prompt = create_prompt_func(item)
            if not prompt:
                continue
            
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
            items_with_prompts.append(item)
        
        return jsonl_data, items_with_prompts
    
    def upload_batch_file(self, file_path):
        """Upload JSONL file to Gemini API"""
        gemini_api_key = getattr(settings, 'GEMINI_API_KEY', '')
        
        if not gemini_api_key:
            raise CommandError("GEMINI_API_KEY not configured")
        
        with open(file_path, 'rb') as f:
            file_content = f.read()
        
        url = f"https://generativelanguage.googleapis.com/upload/v1beta/files?key={gemini_api_key}"
        
        headers = {
            'X-Goog-Upload-Protocol': 'multipart'
        }
        
        metadata = json.dumps({"file": {"displayName": "batch_requests"}})
        
        files = {
            'metadata': (None, metadata, 'application/json'),
            'file': ('batch_requests.jsonl', file_content, 'application/json')
        }
        
        response = requests.post(url, headers=headers, files=files, timeout=120)
        
        if response.status_code == 200:
            result = response.json()
            if 'file' in result and 'name' in result['file']:
                return result['file']['name']
            elif 'name' in result:
                return result['name']
            else:
                raise CommandError(f"Unexpected upload response format: {result}")
        else:
            raise CommandError(f"File upload failed: {response.status_code} - {response.text}")
    
    def create_batch_job(self, file_uri):
        """Create a batch job with the uploaded file"""
        gemini_api_key = getattr(settings, 'GEMINI_API_KEY', '')
        gemini_model = getattr(settings, 'GEMINI_MODEL', 'gemini-2.5-flash-lite-preview-09-2025')
        
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{gemini_model}:batchGenerateContent?key={gemini_api_key}"
        
        headers = {
            'Content-Type': 'application/json'
        }
        
        data = {
            "batch": {
                "display_name": self.__class__.__name__,
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
    
    def poll_batch_job(self, batch_job_name, max_wait_seconds=3600, poll_interval=30):
        """Poll batch job status until completion or timeout"""
        gemini_api_key = getattr(settings, 'GEMINI_API_KEY', '')
        
        url = f"https://generativelanguage.googleapis.com/v1beta/{batch_job_name}?key={gemini_api_key}"
        
        start_time = time.time()
        elapsed_time = 0
        first_unknown = True
        
        while elapsed_time < max_wait_seconds:
            response = requests.get(url, timeout=30)
            
            if response.status_code == 200:
                result = response.json()
                
                metadata = result.get('metadata', {})
                state = metadata.get('state', 'UNKNOWN')
                
                batch_stats = metadata.get('batchStats', {})
                total_requests = int(batch_stats.get('requestCount', 0))
                pending_requests = int(batch_stats.get('pendingRequestCount', 0))
                completed_requests = total_requests - pending_requests
                
                elapsed_time = time.time() - start_time
                
                if state == 'UNKNOWN' and first_unknown:
                    self.stdout.write(self.style.WARNING(f"⚠️  Received UNKNOWN status. Full API response:"))
                    self.stdout.write(json.dumps(result, indent=2))
                    first_unknown = False
                
                if total_requests > 0:
                    progress_pct = (completed_requests / total_requests) * 100
                    self.stdout.write(f"Status: {state} | Progress: {completed_requests}/{total_requests} ({progress_pct:.1f}%) | Elapsed: {elapsed_time/60:.1f}m")
                else:
                    self.stdout.write(f"Status: {state} (elapsed: {elapsed_time/60:.1f}m)")
                
                if state == 'BATCH_STATE_SUCCEEDED':
                    self.stdout.write(self.style.SUCCESS("Batch job completed successfully!"))
                    
                    output = metadata.get('output', {})
                    file_name = output.get('responsesFile')
                    
                    if not file_name:
                        response_data = result.get('response', {})
                        file_name = response_data.get('responsesFile')
                    
                    if not file_name:
                        output_config = metadata.get('outputConfig', {})
                        file_name = output_config.get('fileName')
                    
                    if file_name:
                        return file_name
                    else:
                        self.stdout.write(self.style.WARNING("Full response for debugging:"))
                        self.stdout.write(json.dumps(result, indent=2))
                        raise CommandError(f"No result file in completed batch")
                
                elif state in ['BATCH_STATE_FAILED', 'BATCH_STATE_CANCELLED']:
                    error_msg = result.get('error', {}).get('message', 'Unknown error')
                    self.stdout.write(self.style.ERROR(f"Full error response:"))
                    self.stdout.write(json.dumps(result, indent=2))
                    raise CommandError(f"Batch job {state}: {error_msg}")
                
                elif state == 'UNKNOWN':
                    if elapsed_time > 300:
                        raise CommandError(f"Batch job status remains UNKNOWN after {elapsed_time/60:.1f} minutes")
                
                time.sleep(poll_interval)
                elapsed_time = time.time() - start_time
            else:
                raise CommandError(f"Failed to check batch status: {response.status_code} - {response.text}")
        
        raise CommandError(f"Batch job timed out after {max_wait_seconds/60:.1f} minutes")
    
    def download_batch_results(self, result_file_uri, batch_job_name=None):
        """Download and parse batch results using google-genai SDK"""
        gemini_api_key = getattr(settings, 'GEMINI_API_KEY', '')
        
        self.stdout.write(f"Result file URI: {result_file_uri}")
        
        try:
            from google import genai
            
            client = genai.Client(api_key=gemini_api_key)
            
            self.stdout.write(f"Downloading using google-genai SDK...")
            file_content_bytes = client.files.download(file=result_file_uri)
            file_content = file_content_bytes.decode('utf-8')
            
            self.stdout.write(f"Successfully downloaded batch results")
            
            results = {}
            for line in file_content.strip().split('\n'):
                if line:
                    try:
                        result_item = json.loads(line)
                        key = result_item.get('key', '')
                        response_data = result_item.get('response', {})
                        
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
            
            self.stdout.write(f"Downloaded {len(results)} results")
            return results
            
        except ImportError:
            raise CommandError("google-genai SDK not installed. Install with: pip install google-genai")
        except Exception as e:
            logger.exception(f"Error downloading with SDK: {e}")
            raise CommandError(f"Failed to download batch results: {str(e)}")
    
    def process_batch_with_chunking(self, items_list, item_type, create_prompt_func, update_func):
        """
        Process items using Gemini Batch API with chunking to avoid rate limits
        
        Args:
            items_list: List of items to process
            item_type: String describing item type (for logging)
            create_prompt_func: Function(item) -> prompt_text (or None to skip)
            update_func: Function(item, result_text) -> None to update item
        """
        total_items = len(items_list)
        chunk_size = max(self.batch_size, 100)
        num_chunks = (total_items + chunk_size - 1) // chunk_size
        
        if num_chunks > 1:
            self.stdout.write(self.style.HTTP_INFO(f"Processing {total_items} {item_type} in {num_chunks} batch chunks (max {chunk_size} items per batch)"))
        else:
            self.stdout.write(self.style.HTTP_INFO(f"Processing {total_items} {item_type} in single batch"))
        
        overall_start_time = time.time()
        
        for chunk_idx in range(num_chunks):
            chunk_start = chunk_idx * chunk_size
            chunk_end = min(chunk_start + chunk_size, total_items)
            chunk_items = items_list[chunk_start:chunk_end]
            
            self.stdout.write("\n" + "=" * 80)
            self.stdout.write(self.style.HTTP_INFO(f"BATCH CHUNK {chunk_idx + 1}/{num_chunks}: Processing items {chunk_start + 1}-{chunk_end}"))
            self.stdout.write("=" * 80)
            
            self._process_single_batch(chunk_items, item_type, create_prompt_func, update_func)
            
            if chunk_idx < num_chunks - 1:
                delay_seconds = max(getattr(self, 'delay', 2.0), 2.0)
                self.stdout.write(f"\nWaiting {delay_seconds}s before next batch chunk...")
                time.sleep(delay_seconds)
        
        overall_time = time.time() - overall_start_time
        self.stdout.write("\n" + "=" * 80)
        self.stdout.write(self.style.SUCCESS(f"ALL BATCH CHUNKS COMPLETE"))
        self.stdout.write(f"Total chunks processed: {num_chunks}")
        self.stdout.write(f"Total time: {overall_time/60:.1f} minutes ({overall_time:.1f} seconds)")
        self.stdout.write("=" * 80)
    
    def _process_single_batch(self, items_list, item_type, create_prompt_func, update_func):
        """Process a single batch"""
        start_time = time.time()
        
        try:
            self.stdout.write("Step 1: Creating batch request file...")
            jsonl_data, items_with_prompts = self.create_batch_jsonl_for_items(items_list, create_prompt_func)
            
            if not jsonl_data:
                self.stdout.write(self.style.WARNING("No items need processing (all already translated)"))
                skipped_count = len(items_list)
                self.stdout.write(f"Skipped {skipped_count} {item_type} that already have translations")
                return
            
            with tempfile.NamedTemporaryFile(mode='w', suffix='.jsonl', delete=False) as f:
                for line in jsonl_data:
                    f.write(json.dumps(line) + '\n')
                temp_file_path = f.name
            
            skipped_count = len(items_list) - len(items_with_prompts)
            self.stdout.write(f"Created batch file with {len(jsonl_data)} requests (skipped {skipped_count} already translated)")
            
            try:
                self.stdout.write("\nStep 2: Uploading batch file...")
                file_uri = self.upload_batch_file(temp_file_path)
                self.stdout.write(f"Uploaded file: {file_uri}")
                
                self.stdout.write("\nStep 3: Creating batch job...")
                batch_job_name = self.create_batch_job(file_uri)
                self.stdout.write(f"Created batch job: {batch_job_name}")
                
                resume_cmd = self._generate_resume_command(batch_job_name)
                self.stdout.write(self.style.WARNING(f"\n⚠️  IMPORTANT: If this script crashes, resume with:"))
                self.stdout.write(self.style.WARNING(f"━" * 80))
                self.stdout.write(self.style.SUCCESS(resume_cmd))
                self.stdout.write(self.style.WARNING(f"━" * 80 + "\n"))
                
                self.stdout.write("Step 4: Waiting for batch job to complete...")
                result_file_uri = self.poll_batch_job(batch_job_name)
                
                if not result_file_uri:
                    self.stdout.write(self.style.ERROR("Batch job failed or timed out"))
                    return
                
                self.stdout.write("\nStep 5: Downloading and processing results...")
                results = self.download_batch_results(result_file_uri, batch_job_name)
                
                self.stdout.write("\nStep 6: Updating database with results...")
                # Only update items that were actually included in the batch
                processed, errors = self._update_items_from_results(items_with_prompts, results, update_func)
                
                total_time = time.time() - start_time
                self.stdout.write("\n" + "=" * 60)
                self.stdout.write(self.style.SUCCESS("BATCH PROCESSING COMPLETE"))
                self.stdout.write(f"Total time: {total_time/60:.1f} minutes ({total_time:.1f} seconds)")
                self.stdout.write(f"Items in batch: {len(items_with_prompts)}")
                self.stdout.write(f"Items skipped (already translated): {skipped_count}")
                self.stdout.write(f"Successfully processed: {processed}/{len(items_with_prompts)} {item_type}")
                if errors > 0:
                    self.stdout.write(self.style.ERROR(f"Errors encountered: {errors}"))
                else:
                    self.stdout.write(self.style.SUCCESS("No errors encountered!"))
                
                if hasattr(self, 'dry_run') and self.dry_run:
                    self.stdout.write(self.style.WARNING("Note: This was a dry run - no changes saved"))
                    
            finally:
                if os.path.exists(temp_file_path):
                    os.unlink(temp_file_path)
                
        except Exception as e:
            logger.exception("Error in batch API processing")
            self.stdout.write(self.style.ERROR(f"Batch processing error: {str(e)}"))
            raise
    
    def _update_items_from_results(self, items_list, results, update_func):
        """Update items with batch results"""
        processed = 0
        errors = 0
        
        for item in items_list:
            key = f"item_{item.pk}"
            
            if key not in results:
                self.stdout.write(f"No result for item {item.pk}")
                errors += 1
                continue
            
            try:
                result_text = results[key]
                
                if hasattr(self, 'dry_run') and self.dry_run:
                    self.stdout.write(f"[DRY RUN] Would update item {item.pk}")
                    processed += 1
                else:
                    update_func(item, result_text)
                    processed += 1
                    
            except Exception as e:
                logger.exception(f"Error updating item {item.pk}")
                self.stdout.write(self.style.ERROR(f"Error updating item {item.pk}: {str(e)}"))
                errors += 1
        
        return processed, errors
    
    def _generate_resume_command(self, batch_job_name):
        """Generate a complete resume command"""
        cmd_parts = [f"python manage.py {self.__class__.__module__.split('.')[-1]}"]
        cmd_parts.append(f"--ai-provider=gemini")
        cmd_parts.append(f"--resume-from-batch-id={batch_job_name}")
        
        if hasattr(self, 'target_language') and self.target_language != 'both':
            cmd_parts.append(f"--target-language={self.target_language}")
        
        if hasattr(self, 'overwrite') and self.overwrite:
            cmd_parts.append("--overwrite")
        
        if hasattr(self, 'dry_run') and self.dry_run:
            cmd_parts.append("--dry-run")
        
        if hasattr(self, 'verbose') and self.verbose:
            cmd_parts.append("--verbose")
        
        return " \\\n    ".join(cmd_parts)
    
    def resume_batch_job_only(self, batch_job_id, model_class, update_func):
        """
        Resume a specific batch job without processing any other items
        
        Args:
            batch_job_id: The batch job ID to resume
            model_class: Django model class to query items
            update_func: Function(item, result_text) -> None to update item
        """
        self.stdout.write("=" * 80)
        self.stdout.write(self.style.HTTP_INFO(f"RESUMING BATCH JOB: {batch_job_id}"))
        self.stdout.write("=" * 80)
        
        start_time = time.time()
        
        try:
            self.stdout.write("\nStep 1: Waiting for batch job to complete...")
            result_file_uri = self.poll_batch_job(batch_job_id)
            
            if not result_file_uri:
                raise CommandError("Batch job failed or timed out")
            
            self.stdout.write("\nStep 2: Downloading and processing results...")
            results = self.download_batch_results(result_file_uri, batch_job_id)
            
            self.stdout.write("\nStep 3: Updating database with translations...")
            processed, errors = self._update_items_from_batch_results_by_pk(results, model_class, update_func)
            
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
            
            if hasattr(self, 'dry_run') and self.dry_run:
                self.stdout.write(self.style.WARNING("Note: This was a dry run"))
            
            self.stdout.write("=" * 80)
                
        except Exception as e:
            logger.exception("Error resuming batch job")
            raise CommandError(f"Error resuming batch job: {str(e)}")
    
    def _update_items_from_batch_results_by_pk(self, results, model_class, update_func):
        """Update database items with translation results (used when resuming)"""
        processed = 0
        errors = 0
        
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
        
        items = model_class.objects.filter(pk__in=item_pks)
        items_dict = {item.pk: item for item in items}
        
        self.stdout.write(f"Found {len(items_dict)} items in database matching {len(item_pks)} result keys")
        
        for key, result_text in results.items():
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
                if hasattr(self, 'dry_run') and self.dry_run:
                    self.stdout.write(f"[DRY RUN] Would update item {item.pk}")
                    processed += 1
                else:
                    update_func(item, result_text)
                    processed += 1
                    
            except Exception as e:
                logger.exception(f"Error updating item {item.pk}")
                self.stdout.write(self.style.ERROR(f"Error updating item {item.pk}: {str(e)}"))
                errors += 1
        
        return processed, errors

