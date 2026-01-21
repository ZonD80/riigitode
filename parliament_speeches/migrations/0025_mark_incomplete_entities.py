# Generated data migration to mark existing incomplete entities

from django.db import migrations


def mark_incomplete_speeches(apps, schema_editor):
    """Mark speeches with 'Stenogramm on koostamisel' as incomplete"""
    Speech = apps.get_model('parliament_speeches', 'Speech')
    
    # Find all speeches containing "Stenogramm on koostamisel" (case-insensitive)
    incomplete_speeches = Speech.objects.filter(
        event_type='SPEECH',
        text__icontains='stenogramm on koostamisel'
    )
    
    count = incomplete_speeches.update(is_incomplete=True)
    print(f"Marked {count} speeches as incomplete")


def mark_incomplete_agendas(apps, schema_editor):
    """Mark agendas with incomplete speeches as incomplete"""
    AgendaItem = apps.get_model('parliament_speeches', 'AgendaItem')
    Speech = apps.get_model('parliament_speeches', 'Speech')
    
    # Get all agenda items
    agendas = AgendaItem.objects.all()
    count = 0
    
    for agenda in agendas:
        # Check if this agenda has any incomplete speeches
        has_incomplete = Speech.objects.filter(
            agenda_item=agenda,
            event_type='SPEECH',
            is_incomplete=True
        ).exists()
        
        if has_incomplete:
            agenda.is_incomplete = True
            agenda.save(update_fields=['is_incomplete'])
            count += 1
    
    print(f"Marked {count} agendas as incomplete")


def mark_incomplete_plenary_sessions(apps, schema_editor):
    """Mark plenary sessions with incomplete agendas as incomplete"""
    PlenarySession = apps.get_model('parliament_speeches', 'PlenarySession')
    AgendaItem = apps.get_model('parliament_speeches', 'AgendaItem')
    
    # Get all plenary sessions
    sessions = PlenarySession.objects.all()
    count = 0
    
    for session in sessions:
        # Check if this session has any incomplete agendas
        has_incomplete = AgendaItem.objects.filter(
            plenary_session=session,
            is_incomplete=True
        ).exists()
        
        if has_incomplete:
            session.is_incomplete = True
            session.save(update_fields=['is_incomplete'])
            count += 1
    
    print(f"Marked {count} plenary sessions as incomplete")


def mark_incomplete_agenda_summaries(apps, schema_editor):
    """Mark agenda summaries with incomplete speeches as incomplete"""
    AgendaSummary = apps.get_model('parliament_speeches', 'AgendaSummary')
    Speech = apps.get_model('parliament_speeches', 'Speech')
    
    # Get all agenda summaries
    summaries = AgendaSummary.objects.all()
    count = 0
    
    for summary in summaries:
        # Check if the related agenda has any incomplete speeches
        has_incomplete = Speech.objects.filter(
            agenda_item=summary.agenda_item,
            event_type='SPEECH',
            is_incomplete=True
        ).exists()
        
        if has_incomplete:
            summary.is_incomplete = True
            summary.save(update_fields=['is_incomplete'])
            count += 1
    
    print(f"Marked {count} agenda summaries as incomplete")


def mark_incomplete_politician_profiles(apps, schema_editor):
    """Mark politician profiles with incomplete speeches as incomplete"""
    PoliticianProfilePart = apps.get_model('parliament_speeches', 'PoliticianProfilePart')
    Speech = apps.get_model('parliament_speeches', 'Speech')
    
    # Get all politician profile parts
    profiles = PoliticianProfilePart.objects.all()
    count = 0
    
    for profile in profiles:
        # Build query for speeches related to this profile based on period type
        has_incomplete = False
        
        if profile.period_type == 'AGENDA' and profile.agenda_item:
            has_incomplete = Speech.objects.filter(
                agenda_item=profile.agenda_item,
                politician=profile.politician,
                event_type='SPEECH',
                is_incomplete=True
            ).exists()
        elif profile.period_type == 'PLENARY_SESSION' and profile.plenary_session:
            has_incomplete = Speech.objects.filter(
                agenda_item__plenary_session=profile.plenary_session,
                politician=profile.politician,
                event_type='SPEECH',
                is_incomplete=True
            ).exists()
        elif profile.period_type == 'MONTH' and profile.month:
            # Parse month (format: MM.YYYY)
            month_parts = profile.month.split('.')
            if len(month_parts) == 2:
                month_num, year = int(month_parts[0]), int(month_parts[1])
                has_incomplete = Speech.objects.filter(
                    politician=profile.politician,
                    event_type='SPEECH',
                    date__month=month_num,
                    date__year=year,
                    is_incomplete=True
                ).exists()
        elif profile.period_type == 'YEAR' and profile.year:
            has_incomplete = Speech.objects.filter(
                politician=profile.politician,
                event_type='SPEECH',
                date__year=profile.year,
                is_incomplete=True
            ).exists()
        elif profile.period_type == 'ALL':
            has_incomplete = Speech.objects.filter(
                politician=profile.politician,
                event_type='SPEECH',
                is_incomplete=True
            ).exists()
        
        if has_incomplete:
            profile.is_incomplete = True
            profile.save(update_fields=['is_incomplete'])
            count += 1
    
    print(f"Marked {count} politician profiles as incomplete")


def reverse_mark_incomplete(apps, schema_editor):
    """Reverse the migration by unmarking all entities"""
    Speech = apps.get_model('parliament_speeches', 'Speech')
    AgendaItem = apps.get_model('parliament_speeches', 'AgendaItem')
    PlenarySession = apps.get_model('parliament_speeches', 'PlenarySession')
    AgendaSummary = apps.get_model('parliament_speeches', 'AgendaSummary')
    PoliticianProfilePart = apps.get_model('parliament_speeches', 'PoliticianProfilePart')
    
    Speech.objects.all().update(is_incomplete=False)
    AgendaItem.objects.all().update(is_incomplete=False)
    PlenarySession.objects.all().update(is_incomplete=False)
    AgendaSummary.objects.all().update(is_incomplete=False)
    PoliticianProfilePart.objects.all().update(is_incomplete=False)


class Migration(migrations.Migration):

    dependencies = [
        ('parliament_speeches', '0024_add_is_incomplete_field'),
    ]

    operations = [
        migrations.RunPython(mark_incomplete_speeches, reverse_mark_incomplete),
        migrations.RunPython(mark_incomplete_agendas, reverse_mark_incomplete),
        migrations.RunPython(mark_incomplete_plenary_sessions, reverse_mark_incomplete),
        migrations.RunPython(mark_incomplete_agenda_summaries, reverse_mark_incomplete),
        migrations.RunPython(mark_incomplete_politician_profiles, reverse_mark_incomplete),
    ]

