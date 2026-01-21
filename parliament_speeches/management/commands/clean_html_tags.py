"""
Management command to clean HTML tags from existing data
"""
import re
from django.core.management.base import BaseCommand
from django.utils.html import strip_tags
from django.db import transaction

from parliament_speeches.models import PlenarySession, AgendaItem, Speech


class Command(BaseCommand):
    help = 'Clean HTML tags from existing titles and text content'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Run without saving changes to database'
        )

    def handle(self, *args, **options):
        self.dry_run = options['dry_run']
        
        if self.dry_run:
            self.stdout.write(self.style.WARNING("DRY RUN MODE - No data will be saved"))

        try:
            with transaction.atomic():
                # Clean plenary session titles
                self.clean_plenary_sessions()
                
                # Clean agenda item titles
                self.clean_agenda_items()
                
                # Clean speech content
                self.clean_speeches()
                
                if self.dry_run:
                    # Rollback transaction in dry run mode
                    transaction.set_rollback(True)
                    
            self.stdout.write(self.style.SUCCESS("Successfully cleaned HTML tags"))
            
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"Error during cleaning: {str(e)}"))

    def clean_html_text(self, text):
        """Clean HTML tags and normalize whitespace from text"""
        if not text:
            return text
        
        # Strip HTML tags
        cleaned = strip_tags(text)
        
        # Normalize whitespace - replace multiple spaces/newlines with single space
        cleaned = re.sub(r'\s+', ' ', cleaned)
        
        # Strip leading and trailing whitespace
        cleaned = cleaned.strip()
        
        return cleaned

    def clean_plenary_sessions(self):
        """Clean HTML tags from plenary session titles"""
        self.stdout.write("Cleaning plenary session titles...")
        
        sessions = PlenarySession.objects.all()
        updated_count = 0
        
        for session in sessions:
            original_title = session.title
            cleaned_title = self.clean_html_text(original_title)
            
            if original_title != cleaned_title:
                if not self.dry_run:
                    session.title = cleaned_title
                    session.save(update_fields=['title'])
                    
                updated_count += 1
                self.stdout.write(f"Updated session: {session.pk}")
                
        self.stdout.write(f"Updated {updated_count} plenary session titles")

    def clean_agenda_items(self):
        """Clean HTML tags from agenda item titles"""
        self.stdout.write("Cleaning agenda item titles...")
        
        items = AgendaItem.objects.all()
        updated_count = 0
        
        for item in items:
            original_title = item.title
            cleaned_title = self.clean_html_text(original_title)
            
            if original_title != cleaned_title:
                if not self.dry_run:
                    item.title = cleaned_title
                    item.save(update_fields=['title'])
                    
                updated_count += 1
                self.stdout.write(f"Updated agenda item: {item.pk}")
                
        self.stdout.write(f"Updated {updated_count} agenda item titles")

    def clean_speeches(self):
        """Clean HTML tags from speech content"""
        self.stdout.write("Cleaning speech content...")
        
        speeches = Speech.objects.all()
        updated_count = 0
        
        for speech in speeches:
            original_speaker = speech.speaker
            original_text = speech.text
            
            cleaned_speaker = self.clean_html_text(original_speaker)
            cleaned_text = self.clean_html_text(original_text)
            
            if original_speaker != cleaned_speaker or original_text != cleaned_text:
                if not self.dry_run:
                    speech.speaker = cleaned_speaker
                    speech.text = cleaned_text
                    speech.save(update_fields=['speaker', 'text'])
                    
                updated_count += 1
                if updated_count % 100 == 0:
                    self.stdout.write(f"Updated {updated_count} speeches...")
                    
        self.stdout.write(f"Updated {updated_count} speeches")
