# Generated migration

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('parliament_speeches', '0025_mark_incomplete_entities'),
    ]

    operations = [
        # Add is_incomplete field to AgendaDecision
        migrations.AddField(
            model_name='agendadecision',
            name='is_incomplete',
            field=models.BooleanField(default=False, db_index=True, help_text='Kas otsus on puudulik (sisaldab puudulikke stenogramme)'),
        ),
        # Add is_incomplete field to AgendaActivePolitician
        migrations.AddField(
            model_name='agendaactivepolitician',
            name='is_incomplete',
            field=models.BooleanField(default=False, db_index=True, help_text='Kas aktiivsuse kirjeldus on puudulik (sisaldab puudulikke stenogramme)'),
        ),
    ]

