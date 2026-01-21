# Generated migration

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('parliament_speeches', '0023_set_existing_errors_to_2025'),
    ]

    operations = [
        # Add is_incomplete field to Speech
        migrations.AddField(
            model_name='speech',
            name='is_incomplete',
            field=models.BooleanField(default=False, db_index=True, help_text='Kas stenogramm on puudulik (koostamisel)'),
        ),
        # Add is_incomplete field to AgendaItem
        migrations.AddField(
            model_name='agendaitem',
            name='is_incomplete',
            field=models.BooleanField(default=False, db_index=True, help_text='Kas päevakord sisaldab puudulikke stenogramme'),
        ),
        # Add is_incomplete field to PlenarySession
        migrations.AddField(
            model_name='plenarysession',
            name='is_incomplete',
            field=models.BooleanField(default=False, db_index=True, help_text='Kas istung sisaldab puudulikke stenogramme'),
        ),
        # Add is_incomplete field to AgendaSummary
        migrations.AddField(
            model_name='agendasummary',
            name='is_incomplete',
            field=models.BooleanField(default=False, db_index=True, help_text='Kas kokkuvõte on puudulik (sisaldab puudulikke stenogramme)'),
        ),
        # Add is_incomplete field to PoliticianProfilePart
        migrations.AddField(
            model_name='politicianprofilepart',
            name='is_incomplete',
            field=models.BooleanField(default=False, db_index=True, help_text='Kas profiil on puudulik (sisaldab puudulikke stenogramme)'),
        ),
        # Add MISSING_STENOGRAM to ParliamentParseError ERROR_TYPES choices
        migrations.AlterField(
            model_name='parliamentparseerror',
            name='error_type',
            field=models.CharField(
                choices=[
                    ('API_CONNECTION', 'API Connection Error'),
                    ('DATA_PARSING', 'Data Parsing Error'),
                    ('MISSING_DATA', 'Missing Required Data'),
                    ('MISSING_STENOGRAM', 'Missing Stenogram'),
                    ('VALIDATION', 'Data Validation Error'),
                    ('DATABASE', 'Database Error'),
                    ('PHOTO_DOWNLOAD', 'Photo Download Error'),
                    ('OTHER', 'Other Error'),
                ],
                default='OTHER',
                help_text='Vea tüüp',
                max_length=50
            ),
        ),
    ]

