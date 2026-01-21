# Generated data migration to mark existing incomplete decisions and active politicians

from django.db import migrations


def mark_incomplete_decisions(apps, schema_editor):
    """Mark agenda decisions with incomplete speeches as incomplete"""
    AgendaDecision = apps.get_model('parliament_speeches', 'AgendaDecision')
    Speech = apps.get_model('parliament_speeches', 'Speech')
    
    # Get all agenda decisions
    decisions = AgendaDecision.objects.all()
    count = 0
    
    for decision in decisions:
        # Check if the related agenda has any incomplete speeches
        has_incomplete = Speech.objects.filter(
            agenda_item=decision.agenda_item,
            event_type='SPEECH',
            is_incomplete=True
        ).exists()
        
        if has_incomplete:
            decision.is_incomplete = True
            decision.save(update_fields=['is_incomplete'])
            count += 1
    
    print(f"Marked {count} agenda decisions as incomplete")


def mark_incomplete_active_politicians(apps, schema_editor):
    """Mark agenda active politicians with incomplete speeches as incomplete"""
    AgendaActivePolitician = apps.get_model('parliament_speeches', 'AgendaActivePolitician')
    Speech = apps.get_model('parliament_speeches', 'Speech')
    
    # Get all agenda active politicians
    active_politicians = AgendaActivePolitician.objects.all()
    count = 0
    
    for active_politician in active_politicians:
        # Check if the related agenda has any incomplete speeches
        has_incomplete = Speech.objects.filter(
            agenda_item=active_politician.agenda_item,
            event_type='SPEECH',
            is_incomplete=True
        ).exists()
        
        if has_incomplete:
            active_politician.is_incomplete = True
            active_politician.save(update_fields=['is_incomplete'])
            count += 1
    
    print(f"Marked {count} agenda active politicians as incomplete")


def reverse_mark_incomplete(apps, schema_editor):
    """Reverse the migration by unmarking all entities"""
    AgendaDecision = apps.get_model('parliament_speeches', 'AgendaDecision')
    AgendaActivePolitician = apps.get_model('parliament_speeches', 'AgendaActivePolitician')
    
    AgendaDecision.objects.all().update(is_incomplete=False)
    AgendaActivePolitician.objects.all().update(is_incomplete=False)


class Migration(migrations.Migration):

    dependencies = [
        ('parliament_speeches', '0026_add_is_incomplete_to_decisions_and_active'),
    ]

    operations = [
        migrations.RunPython(mark_incomplete_decisions, reverse_mark_incomplete),
        migrations.RunPython(mark_incomplete_active_politicians, reverse_mark_incomplete),
    ]

