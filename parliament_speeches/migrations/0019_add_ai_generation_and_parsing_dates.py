# Generated migration
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('parliament_speeches', '0018_allow_null_politician_in_active_politician'),
    ]

    operations = [
        migrations.AddField(
            model_name='agendasummary',
            name='ai_summary_generated_at',
            field=models.DateTimeField(blank=True, help_text='AI kokkuvõtte genereerimise aeg', null=True),
        ),
        migrations.AddField(
            model_name='agendadecision',
            name='ai_summary_generated_at',
            field=models.DateTimeField(blank=True, help_text='AI kokkuvõtte genereerimise aeg', null=True),
        ),
        migrations.AddField(
            model_name='agendaactivepolitician',
            name='ai_summary_generated_at',
            field=models.DateTimeField(blank=True, help_text='AI kokkuvõtte genereerimise aeg', null=True),
        ),
        migrations.AddField(
            model_name='speech',
            name='ai_summary_generated_at',
            field=models.DateTimeField(blank=True, help_text='AI kokkuvõtte genereerimise aeg', null=True),
        ),
        migrations.AddField(
            model_name='speech',
            name='parsed_at',
            field=models.DateTimeField(blank=True, help_text='Parsimise aeg API-st', null=True),
        ),
        migrations.AddField(
            model_name='politicianprofilepart',
            name='ai_summary_generated_at',
            field=models.DateTimeField(blank=True, help_text='AI profiili genereerimise aeg', null=True),
        ),
    ]

