"""
Management command to debug the translation system
"""
from django.core.management.base import BaseCommand
from parliament_speeches.translation import get_translation_manager
import os

class Command(BaseCommand):
    help = 'Debug the translation system'

    def handle(self, *args, **options):
        self.stdout.write("Debugging translation system...")
        
        manager = get_translation_manager()
        
        # Check what's loaded
        self.stdout.write(f"I18NEXT_AVAILABLE: {hasattr(manager, 'translate_func') and manager.translate_func is not None}")
        self.stdout.write(f"Fallback translations loaded: {bool(manager.fallback_translations)}")
        
        if manager.fallback_translations:
            for lang, translations in manager.fallback_translations.items():
                self.stdout.write(f"{lang}: {len(translations)} keys loaded")
                if 'SITE_NAME' in translations:
                    self.stdout.write(f"  SITE_NAME = '{translations['SITE_NAME']}'")
        
        # Test direct access
        self.stdout.write("\nTesting direct fallback access:")
        et_translations = manager.fallback_translations.get('et', {})
        site_name = et_translations.get('SITE_NAME', 'NOT_FOUND')
        self.stdout.write(f"Direct access to et.SITE_NAME: '{site_name}'")
        
        # Test the translate method
        from parliament_speeches.translation import translate
        result = translate('SITE_NAME', 'et')
        self.stdout.write(f"translate('SITE_NAME', 'et'): '{result}'")
        
        # Test pyi18next directly
        if manager.translate_func:
            self.stdout.write("\nTesting pyi18next directly:")
            try:
                direct_result = manager.translate_func('et', 'SITE_NAME')
                self.stdout.write(f"manager.translate_func('et', 'SITE_NAME'): '{direct_result}'")
            except Exception as e:
                self.stdout.write(f"Error calling translate_func: {e}")
        
        self.stdout.write("\nDebug completed!")
