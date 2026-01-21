"""
Language detection middleware for pyi18next translations
"""
import re
from django.utils.deprecation import MiddlewareMixin

class LanguageMiddleware(MiddlewareMixin):
    """
    Middleware to detect and set the user's preferred language
    """
    
    SUPPORTED_LANGUAGES = ['et', 'en', 'ru']
    DEFAULT_LANGUAGE = 'et'
    
    def process_request(self, request):
        """
        Process the request to determine the user's language preference
        """
        # Priority order:
        # 1. URL parameter ?lang=xx
        # 2. Session language (if previously set)
        # 3. Accept-Language header
        # 4. Default language (Estonian)
        
        lang = None
        
        # 1. Check URL parameter
        url_lang = request.GET.get('lang')
        if url_lang and url_lang in self.SUPPORTED_LANGUAGES:
            lang = url_lang
            # Store in session for future requests
            request.session['language'] = lang
        
        # 2. Check session
        elif 'language' in request.session and request.session['language'] in self.SUPPORTED_LANGUAGES:
            lang = request.session['language']
        
        # 3. Check Accept-Language header
        else:
            accept_language = request.META.get('HTTP_ACCEPT_LANGUAGE', '')
            lang = self.parse_accept_language(accept_language)
        
        # 4. Fallback to default
        if not lang or lang not in self.SUPPORTED_LANGUAGES:
            lang = self.DEFAULT_LANGUAGE
        
        # Set the language on the request
        request.LANGUAGE_CODE = lang
    
    def parse_accept_language(self, accept_language):
        """
        Parse the Accept-Language header to find the best matching language
        
        Args:
            accept_language: The Accept-Language header value
            
        Returns:
            Language code or None if no match found
        """
        if not accept_language:
            return None
        
        # Parse Accept-Language header
        # Format: "en-US,en;q=0.9,et;q=0.8,ru;q=0.7"
        languages = []
        
        for item in accept_language.split(','):
            item = item.strip()
            if ';' in item:
                lang, q = item.split(';', 1)
                try:
                    quality = float(q.split('=')[1])
                except (IndexError, ValueError):
                    quality = 1.0
            else:
                lang = item
                quality = 1.0
            
            # Extract language code (first 2 characters)
            lang_code = lang.strip()[:2].lower()
            if lang_code in self.SUPPORTED_LANGUAGES:
                languages.append((lang_code, quality))
        
        # Sort by quality (highest first)
        languages.sort(key=lambda x: x[1], reverse=True)
        
        # Return the highest quality supported language
        if languages:
            return languages[0][0]
        
        return None
