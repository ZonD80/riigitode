# Generated migration to clean HTML tags from existing data

import re
from django.db import migrations
from django.utils.html import strip_tags


def clean_html_text(text):
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


def clean_html_tags_forward(apps, schema_editor):
    """Clean HTML tags from existing data"""
    PlenarySession = apps.get_model('parliament_speeches', 'PlenarySession')
    AgendaItem = apps.get_model('parliament_speeches', 'AgendaItem')
    Speech = apps.get_model('parliament_speeches', 'Speech')
    
    # Clean plenary session titles
    for session in PlenarySession.objects.all():
        original_title = session.title
        cleaned_title = clean_html_text(original_title)
        if original_title != cleaned_title:
            session.title = cleaned_title
            session.save(update_fields=['title'])
    
    # Clean agenda item titles
    for item in AgendaItem.objects.all():
        original_title = item.title
        cleaned_title = clean_html_text(original_title)
        if original_title != cleaned_title:
            item.title = cleaned_title
            item.save(update_fields=['title'])
    
    # Clean speech content
    for speech in Speech.objects.all():
        original_speaker = speech.speaker
        original_text = speech.text
        
        cleaned_speaker = clean_html_text(original_speaker)
        cleaned_text = clean_html_text(original_text)
        
        if original_speaker != cleaned_speaker or original_text != cleaned_text:
            speech.speaker = cleaned_speaker
            speech.text = cleaned_text
            speech.save(update_fields=['speaker', 'text'])


def clean_html_tags_reverse(apps, schema_editor):
    """Reverse migration - no action needed"""
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('parliament_speeches', '0001_initial'),
    ]

    operations = [
        migrations.RunPython(clean_html_tags_forward, clean_html_tags_reverse),
    ]