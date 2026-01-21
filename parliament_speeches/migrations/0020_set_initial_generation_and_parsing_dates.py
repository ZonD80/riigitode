# Data migration to set initial generation and parsing dates
from django.db import migrations
from django.utils import timezone
from datetime import datetime


def set_initial_dates(apps, schema_editor):
    """Set initial dates for existing records to September 30, 2025 23:00:00"""
    
    # Set default date: September 30, 2025 23:00:00 UTC
    # Use timezone.make_aware to create timezone-aware datetime
    naive_date = datetime(2025, 9, 30, 23, 0, 0)
    default_date = timezone.make_aware(naive_date, timezone.utc)
    
    # Get models
    Speech = apps.get_model('parliament_speeches', 'Speech')
    AgendaSummary = apps.get_model('parliament_speeches', 'AgendaSummary')
    AgendaDecision = apps.get_model('parliament_speeches', 'AgendaDecision')
    AgendaActivePolitician = apps.get_model('parliament_speeches', 'AgendaActivePolitician')
    PoliticianProfilePart = apps.get_model('parliament_speeches', 'PoliticianProfilePart')
    
    # Set parsed_at for all existing speeches
    speeches_count = Speech.objects.all().update(parsed_at=default_date)
    print(f"Set parsed_at for {speeches_count} speeches to {default_date}")
    
    # Set ai_summary_generated_at for speeches that have AI summaries
    speeches_with_summary = Speech.objects.filter(ai_summary__isnull=False).exclude(ai_summary='')
    speeches_summary_count = speeches_with_summary.update(ai_summary_generated_at=default_date)
    print(f"Set ai_summary_generated_at for {speeches_summary_count} speeches with AI summaries to {default_date}")
    
    # Set ai_summary_generated_at for all agenda summaries
    agenda_summaries_count = AgendaSummary.objects.all().update(ai_summary_generated_at=default_date)
    print(f"Set ai_summary_generated_at for {agenda_summaries_count} agenda summaries to {default_date}")
    
    # Set ai_summary_generated_at for all agenda decisions
    agenda_decisions_count = AgendaDecision.objects.all().update(ai_summary_generated_at=default_date)
    print(f"Set ai_summary_generated_at for {agenda_decisions_count} agenda decisions to {default_date}")
    
    # Set ai_summary_generated_at for all active politicians
    active_politicians_count = AgendaActivePolitician.objects.all().update(ai_summary_generated_at=default_date)
    print(f"Set ai_summary_generated_at for {active_politicians_count} active politicians to {default_date}")
    
    # Set ai_summary_generated_at for all politician profile parts
    profile_parts_count = PoliticianProfilePart.objects.all().update(ai_summary_generated_at=default_date)
    print(f"Set ai_summary_generated_at for {profile_parts_count} politician profile parts to {default_date}")


def reverse_initial_dates(apps, schema_editor):
    """Reverse the data migration by setting dates back to NULL"""
    
    # Get models
    Speech = apps.get_model('parliament_speeches', 'Speech')
    AgendaSummary = apps.get_model('parliament_speeches', 'AgendaSummary')
    AgendaDecision = apps.get_model('parliament_speeches', 'AgendaDecision')
    AgendaActivePolitician = apps.get_model('parliament_speeches', 'AgendaActivePolitician')
    PoliticianProfilePart = apps.get_model('parliament_speeches', 'PoliticianProfilePart')
    
    # Reset all dates to NULL
    Speech.objects.all().update(parsed_at=None, ai_summary_generated_at=None)
    AgendaSummary.objects.all().update(ai_summary_generated_at=None)
    AgendaDecision.objects.all().update(ai_summary_generated_at=None)
    AgendaActivePolitician.objects.all().update(ai_summary_generated_at=None)
    PoliticianProfilePart.objects.all().update(ai_summary_generated_at=None)


class Migration(migrations.Migration):

    dependencies = [
        ('parliament_speeches', '0019_add_ai_generation_and_parsing_dates'),
    ]

    operations = [
        migrations.RunPython(set_initial_dates, reverse_initial_dates),
    ]

