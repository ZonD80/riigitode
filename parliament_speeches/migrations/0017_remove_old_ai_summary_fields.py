# Generated manually to remove old ai_summary fields from AgendaItem

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('parliament_speeches', '0016_remove_obsolete_politician_profile'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='agendaitem',
            name='ai_summary',
        ),
        migrations.RemoveField(
            model_name='agendaitem',
            name='ai_summary_en',
        ),
        migrations.RemoveField(
            model_name='agendaitem',
            name='ai_summary_ru',
        ),
    ]
