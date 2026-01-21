"""
AI Service for generating summaries using different AI providers with multi-provider support
"""
import json
import logging
import re
import requests
import time
from django.conf import settings
from typing import Optional, Generator, Tuple

logger = logging.getLogger(__name__)


class AIService:
    """Service class for handling AI API calls with support for multiple providers"""
    
    def __init__(self, provider: Optional[str] = None):
        """
        Initialize AIService with optional provider override
        
        Args:
            provider: Optional provider to use ('claude', 'ollama', 'openai'). 
                     If None, uses settings.AI_PROVIDER
        """
        # Use provided provider or fall back to settings default
        self.provider = provider or settings.AI_PROVIDER
        
        # Load all provider configurations
        self.claude_api_key = settings.CLAUDE_API_KEY
        self.ollama_base_url = settings.OLLAMA_BASE_URL
        self.ollama_model = settings.OLLAMA_MODEL
        self.openai_api_key = settings.OPENAI_API_KEY
        self.openai_model = settings.OPENAI_MODEL
        self.gemini_api_key = settings.GEMINI_API_KEY
        self.gemini_model = settings.GEMINI_MODEL
        
        # Validate the selected provider configuration
        self._validate_provider_config()
    
    def _validate_provider_config(self):
        """Validate that the selected provider has proper configuration"""
        if self.provider == 'claude' and not self.claude_api_key:
            raise ValueError("CLAUDE_API_KEY must be set when using Claude provider")
        elif self.provider == 'ollama' and not self.ollama_base_url:
            raise ValueError("OLLAMA_BASE_URL must be set when using Ollama provider")
        elif self.provider == 'openai' and not self.openai_api_key:
            raise ValueError("OPENAI_API_KEY must be set when using OpenAI provider")
        elif self.provider == 'gemini' and not self.gemini_api_key:
            raise ValueError("GEMINI_API_KEY must be set when using Gemini provider")
        elif self.provider not in ['claude', 'ollama', 'openai', 'gemini']:
            raise ValueError(f"Unsupported AI provider: {self.provider}. Must be 'claude', 'ollama', 'openai', or 'gemini'")
    
    def generate_summary(self, prompt: str, max_tokens: int = 300, temperature: float = 0.3) -> Optional[str]:
        """
        Generate a summary using the configured AI provider
        
        Args:
            prompt: The input prompt for the AI
            max_tokens: Maximum number of tokens to generate
            temperature: Temperature for generation (0.0 to 1.0)
            
        Returns:
            Generated summary text or None if failed
        """
        try:
            if self.provider == 'claude':
                return self._generate_claude_summary(prompt, max_tokens, temperature)
            elif self.provider == 'ollama':
                return self._generate_ollama_summary(prompt, max_tokens, temperature)
            elif self.provider == 'openai':
                return self._generate_openai_summary(prompt, max_tokens, temperature)
            elif self.provider == 'gemini':
                return self._generate_gemini_summary(prompt, max_tokens, temperature)
        except Exception as e:
            logger.error(f"Error generating summary with {self.provider}: {str(e)}")
            print(f"DEBUG: Error generating summary with {self.provider}: {str(e)}")
            return None
    
    def generate_summary_stream(self, prompt: str, max_tokens: int = 300, temperature: float = 0.3) -> Generator[str, None, None]:
        """
        Generate a summary with streaming response
        
        Args:
            prompt: The input prompt for the AI
            max_tokens: Maximum number of tokens to generate
            temperature: Temperature for generation (0.0 to 1.0)
            
        Yields:
            Chunks of generated text
        """
        try:
            if self.provider == 'claude':
                yield from self._generate_claude_summary_stream(prompt, max_tokens, temperature)
            elif self.provider == 'ollama':
                yield from self._generate_ollama_summary_stream(prompt, max_tokens, temperature)
            elif self.provider == 'openai':
                yield from self._generate_openai_summary_stream(prompt, max_tokens, temperature)
            elif self.provider == 'gemini':
                yield from self._generate_gemini_summary_stream(prompt, max_tokens, temperature)
        except Exception as e:
            logger.error(f"Error generating streaming summary with {self.provider}: {str(e)}")
            print(f"DEBUG: Error generating streaming summary with {self.provider}: {str(e)}")
            return
    
    def _generate_claude_summary(self, prompt: str, max_tokens: int, temperature: float) -> Optional[str]:
        """Generate summary using Claude API"""
        headers = {
            'Content-Type': 'application/json',
            'x-api-key': self.claude_api_key,
            'anthropic-version': '2023-06-01',
            'anthropic-beta': 'context-1m-2025-08-07'
        }
        
        data = {
            'model': 'claude-sonnet-4-20250514',
            'max_tokens': max_tokens,
            'temperature': temperature,
            'messages': [
                {
                    'role': 'user',
                    'content': prompt
                }
            ]
        }
        
        response = requests.post(
            'https://api.anthropic.com/v1/messages',
            headers=headers,
            json=data,
            timeout=300
        )
        
        if response.status_code != 200:
            error_details = response.text
            logger.error(f"Claude API error {response.status_code}: {error_details}")
            print(f"DEBUG: Claude API error {response.status_code}: {error_details}")
            response.raise_for_status()
        
        result = response.json()
        
        if 'content' in result and len(result['content']) > 0:
            return result['content'][0].get('text', '').strip()
        return None
    
    def _generate_claude_summary_stream(self, prompt: str, max_tokens: int, temperature: float) -> Generator[str, None, None]:
        """Generate streaming summary using Claude API"""
        headers = {
            'Content-Type': 'application/json',
            'x-api-key': self.claude_api_key,
            'anthropic-version': '2023-06-01',
            'anthropic-beta': 'context-1m-2025-08-07'
        }
        
        data = {
            'model': 'claude-sonnet-4-20250514',
            'max_tokens': max_tokens,
            'temperature': temperature,
            'stream': True,
            'messages': [
                {
                    'role': 'user',
                    'content': prompt
                }
            ]
        }
        
        
        response = requests.post(
            'https://api.anthropic.com/v1/messages',
            headers=headers,
            json=data,
            timeout=300,
            stream=True
        )
        
        
        if response.status_code != 200:
            error_details = response.text
            logger.error(f"Claude streaming API error {response.status_code}: {error_details}")
            print(f"DEBUG: Claude streaming API error {response.status_code}: {error_details}")
            response.raise_for_status()
        
        # Process streaming response
        for line in response.iter_lines():
            if line:
                line_str = line.decode('utf-8')
                if line_str.startswith('data: '):
                    try:
                        data_json = json.loads(line_str[6:])
                        if data_json.get('type') == 'content_block_delta':
                            delta = data_json.get('delta', {})
                            if 'text' in delta:
                                yield delta['text']
                    except json.JSONDecodeError:
                        continue
    
    def _generate_ollama_summary(self, prompt: str, max_tokens: int, temperature: float) -> Optional[str]:
        """Generate summary using Ollama API"""
        url = f"{self.ollama_base_url}/api/generate"
        
        headers = {
            'Content-Type': 'application/json',
        }
        
        data = {
            'model': self.ollama_model,
            'prompt': prompt,
            'stream': False,
            'options': {
                'num_predict': max_tokens,
                'temperature': temperature,
            }
        }
        
        try:
            response = requests.post(
                url,
                headers=headers,
                json=data,
                timeout=300
            )
            
            response.raise_for_status()
            result = response.json()
            
            # Handle thinking models and extract content from <reply> tags
            if 'response' in result:
                response_text = result['response'].strip()
                
                # Extract content from <reply> tags if present
                if '<reply>' in response_text and '</reply>' in response_text:
                    reply_match = re.search(r'<reply>(.*?)</reply>', response_text, re.DOTALL)
                    if reply_match:
                        reply_content = reply_match.group(1).strip()
                        return reply_content
                
                # If response is empty but there's thinking content, use the thinking
                if not response_text and 'thinking' in result and result['thinking']:
                    thinking_text = result['thinking'].strip()
                    # Also try to extract from <reply> tags in thinking content
                    if '<reply>' in thinking_text and '</reply>' in thinking_text:
                        reply_match = re.search(r'<reply>(.*?)</reply>', thinking_text, re.DOTALL)
                        if reply_match:
                            reply_content = reply_match.group(1).strip()
                            return reply_content
                    return thinking_text
                
                
                return response_text if response_text else None
            return None
            
        except requests.exceptions.ConnectionError as e:
            print(f"DEBUG: Ollama connection error: {e}")
            raise
        except requests.exceptions.RequestException as e:
            print(f"DEBUG: Ollama request error: {e}")
            raise
        except Exception as e:
            print(f"DEBUG: Ollama unexpected error: {e}")
            raise
    
    def _generate_ollama_summary_stream(self, prompt: str, max_tokens: int, temperature: float) -> Generator[str, None, None]:
        """Generate streaming summary using Ollama API"""
        headers = {
            'Content-Type': 'application/json',
        }
        
        data = {
            'model': self.ollama_model,
            'prompt': prompt,
            'stream': True,
            'options': {
                'num_predict': max_tokens,
                'temperature': temperature,
            }
        }
        
        response = requests.post(
            f"{self.ollama_base_url}/api/generate",
            headers=headers,
            json=data,
            timeout=300,
            stream=True
        )
        
        response.raise_for_status()
        
        # Process streaming response
        thinking_content = ""
        response_content = ""
        full_content = ""
        
        for line in response.iter_lines():
            if line:
                line_str = line.decode('utf-8')
                try:
                    data_json = json.loads(line_str)
                    
                    # Collect response content
                    if 'response' in data_json:
                        chunk = data_json['response']
                        response_content += chunk
                        full_content += chunk
                        yield chunk
                    
                    # For thinking models, also collect thinking content
                    if 'thinking' in data_json:
                        thinking_content += data_json['thinking']
                        full_content += data_json['thinking']
                    
                    if data_json.get('done', False):
                        # Try to extract content from <reply> tags in the full content
                        if '<reply>' in full_content and '</reply>' in full_content:
                            reply_match = re.search(r'<reply>(.*?)</reply>', full_content, re.DOTALL)
                            if reply_match:
                                reply_content = reply_match.group(1).strip()
                                # Clear previous output and yield only the reply content
                                yield f"\n[Extracted reply]: {reply_content}"
                                break
                        
                        # If we got no response content but have thinking content, yield the thinking
                        if not response_content.strip() and thinking_content.strip():
                            yield thinking_content
                        break
                        
                except json.JSONDecodeError:
                    continue
    
    def _generate_gemini_summary(self, prompt: str, max_tokens: int, temperature: float) -> Optional[str]:
        """Generate summary using Google Gemini API"""
        try:
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
                    'temperature': temperature,
                    'maxOutputTokens': max_tokens,
                    'topK': 40,
                    'topP': 0.95
                }
            }
            
            
            # Use the Gemini REST API endpoint with API key as query parameter
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.gemini_model}:generateContent?key={self.gemini_api_key}"
            

            
            response = requests.post(
                url,
                headers=headers,
                json=data,
                timeout=300
            )
            
            
            if response.status_code != 200:
                error_details = response.text
                logger.error(f"Gemini API error {response.status_code}: {error_details}")
                print(f"DEBUG: Gemini API error {response.status_code}: {error_details}")
                print(f"DEBUG: Full response headers: {dict(response.headers)}")
                print(f"DEBUG: Full response content: {response.text}")
                response.raise_for_status()
            
            result = response.json()
            print(f"DEBUG: Gemini API successful response: {result}")
            
            if 'candidates' in result and len(result['candidates']) > 0:
                candidate = result['candidates'][0]
                print(f"DEBUG: First candidate: {candidate}")
                
                # Check finish reason
                finish_reason = candidate.get('finishReason', 'UNKNOWN')
                print(f"DEBUG: Finish reason: {finish_reason}")
                
                if finish_reason == 'MAX_TOKENS':
                    logger.warning(f"Gemini response truncated due to MAX_TOKENS. Consider increasing max_tokens parameter.")
                    print(f"WARNING: Response was truncated due to MAX_TOKENS limit!")
                
                if 'content' in candidate and 'parts' in candidate['content']:
                    parts = candidate['content']['parts']
                    print(f"DEBUG: Content parts: {parts}")
                    if len(parts) > 0 and 'text' in parts[0]:
                        text = parts[0]['text'].strip()
                        print(f"DEBUG: Extracted text: {text[:200]}...")
                        return text
                else:
                    print(f"DEBUG: No 'parts' in content. Content: {candidate.get('content', 'N/A')}")
            else:
                print(f"DEBUG: No candidates in response or empty candidates list")
            
            return None
            
        except Exception as e:
            logger.error(f"Gemini API exception: {str(e)}")
            print(f"DEBUG: Gemini API exception: {str(e)}")
            print(f"DEBUG: Exception type: {type(e).__name__}")
            import traceback
            print(f"DEBUG: Full traceback: {traceback.format_exc()}")
            return None
    
    def _generate_gemini_summary_stream(self, prompt: str, max_tokens: int, temperature: float) -> Generator[str, None, None]:
        """Generate streaming summary using Google Gemini API"""
        try:
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
                    'temperature': temperature,
                    'maxOutputTokens': max_tokens,
                    'topK': 40,
                    'topP': 0.95
                }
            }
            
            
            # Use the Gemini streaming endpoint with API key as query parameter
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.gemini_model}:streamGenerateContent?key={self.gemini_api_key}"
            
            response = requests.post(
                url,
                headers=headers,
                json=data,
                timeout=300,
                stream=True
            )
            
            
            if response.status_code != 200:
                error_details = response.text
                logger.error(f"Gemini streaming API error {response.status_code}: {error_details}")
                print(f"DEBUG: Gemini streaming API error {response.status_code}: {error_details}")
                response.raise_for_status()
            
            # Process streaming response - Gemini returns a JSON array format
            full_response = ""
            for line in response.iter_lines():
                if line:
                    full_response += line.decode('utf-8')
            
            
            try:
                # Parse the complete JSON array
                data_array = json.loads(full_response)
                
                # Process each chunk in the array
                for i, data_json in enumerate(data_array):
                    if 'candidates' in data_json and len(data_json['candidates']) > 0:
                        candidate = data_json['candidates'][0]
                        
                        if 'content' in candidate and 'parts' in candidate['content']:
                            parts = candidate['content']['parts']
                            
                            if len(parts) > 0 and 'text' in parts[0]:
                                text_chunk = parts[0]['text']
                                yield text_chunk
                                
            except json.JSONDecodeError as e:
                print(f"DEBUG: Gemini JSON decode error: {e}")
        except Exception as e:
            logger.error(f"Gemini streaming API exception: {str(e)}")
            print(f"DEBUG: Gemini streaming API exception: {str(e)}")
            return
    
    def _generate_openai_summary(self, prompt: str, max_tokens: int, temperature: float) -> Optional[str]:
        """Generate summary using OpenAI API"""
        try:
            headers = {
                'Content-Type': 'application/json',
                'Authorization': f'Bearer {self.openai_api_key}'
            }
            
            data = {
                'model': self.openai_model,
                'messages': [
                    {
                        'role': 'user',
                        'content': prompt
                    }
                ],
                # No max_completion_tokens limit - let the model use what it needs
                # Note: gpt-5-nano only supports default temperature of 1, so we ignore the temperature parameter
            }
            
            
            response = requests.post(
                'https://api.openai.com/v1/chat/completions',
                headers=headers,
                json=data,
                timeout=300
            )
            
            
            if response.status_code != 200:
                error_details = response.text
                logger.error(f"OpenAI API error {response.status_code}: {error_details}")
                print(f"DEBUG: OpenAI API error {response.status_code}: {error_details}")
                response.raise_for_status()
            
            result = response.json()
            
            if 'choices' in result and len(result['choices']) > 0:
                message = result['choices'][0].get('message', {})
                content = message.get('content', '')
                if content:
                    return content.strip()
            return None
            
        except Exception as e:
            logger.error(f"OpenAI API exception: {str(e)}")
            print(f"DEBUG: OpenAI API exception: {str(e)}")
            return None
    
    def _generate_openai_summary_stream(self, prompt: str, max_tokens: int, temperature: float) -> Generator[str, None, None]:
        """Generate streaming summary using OpenAI API"""
        headers = {
            'Content-Type': 'application/json',
            'Authorization': f'Bearer {self.openai_api_key}'
        }
        
        data = {
            'model': self.openai_model,
            'messages': [
                {
                    'role': 'user',
                    'content': prompt
                }
            ],
            # No max_completion_tokens limit - let the model use what it needs
            'stream': True
            # Note: gpt-5-nano only supports default temperature of 1, so we ignore the temperature parameter
        }
        
        response = requests.post(
            'https://api.openai.com/v1/chat/completions',
            headers=headers,
            json=data,
            timeout=300,
            stream=True
        )
        
        if response.status_code != 200:
            error_details = response.text
            logger.error(f"OpenAI streaming API error {response.status_code}: {error_details}")
            print(f"DEBUG: OpenAI streaming API error {response.status_code}: {error_details}")
            response.raise_for_status()
        
        # Process streaming response
        for line in response.iter_lines():
            if line:
                line_str = line.decode('utf-8')
                if line_str.startswith('data: '):
                    data_str = line_str[6:]  # Remove 'data: ' prefix
                    if data_str.strip() == '[DONE]':
                        break
                    try:
                        data_json = json.loads(data_str)
                        if 'choices' in data_json and len(data_json['choices']) > 0:
                            delta = data_json['choices'][0].get('delta', {})
                            if 'content' in delta and delta['content']:
                                yield delta['content']
                    except json.JSONDecodeError:
                        continue
    
    def get_provider_info(self) -> dict:
        """Get information about the current AI provider"""
        if self.provider == 'claude':
            return {
                'provider': self.provider,
                'model': 'claude-sonnet-4-20250514',
                'endpoint': 'https://api.anthropic.com/v1/messages'
            }
        elif self.provider == 'ollama':
            return {
                'provider': self.provider,
                'model': self.ollama_model,
                'endpoint': f"{self.ollama_base_url}/api/generate"
            }
        elif self.provider == 'openai':
            return {
                'provider': self.provider,
                'model': self.openai_model,
                'endpoint': 'https://api.openai.com/v1/chat/completions'
            }
        elif self.provider == 'gemini':
            return {
                'provider': self.provider,
                'model': self.gemini_model,
                'endpoint': 'https://generativelanguage.googleapis.com/v1beta/models'
            }
        else:
            return {
                'provider': self.provider,
                'model': 'unknown',
                'endpoint': 'unknown'
            }


def get_ai_service(provider: Optional[str] = None):
    """
    Get an instance of AIService with optional provider override
    
    Args:
        provider: Optional provider to use ('claude', 'ollama', 'openai').
                 If None, uses settings.AI_PROVIDER
    """
    try:
        return AIService(provider=provider)
    except Exception as e:
        logger.error(f"Failed to initialize AI service with provider {provider}: {str(e)}")
        return None


# Convenience functions for specific providers
def get_claude_service():
    """Get AIService configured for Claude"""
    return get_ai_service('claude')


def get_openai_service():
    """Get AIService configured for OpenAI"""
    return get_ai_service('openai')


def get_ollama_service():
    """Get AIService configured for Ollama"""
    return get_ai_service('ollama')


def get_gemini_service():
    """Get AIService configured for Gemini"""
    return get_ai_service('gemini')