"""
Management command to clear speeches from the database while keeping agendas
"""
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from parliament_speeches.models import Speech


class Command(BaseCommand):
    help = 'Clear all speeches from the database (keeps agendas, sessions, and politicians)'

    def add_arguments(self, parser):
        parser.add_argument(
            '--confirm',
            action='store_true',
            help='Confirm that you want to delete all speeches'
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show what would be deleted without actually deleting'
        )

    def handle(self, *args, **options):
        # Count existing speeches
        speech_count = Speech.objects.count()
        
        if speech_count == 0:
            self.stdout.write(self.style.SUCCESS("No speeches found in the database."))
            return

        self.stdout.write(f"Found {speech_count} speeches in the database.")
        
        if options['dry_run']:
            self.stdout.write(self.style.WARNING("DRY RUN MODE - No data will be deleted"))
            self.stdout.write(f"Would delete {speech_count} speeches")
            return
        
        if not options['confirm']:
            self.stdout.write(
                self.style.ERROR(
                    "This will permanently delete all speeches from the database!\n"
                    "Agendas, sessions, and politicians will be kept.\n"
                    "Use --confirm flag if you're sure you want to proceed."
                )
            )
            return
        
        # Confirm deletion
        self.stdout.write(
            self.style.WARNING(
                f"WARNING: About to delete {speech_count} speeches permanently!\n"
                "This action cannot be undone."
            )
        )
        
        try:
            with transaction.atomic():
                deleted_count, deleted_details = Speech.objects.all().delete()
                
                self.stdout.write(
                    self.style.SUCCESS(
                        f"Successfully deleted {deleted_count} speeches from the database."
                    )
                )
                
                if deleted_details:
                    self.stdout.write("Deletion details:")
                    for model, count in deleted_details.items():
                        self.stdout.write(f"  {model}: {count}")
                        
        except Exception as e:
            raise CommandError(f"Error during deletion: {str(e)}")
