# Generated manually for adding ai_summary field to AgendaItem

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('parliament_speeches', '0003_add_ai_summary_field'),
    ]

    operations = [
        migrations.AddField(
            model_name='agendaitem',
            name='ai_summary',
            field=models.TextField(blank=True, help_text='AI kokkuvõte päevakorrapunktist', null=True),
        ),
    ]
