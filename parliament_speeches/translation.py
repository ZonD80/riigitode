"""
Translation utilities using pyi18next for server-side template translation
"""
import os
import json
from typing import Optional, Dict, Any

try:
    import pyi18next.i18next
    I18NEXT_AVAILABLE = True
except ImportError:
    I18NEXT_AVAILABLE = False
    print("Warning: pyi18next not installed. Install with: pip install pyi18next")

class TranslationManager:
    def __init__(self):
        self.i18n = None
        self.fallback_translations = {}
        self.setup()
    
    def setup(self):
        """Initialize pyi18next or fallback system"""
        # Always load fallback translations as a safety net
        self.load_fallback_translations()
        
        if I18NEXT_AVAILABLE:
            self.load_translations_for_pyi18next()
    
    def load_translations_for_pyi18next(self):
        """Load translation files and initialize pyi18next"""
        locales_dir = os.path.join(os.path.dirname(__file__), '..', 'locales')
        resources = {}
        
        for lang in ['en', 'et', 'ru']:
            translation_file = os.path.join(locales_dir, lang, 'translation.json')
            try:
                with open(translation_file, 'r', encoding='utf-8') as f:
                    translations = json.load(f)
                    resources[lang] = {
                        'translation': translations
                    }
            except FileNotFoundError:
                print(f"Warning: Translation file not found for language: {lang}")
                resources[lang] = {'translation': {}}
            except json.JSONDecodeError:
                print(f"Warning: Invalid JSON in translation file for language: {lang}")
                resources[lang] = {'translation': {}}
        
        try:
            # Initialize pyi18next with resources
            print(f"Initializing pyi18next with resources: {list(resources.keys())}")
            self.i18n = pyi18next.i18next.I18next(
                default_lng='et',
                default_ns='translation',
                resources=resources
            )
            print("pyi18next initialized successfully")
        except Exception as e:
            print(f"Error initializing pyi18next: {e}")
            self.i18n = None
    
    def load_fallback_translations(self):
        """Load translations for fallback system when pyi18next is not available"""
        locales_dir = os.path.join(os.path.dirname(__file__), '..', 'locales')
        
        for lang in ['en', 'et', 'ru']:
            translation_file = os.path.join(locales_dir, lang, 'translation.json')
            try:
                with open(translation_file, 'r', encoding='utf-8') as f:
                    self.fallback_translations[lang] = json.load(f)
            except (FileNotFoundError, json.JSONDecodeError):
                self.fallback_translations[lang] = {}
    
    def translate(self, key: str, lang: str = 'et', **kwargs) -> str:
        """
        Translate a key to the specified language
        
        Args:
            key: Translation key
            lang: Language code (et, en, ru)
            **kwargs: Additional parameters for interpolation
            
        Returns:
            Translated string or the key if translation not found
        """
        # Ensure language is supported
        if lang not in ['et', 'en', 'ru']:
            lang = 'et'
        
        if I18NEXT_AVAILABLE and self.i18n:
            try:
                # Use pyi18next for translation with correct API
                # Get language-specific translation function
                t_ = self.i18n.get_translate_func(lng=lang)
                result = t_(key, **kwargs)
                
                # If pyi18next returns None or the key (meaning not found), fall back to fallback translations
                if result is None or result == key:
                    translations = self.fallback_translations.get(lang, {})
                    translation = translations.get(key, key)
                    if kwargs:
                        try:
                            return translation.format(**kwargs)
                        except (KeyError, ValueError):
                            return translation
                    return translation
                return result
            except Exception as e:
                print(f"pyi18next translation error: {e}")
                # Fall back to fallback translations if pyi18next fails
                translations = self.fallback_translations.get(lang, {})
                translation = translations.get(key, key)
                if kwargs:
                    try:
                        return translation.format(**kwargs)
                    except (KeyError, ValueError):
                        return translation
                return translation
        else:
            # Fallback translation system
            translations = self.fallback_translations.get(lang, {})
            translation = translations.get(key, key)
            
            # Simple interpolation for fallback
            if kwargs:
                try:
                    return translation.format(**kwargs)
                except (KeyError, ValueError):
                    return translation
            
            return translation

# Global translation manager instance
_translation_manager = None

def get_translation_manager():
    """Get the global translation manager instance"""
    global _translation_manager
    if _translation_manager is None:
        _translation_manager = TranslationManager()
    return _translation_manager

def translate(key: str, lang: str = 'et', **kwargs) -> str:
    """
    Convenience function to translate a key
    
    Args:
        key: Translation key
        lang: Language code (et, en, ru)  
        **kwargs: Additional parameters for interpolation
        
    Returns:
        Translated string
    """
    manager = get_translation_manager()
    return manager.translate(key, lang, **kwargs)

# Template function alias
t = translate
