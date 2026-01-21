"""
Management command to parse speeches from Estonian Parliament API
"""
import requests
import logging
import re
import uuid
import hashlib
from datetime import datetime, timedelta
from dateutil.parser import parse as parse_date
from django.core.management.base import BaseCommand, CommandError
from django.conf import settings
from django.db import transaction
from django.utils import timezone
from django.utils.html import strip_tags
from django.core.files.base import ContentFile
from PIL import Image
from io import BytesIO

from parliament_speeches.models import (
    Politician, Faction, PoliticianFaction, PlenarySession, 
    AgendaItem, Speech, ParliamentParseError
)


logger = logging.getLogger(__name__)


class Command(BaseCommand):
    
    def log_error(self, error_type, error_message, entity_type=None, entity_id=None, 
                  entity_name=None, error_details=None):
        """Log an error to the ParliamentParseError model"""
        if self.dry_run:
            return
            
        try:
            ParliamentParseError.objects.create(
                error_type=error_type,
                error_message=error_message,
                error_details=error_details,
                entity_type=entity_type,
                entity_id=entity_id,
                entity_name=entity_name,
                year=self.parse_year
            )
        except Exception as e:
            logger.error(f"Failed to log parse error: {e}")
    
    def delete_incomplete_speeches(self, start_date, end_date):
        """Delete incomplete speeches in the given date range before parsing"""
        # Find all plenary sessions in the date range
        # Convert dates to timezone-aware datetimes
        from django.utils.timezone import make_aware
        start_datetime = make_aware(datetime.combine(start_date, datetime.min.time()))
        end_datetime = make_aware(datetime.combine(end_date, datetime.max.time()))
        
        sessions = PlenarySession.objects.filter(
            date__gte=start_datetime,
            date__lte=end_datetime
        )
        
        if not sessions.exists():
            self.stdout.write("No sessions found in date range, skipping incomplete speech deletion")
            return
        
        # Find all agenda items for these sessions
        agenda_items = AgendaItem.objects.filter(plenary_session__in=sessions)
        
        # Delete incomplete speeches
        deleted_speeches, _ = Speech.objects.filter(
            agenda_item__in=agenda_items,
            is_incomplete=True
        ).delete()
        
        if deleted_speeches > 0:
            self.stdout.write(f"Deleted {deleted_speeches} incomplete speeches from date range {start_date} to {end_date}")
            
            # After deleting speeches, update agenda and session incomplete flags
            for agenda_item in agenda_items:
                self.update_agenda_incomplete_flag(agenda_item)
        else:
            self.stdout.write("No incomplete speeches found in date range")
    
    help = 'Parse speeches from Estonian Parliament API for the last month'

    def add_arguments(self, parser):
        parser.add_argument(
            '--days',
            type=int,
            default=30,
            help='Number of days to look back (default: 30)'
        )
        parser.add_argument(
            '--start-date',
            type=str,
            help='Start date in YYYY-MM-DD format (overrides --days)'
        )
        parser.add_argument(
            '--end-date',
            type=str,
            help='End date in YYYY-MM-DD format (default: today)'
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Run without saving data to database'
        )
        parser.add_argument(
            '--verbose',
            action='store_true',
            help='Enable verbose logging to see detailed processing information'
        )

    def handle(self, *args, **options):
        self.dry_run = options['dry_run']
        self.verbose = options['verbose']
        
        # Configure logging level
        if self.verbose:
            logging.getLogger(__name__).setLevel(logging.DEBUG)
            logger.setLevel(logging.DEBUG)
        
        # Calculate date range
        if options['end_date']:
            end_date = datetime.strptime(options['end_date'], '%Y-%m-%d').date()
        else:
            end_date = datetime.now().date()
        
        if options['start_date']:
            start_date = datetime.strptime(options['start_date'], '%Y-%m-%d').date()
        else:
            # Calculate start_date from days parameter
            # Limit lookback to January 1st of the end_date's year
            days_back = options['days']
            calculated_start_date = end_date - timedelta(days=days_back)
            january_first = datetime(end_date.year, 1, 1).date()
            
            # Use the later of the two dates (don't go before January 1st)
            start_date = max(calculated_start_date, january_first)
            
            if calculated_start_date < january_first:
                self.stdout.write(
                    self.style.WARNING(
                        f"Lookback limited to January 1st, {end_date.year}. "
                        f"Requested {days_back} days would have gone to {calculated_start_date}."
                    )
                )
        
        # Validate that start_date and end_date are in the same year
        if start_date.year != end_date.year:
            raise CommandError(
                f"Start date ({start_date}) and end date ({end_date}) must be in the same year. "
                f"Please run separate parsing commands for each year."
            )
        
        # Store the year for error logging
        self.parse_year = start_date.year
        
        # Clear previous parse errors for this year only
        if not self.dry_run:
            deleted_count, _ = ParliamentParseError.objects.filter(year=self.parse_year).delete()
            self.stdout.write(f"Cleared {deleted_count} previous parse errors for year {self.parse_year}")

        self.stdout.write(f"Parsing speeches from {start_date} to {end_date} (Year: {self.parse_year})")
        
        # Delete incomplete speeches for this date range before parsing
        # This is necessary because our UUIDs are content-based hashes - if stenogram 
        # text changes from "Stenogramm on koostamisel" to actual content, the UUID changes
        if not self.dry_run:
            self.delete_incomplete_speeches(start_date, end_date)
        
        # Log parse run metadata (including date range)
        if not self.dry_run:
            parse_info = f"Parse run started for date range: {start_date} to {end_date}"
            self.log_error('OTHER', parse_info, 
                          entity_type='parse_run',
                          entity_id=f"{start_date}_to_{end_date}",
                          entity_name=f"Parse Run {timezone.now().strftime('%Y-%m-%d %H:%M')}",
                          error_details=f"Date range: {start_date} to {end_date}\nDays parameter: {options['days']}\nDry run: {self.dry_run}")
        
        if self.dry_run:
            self.stdout.write(self.style.WARNING("DRY RUN MODE - No data will be saved"))
        if self.verbose:
            self.stdout.write("Verbose logging enabled")

        try:
            # First, fetch and update politicians
            self.fetch_politicians()
            
            # Then fetch speeches/verbatims
            self.fetch_verbatims(start_date, end_date)
            
            self.stdout.write(self.style.SUCCESS("Successfully completed parsing"))
            
        except Exception as e:
            logger.exception("Error during parsing")
            self.log_error('OTHER', f"Critical error during parsing: {str(e)}", 
                          error_details=str(e))
            raise CommandError(f"Error during parsing: {str(e)}")

    def fetch_politicians(self):
        """Fetch and update politicians from API"""
        self.stdout.write("Fetching politicians...")
        
        url = f"{settings.PARLIAMENT_API_BASE_URL}/api/plenary-members"
        
        try:
            response = requests.get(url, timeout=30)
            response.raise_for_status()
            data = response.json()
            
            # Handle both list response and object with 'data' key
            if isinstance(data, list):
                politicians_data = data
            else:
                politicians_data = data.get('data', [])
            
            self.stdout.write(f"Found {len(politicians_data)} politicians to process")
            created_count = 0
            updated_count = 0
            photos_downloaded = 0
            photos_skipped = 0
            
            for i, politician_data in enumerate(politicians_data, 1):
                politician_name = politician_data.get('fullName', 'N/A')
                
                if not self.dry_run:
                    self.stdout.write(f"[{i}/{len(politicians_data)}] Processing: {politician_name}")
                    
                    politician, created = self.save_politician(politician_data)
                    if politician:
                        if created:
                            created_count += 1
                            self.stdout.write(f"  ✓ Created new politician: {politician_name}")
                        else:
                            updated_count += 1
                            if self.verbose:
                                self.stdout.write(f"  ↻ Updated existing politician: {politician_name}")
                        
                        # Fetch detailed politician data including photos
                        if politician.uuid:
                            # Check if photos already exist
                            if politician.photo and politician.photo_big:
                                photos_skipped += 1
                                if self.verbose:
                                    self.stdout.write(f"  📷 Photos already exist for: {politician_name}")
                            else:
                                if self.verbose:
                                    self.stdout.write(f"  📷 Fetching photos for: {politician_name}")
                                photo_downloaded = self.fetch_politician_details(politician)
                                if photo_downloaded:
                                    photos_downloaded += 1
                                    self.stdout.write(f"  ✓ Downloaded photo for: {politician_name}")
                                elif self.verbose:
                                    self.stdout.write(f"  ⚠ No photo available for: {politician_name}")
                    else:
                        self.stdout.write(f"  ✗ Failed to process: {politician_name}")
                else:
                    self.stdout.write(f"[{i}/{len(politicians_data)}] Would process: {politician_name}")
            
            if not self.dry_run:
                self.stdout.write(f"\n📊 Politicians Summary:")
                self.stdout.write(f"  • Created: {created_count}")
                self.stdout.write(f"  • Updated: {updated_count}")
                self.stdout.write(f"  • Photos downloaded: {photos_downloaded}")
                self.stdout.write(f"  • Photos skipped (already exist): {photos_skipped}")
                self.stdout.write(f"  • Total processed: {len(politicians_data)}")
            
        except requests.RequestException as e:
            error_msg = f"Failed to fetch politicians: {str(e)}"
            self.log_error('API_CONNECTION', error_msg, entity_type='politician', 
                          error_details=str(e))
            raise CommandError(error_msg)

    def save_politician(self, politician_data):
        """Save or update a politician"""
        uuid = politician_data.get('uuid')
        if not uuid:
            error_msg = f"Politician without UUID"
            logger.warning(f"{error_msg}: {politician_data}")
            self.log_error('MISSING_DATA', error_msg, entity_type='politician',
                          entity_name=politician_data.get('fullName', 'Unknown'),
                          error_details=str(politician_data))
            return None, False
        
        # Parse date of birth
        date_of_birth = None
        if politician_data.get('dateOfBirth'):
            try:
                date_of_birth = parse_date(politician_data['dateOfBirth']).date()
            except Exception as e:
                error_msg = f"Failed to parse date of birth: {e}"
                logger.warning(f"{error_msg} for {politician_data.get('fullName', 'Unknown')}")
                self.log_error('DATA_PARSING', error_msg, entity_type='politician',
                              entity_id=politician_data.get('uuid'),
                              entity_name=politician_data.get('fullName', 'Unknown'),
                              error_details=str(e))
        
        # Convert parliament seniority from days to years
        parliament_seniority = politician_data.get('parliamentSeniority')
        if parliament_seniority is not None:
            # Convert days to years (approximately)
            parliament_seniority = round(parliament_seniority / 365.25, 1)
        
        politician_defaults = {
            'first_name': politician_data.get('firstName', ''),
            'last_name': politician_data.get('lastName', ''),
            'full_name': politician_data.get('fullName', ''),
            'active': politician_data.get('active', True),
            'email': politician_data.get('email', ''),
            'phone': politician_data.get('phone', ''),
            'gender': politician_data.get('gender', ''),
            'date_of_birth': date_of_birth,
            'parliament_seniority': parliament_seniority,
        }
        
        politician, created = Politician.objects.update_or_create(
            uuid=uuid,
            defaults=politician_defaults
        )
        
        # Handle factions
        factions_data = politician_data.get('factions', [])
        for faction_data in factions_data:
            self.save_faction_membership(politician, faction_data)
        
        return politician, created

    def fetch_politician_details(self, politician):
        """Fetch detailed politician data including photos from API"""
        if not politician.uuid:
            return False
            
        # Skip if politician already has photos
        if politician.photo and politician.photo_big:
            if self.verbose:
                logger.debug(f"Politician {politician.full_name} already has both photos, skipping")
            return False
        elif politician.photo:
            if self.verbose:
                logger.debug(f"Politician {politician.full_name} already has standard photo, checking for big photo only")
        elif politician.photo_big:
            if self.verbose:
                logger.debug(f"Politician {politician.full_name} already has big photo, checking for standard photo only")
            
        url = f"{settings.PARLIAMENT_API_BASE_URL}/api/plenary-members/{politician.uuid}"
        
        try:
            response = requests.get(url, timeout=30)
            response.raise_for_status()
            detailed_data = response.json()
            
            # Download and save photos
            photo_downloaded = False
            
            # Download standard photo (only if not already exists)
            if (not politician.photo and 
                detailed_data.get('photo') and 
                detailed_data['photo'].get('_links', {}).get('self', {}).get('href')):
                photo_url = detailed_data['photo']['_links']['self']['href'] + '/download'
                if self.download_and_save_photo(politician, photo_url, 'photo', detailed_data['photo']):
                    photo_downloaded = True
                    
            # Download big photo (only if not already exists)
            if (not politician.photo_big and 
                detailed_data.get('photoBig') and 
                detailed_data['photoBig'].get('_links', {}).get('self', {}).get('href')):
                photo_url = detailed_data['photoBig']['_links']['self']['href'] + '/download'
                if self.download_and_save_photo(politician, photo_url, 'photo_big', detailed_data['photoBig']):
                    photo_downloaded = True
                    
            if photo_downloaded:
                logger.info(f"Downloaded photos for politician: {politician.full_name}")
                
            return photo_downloaded
            
        except requests.RequestException as e:
            error_msg = f"Failed to fetch details: {str(e)}"
            logger.warning(f"{error_msg} for politician {politician.full_name}")
            self.log_error('API_CONNECTION', error_msg, entity_type='politician',
                          entity_id=politician.uuid, entity_name=politician.full_name,
                          error_details=str(e))
            return False
        except Exception as e:
            error_msg = f"Error processing photos: {str(e)}"
            logger.error(f"{error_msg} for politician {politician.full_name}")
            self.log_error('PHOTO_DOWNLOAD', error_msg, entity_type='politician',
                          entity_id=politician.uuid, entity_name=politician.full_name,
                          error_details=str(e))
            return False

    def download_and_save_photo(self, politician, photo_url, field_name, photo_metadata):
        """Download and save a photo for a politician"""
        try:
            # Download the photo
            response = requests.get(photo_url, timeout=30)
            response.raise_for_status()
            
            # Verify it's an image
            content_type = response.headers.get('content-type', '').lower()
            if not content_type.startswith('image/'):
                logger.warning(f"Invalid content type for photo {photo_url}: {content_type}")
                return False
                
            # Get image data
            image_data = response.content
            
            # Verify image can be processed
            try:
                image = Image.open(BytesIO(image_data))
                image.verify()
            except Exception as e:
                logger.warning(f"Invalid image data for photo {photo_url}: {str(e)}")
                return False
                
            # Generate filename
            photo_uuid = photo_metadata.get('uuid', '')
            original_filename = photo_metadata.get('fileName', 'photo.jpg')
            file_extension = photo_metadata.get('fileExtension', 'jpg')
            
            # Create a safe filename
            safe_filename = f"{politician.uuid}_{field_name}.{file_extension}"
            
            # Save photo metadata
            if field_name == 'photo':
                politician.photo_uuid = photo_uuid
                politician.photo_filename = original_filename
                politician.photo_extension = file_extension
                
            # Save the image file
            content_file = ContentFile(image_data, name=safe_filename)
            field = getattr(politician, field_name)
            field.save(safe_filename, content_file, save=False)
            
            # Save the politician model
            politician.save()
            
            logger.debug(f"Saved {field_name} for politician {politician.full_name}: {safe_filename}")
            return True
            
        except requests.RequestException as e:
            error_msg = f"Failed to download photo: {str(e)}"
            logger.warning(f"{error_msg} from {photo_url}")
            self.log_error('PHOTO_DOWNLOAD', error_msg, entity_type='politician',
                          entity_id=politician.uuid, entity_name=politician.full_name,
                          error_details=f"URL: {photo_url}, Error: {str(e)}")
            return False
        except Exception as e:
            error_msg = f"Error saving photo: {str(e)}"
            logger.error(f"{error_msg} for politician {politician.full_name}")
            self.log_error('PHOTO_DOWNLOAD', error_msg, entity_type='politician',
                          entity_id=politician.uuid, entity_name=politician.full_name,
                          error_details=str(e))
            return False

    def save_faction_membership(self, politician, faction_data):
        """Save faction membership for a politician"""
        faction_uuid = faction_data.get('uuid')
        if not faction_uuid:
            return
        
        # Create or get faction
        faction, _ = Faction.objects.get_or_create(
            uuid=faction_uuid,
            defaults={'name': faction_data.get('name', '')}
        )
        
        # Parse dates
        start_date = None
        end_date = None
        
        if faction_data.get('startDate'):
            try:
                start_date = parse_date(faction_data['startDate']).date()
            except Exception as e:
                logger.warning(f"Failed to parse faction start date: {e}")
                pass
                
        if faction_data.get('endDate'):
            try:
                end_date = parse_date(faction_data['endDate']).date()
            except Exception as e:
                logger.warning(f"Failed to parse faction end date: {e}")
                pass
        
        # Create faction membership
        PoliticianFaction.objects.get_or_create(
            politician=politician,
            faction=faction,
            start_date=start_date,
            defaults={'end_date': end_date}
        )

    def fetch_verbatims(self, start_date, end_date):
        """Fetch verbatims (transcripts) from API"""
        self.stdout.write(f"Fetching verbatims from {start_date} to {end_date}...")
        
        url = f"{settings.PARLIAMENT_API_BASE_URL}/api/steno/verbatims"
        params = {
            'startDate': start_date.strftime('%Y-%m-%d'),
            'endDate': end_date.strftime('%Y-%m-%d'),
            'type': 'IS'  # Plenary sessions
        }
        
        try:
            response = requests.get(url, params=params, timeout=60)
            response.raise_for_status()
            verbatims_data = response.json()
            
            # Handle both list response and object with 'data' key
            if isinstance(verbatims_data, dict) and 'data' in verbatims_data:
                verbatims_data = verbatims_data['data']
            elif not isinstance(verbatims_data, list):
                self.stdout.write(self.style.WARNING(f"Unexpected verbatims response format: {type(verbatims_data)}"))
                verbatims_data = []
            
            self.stdout.write(f"Found {len(verbatims_data)} sessions to process")
            sessions_count = 0
            speeches_count = 0
            skipped_sessions = 0
            event_types_stats = {}  # Track different event types
            processing_stats = {
                'speeches_created': 0,
                'speeches_already_existed': 0,
                'speeches_skipped': 0,
                'uuid_generated': 0,
                'uuid_from_api': 0,
                'created_by_type': {}  # Track created speeches by event type
            }
            
            for i, verbatim in enumerate(verbatims_data, 1):
                session_title = verbatim.get('title', 'N/A')
                session_date = verbatim.get('date', 'N/A')
                session_uuid = verbatim.get('uuid', verbatim.get('id'))
                
                if not self.dry_run:
                    self.stdout.write(f"\n[{i}/{len(verbatims_data)}] Processing session: {session_title}")
                    self.stdout.write(f"  📅 Date: {session_date}")
                    if session_uuid:
                        self.stdout.write(f"  🆔 UUID: {session_uuid}")
                    
                    session_speeches = self.process_verbatim(verbatim, event_types_stats, processing_stats)
                    if session_speeches > 0:
                        sessions_count += 1
                        speeches_count += session_speeches
                        self.stdout.write(f"  ✓ Processed {session_speeches} speeches from this session")
                    else:
                        skipped_sessions += 1
                        self.stdout.write(self.style.WARNING(f"  ⚠ Skipped session (no speeches found)"))
                        
                        # Log this as an error
                        session_identifier = f"{verbatim.get('membership', 'N/A')}-{verbatim.get('plenarySession', 'N/A')}"
                        error_msg = f"Session has no speeches or agenda items"
                        error_details = f"Session UUID: {session_uuid}\n"
                        error_details += f"Membership: {verbatim.get('membership', 'N/A')}\n"
                        error_details += f"Plenary Session: {verbatim.get('plenarySession', 'N/A')}\n"
                        error_details += f"Date: {session_date}\n"
                        error_details += f"Edited: {verbatim.get('edited', False)}\n"
                        error_details += f"Agenda Items Count: {len(verbatim.get('agendaItems', []))}"
                        
                        self.log_error('MISSING_DATA', error_msg, 
                                      entity_type='session',
                                      entity_id=session_uuid or session_identifier,
                                      entity_name=session_title,
                                      error_details=error_details)
                else:
                    self.stdout.write(f"[{i}/{len(verbatims_data)}] Would process session: {session_title}")
            
            if not self.dry_run:
                self.stdout.write(f"Processed {sessions_count} sessions with {speeches_count} speeches")
                if skipped_sessions > 0:
                    self.stdout.write(self.style.WARNING(f"Skipped {skipped_sessions} sessions (no speeches found)"))
                
                # Show processing statistics
                self.stdout.write(f"\nSpeech Processing Summary:")
                self.stdout.write(f"  Created (new): {processing_stats['speeches_created']}")
                
                # Show created speeches by event type
                if processing_stats['created_by_type']:
                    self.stdout.write(f"    Created by event type:")
                    for event_type, count in sorted(processing_stats['created_by_type'].items()):
                        self.stdout.write(f"      {event_type}: {count}")
                
                self.stdout.write(f"  Already existed: {processing_stats['speeches_already_existed']}")
                self.stdout.write(f"  Skipped (non-speech/invalid): {processing_stats['speeches_skipped']}")
                self.stdout.write(f"  Content-based UUIDs generated: {processing_stats['uuid_generated']}")
                self.stdout.write(f"  API provided UUIDs (ignored): {processing_stats['uuid_from_api']}")
                total_processed = processing_stats['speeches_created'] + processing_stats['speeches_already_existed'] + processing_stats['speeches_skipped']
                self.stdout.write(f"  Total events processed: {total_processed}")
                
                # Show event types statistics
                if event_types_stats:
                    self.stdout.write("\nEvent types found:")
                    for event_type, count in sorted(event_types_stats.items()):
                        self.stdout.write(f"  {event_type}: {count}")
            
        except requests.RequestException as e:
            error_msg = f"Failed to fetch verbatims: {str(e)}"
            self.log_error('API_CONNECTION', error_msg, entity_type='session',
                          error_details=f"Date range: {start_date} to {end_date}, Error: {str(e)}")
            raise CommandError(error_msg)

    @transaction.atomic
    def process_verbatim(self, verbatim_data, event_types_stats=None, processing_stats=None):
        """Process a single verbatim (session transcript)"""
        speeches_count = 0
        
        # Create plenary session
        try:
            session_date = parse_date(verbatim_data['date'])
        except Exception as e:
            error_msg = f"Failed to parse session date: {e}"
            logger.warning(error_msg)
            self.log_error('DATA_PARSING', error_msg, entity_type='session',
                          entity_name=verbatim_data.get('title', 'Unknown'),
                          error_details=str(e))
            return 0
            
        plenary_session, _ = PlenarySession.objects.get_or_create(
            membership=verbatim_data['membership'],
            plenary_session=verbatim_data['plenarySession'],
            date=session_date,
            defaults={
                'title': self.clean_html_text(verbatim_data.get('title', '')),
                'edited': verbatim_data.get('edited', False)
            }
        )
        
        # Process agenda items
        agenda_items = verbatim_data.get('agendaItems', [])
        for agenda_item_data in agenda_items:
            speeches_count += self.process_agenda_item(plenary_session, agenda_item_data, event_types_stats, processing_stats)
        
        return speeches_count

    def process_agenda_item(self, plenary_session, agenda_item_data, event_types_stats=None, processing_stats=None):
        """Process an agenda item and its events/speeches"""
        speeches_count = 0
        skipped_events = 0
        
        # Create agenda item
        agenda_item_uuid = agenda_item_data.get('agendaItemUuid')
        if not agenda_item_uuid:
            error_msg = "Agenda item without UUID"
            logger.warning(f"{error_msg}: {agenda_item_data.get('title', 'N/A')}")
            self.log_error('MISSING_DATA', error_msg, entity_type='agenda',
                          entity_name=agenda_item_data.get('title', 'Unknown'),
                          error_details=str(agenda_item_data))
            return 0
        
        try:
            agenda_date = parse_date(agenda_item_data['date'])
        except Exception as e:
            error_msg = f"Failed to parse agenda item date: {e}"
            logger.warning(error_msg)
            self.log_error('DATA_PARSING', error_msg, entity_type='agenda',
                          entity_id=agenda_item_uuid,
                          entity_name=agenda_item_data.get('title', 'Unknown'),
                          error_details=str(e))
            return 0
            
        agenda_item, _ = AgendaItem.objects.get_or_create(
            uuid=agenda_item_uuid,
            defaults={
                'plenary_session': plenary_session,
                'date': agenda_date,
                'title': self.clean_html_text(agenda_item_data.get('title', ''))
            }
        )
        
        # Process events (speeches)
        events = agenda_item_data.get('events', [])
        total_events = len(events)
        
        for event_data in events:
            # Track event types
            event_type = event_data.get('type', 'UNKNOWN')
            if event_types_stats is not None:
                event_types_stats[event_type] = event_types_stats.get(event_type, 0) + 1
            
            result = self.process_speech_event(agenda_item, event_data, processing_stats)
            if result == 'created':
                speeches_count += 1
                if processing_stats:
                    processing_stats['speeches_created'] += 1
                    # Track created speeches by event type
                    processing_stats['created_by_type'][event_type] = processing_stats['created_by_type'].get(event_type, 0) + 1
            elif result == 'existed':
                if processing_stats:
                    processing_stats['speeches_already_existed'] += 1
            else:  # result == False (skipped)
                skipped_events += 1
                if processing_stats:
                    processing_stats['speeches_skipped'] += 1
        
        if total_events > 0:
            logger.info(f"Agenda item '{agenda_item.title[:50]}...': {speeches_count} speeches processed, {skipped_events} events skipped")
        
        # Calculate and update total time for this agenda item
        self.calculate_agenda_total_time(agenda_item)
        
        # Check if agenda has any incomplete speeches and propagate the flag
        self.update_agenda_incomplete_flag(agenda_item)
        
        return speeches_count

    def process_speech_event(self, agenda_item, event_data, processing_stats=None):
        """Process a speech event"""
        event_uuid = event_data.get('uuid')
        event_type = event_data.get('type', 'SPEECH')
        
        # Only process speech events
        if event_type != 'SPEECH':
            logger.debug(f"Skipping non-speech event: {event_type}")
            return False
        
        try:
            event_date = parse_date(event_data['date'])
        except Exception as e:
            error_msg = f"Failed to parse event date: {e}"
            logger.warning(error_msg)
            self.log_error('DATA_PARSING', error_msg, entity_type='speech',
                          entity_id=event_data.get('uuid'),
                          entity_name=event_data.get('speaker', 'Unknown'),
                          error_details=str(e))
            return False
            
        speaker_name = self.clean_html_text(event_data.get('speaker', ''))
        text = self.clean_html_text(event_data.get('text', ''))
        
        # Skip if no text content
        if not text or len(text.strip()) == 0:
            logger.debug(f"Skipping speech event with no text content for speaker: {speaker_name}")
            return False
        
        # Check if speech is incomplete (stenogram being prepared)
        is_incomplete = "stenogramm on koostamisel" in text.lower()
        if is_incomplete:
            # Normalize incomplete speech text to standard message
            text = "Stenogramm on koostamisel"
            
            error_msg = "Missing stenogram"
            error_details = f"Speech contains 'Stenogramm on koostamisel'\nSpeaker: {speaker_name}\nDate: {event_date}"
            self.log_error('MISSING_STENOGRAM', error_msg, entity_type='speech',
                          entity_id=event_data.get('uuid'),
                          entity_name=speaker_name,
                          error_details=error_details)
            logger.info(f"Incomplete speech detected for {speaker_name}, text normalized to: {text}")
        
        # Try to match speaker to politician
        politician = self.find_politician_by_name(speaker_name)
        
        # Always generate our own deterministic UUID based on content + context
        # This ensures uniqueness and avoids API UUID duplication issues
        unique_content = f"{agenda_item.uuid}_{event_date.isoformat()}_{speaker_name}_{text}"
        content_hash = hashlib.sha256(unique_content.encode('utf-8')).hexdigest()
        event_uuid = str(uuid.uuid5(uuid.NAMESPACE_DNS, content_hash))
        
        # Track statistics
        if processing_stats:
            processing_stats['uuid_generated'] += 1
            # Still track if API provided a UUID (for debugging purposes)
            if event_data.get('uuid'):
                processing_stats['uuid_from_api'] += 1
        
        try:
            # Create speech
            speech, created = Speech.objects.get_or_create(
                uuid=event_uuid,
                defaults={
                    'agenda_item': agenda_item,
                    'politician': politician,
                    'event_type': event_type,
                    'date': event_date,
                    'speaker': speaker_name,
                    'text': text,
                    'link': event_data.get('link', ''),
                    'is_incomplete': is_incomplete,
                    'parsed_at': timezone.now()
                }
            )
            
            if created:
                logger.debug(f"Created speech: {speaker_name} - {text[:50]}... (UUID: {event_uuid[:8]}...)")
                return 'created'
            else:
                # Since we're using content-based UUIDs, if it already exists, it's truly the same content
                logger.debug(f"Speech already exists: {speaker_name} - {text[:50]}... (UUID: {event_uuid[:8]}...)")
                return 'existed'
            
        except Exception as e:
            error_msg = f"Failed to create speech: {e}"
            logger.error(f"{error_msg} for {speaker_name}")
            self.log_error('DATABASE', error_msg, entity_type='speech',
                          entity_id=event_uuid,
                          entity_name=speaker_name,
                          error_details=str(e))
            return False

    def find_politician_by_name(self, speaker_name):
        """Try to find a politician by speaker name"""
        if not speaker_name:
            return None
        
        # Try exact match first
        politician = Politician.objects.filter(full_name__iexact=speaker_name).first()
        if politician:
            return politician
        
        # Try partial matches
        name_parts = speaker_name.split()
        if len(name_parts) >= 2:
            first_name = name_parts[0]
            last_name = ' '.join(name_parts[1:])
            
            politician = Politician.objects.filter(
                first_name__iexact=first_name,
                last_name__iexact=last_name
            ).first()
            
            if politician:
                return politician
        
        return None
    
    def clean_html_text(self, text):
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

    def calculate_agenda_total_time(self, agenda_item):
        """Calculate and update the total time for an agenda item based on speech intervals"""
        speeches = agenda_item.speeches.filter(event_type='SPEECH').order_by('date')
        
        if speeches.count() < 2:
            # Need at least 2 speeches to calculate time intervals
            logger.debug(f"Agenda item {agenda_item.pk} has less than 2 speeches, cannot calculate duration")
            return
        
        # Calculate total time from first speech to last speech
        first_speech = speeches.first()
        last_speech = speeches.last()
        
        if first_speech and last_speech:
            duration_seconds = int((last_speech.date - first_speech.date).total_seconds())
            
            # Update the agenda item
            agenda_item.total_time_seconds = duration_seconds
            agenda_item.save(update_fields=['total_time_seconds'])
            
            logger.info(f"Updated agenda item {agenda_item.pk} total time: {duration_seconds} seconds ({duration_seconds//60} minutes)")
        else:
            logger.warning(f"Could not calculate duration for agenda item {agenda_item.pk}")
    
    def update_agenda_incomplete_flag(self, agenda_item):
        """Check if agenda has incomplete speeches and update the is_incomplete flag"""
        has_incomplete = agenda_item.speeches.filter(
            event_type='SPEECH',
            is_incomplete=True
        ).exists()
        
        if has_incomplete and not agenda_item.is_incomplete:
            agenda_item.is_incomplete = True
            agenda_item.save(update_fields=['is_incomplete'])
            logger.info(f"Marked agenda item {agenda_item.pk} as incomplete")
        elif not has_incomplete and agenda_item.is_incomplete:
            # If all incomplete speeches were removed, mark as complete
            agenda_item.is_incomplete = False
            agenda_item.save(update_fields=['is_incomplete'])
            logger.info(f"Marked agenda item {agenda_item.pk} as complete")
        
        # Propagate to plenary session
        self.update_plenary_session_incomplete_flag(agenda_item.plenary_session)
    
    def update_plenary_session_incomplete_flag(self, plenary_session):
        """Check if plenary session has incomplete agendas and update the is_incomplete flag"""
        has_incomplete = plenary_session.agenda_items.filter(
            is_incomplete=True
        ).exists()
        
        if has_incomplete and not plenary_session.is_incomplete:
            plenary_session.is_incomplete = True
            plenary_session.save(update_fields=['is_incomplete'])
            logger.info(f"Marked plenary session {plenary_session.pk} as incomplete")
        elif not has_incomplete and plenary_session.is_incomplete:
            # If all incomplete agendas were removed, mark as complete
            plenary_session.is_incomplete = False
            plenary_session.save(update_fields=['is_incomplete'])
            logger.info(f"Marked plenary session {plenary_session.pk} as complete")

    def calculate_politician_total_time(self, politician):
        """Calculate and update the total speaking time for a politician"""
        speeches = politician.speeches.filter(event_type='SPEECH').order_by('date')
        
        if not speeches.exists():
            logger.debug(f"Politician {politician.pk} has no speeches")
            return
        
        # Group speeches by agenda item to calculate speaking time per agenda
        from collections import defaultdict
        agenda_groups = defaultdict(list)
        
        for speech in speeches:
            agenda_groups[speech.agenda_item_id].append(speech)
        
        total_speaking_seconds = 0
        
        for agenda_id, agenda_speeches in agenda_groups.items():
            if len(agenda_speeches) < 2:
                # Single speech, estimate 30 seconds per speech
                total_speaking_seconds += 30 * len(agenda_speeches)
                continue
            
            # Sort speeches by date
            agenda_speeches.sort(key=lambda x: x.date)
            
            # Calculate intervals between consecutive speeches by this politician
            for i in range(len(agenda_speeches) - 1):
                current_speech = agenda_speeches[i]
                next_speech = agenda_speeches[i + 1]
                
                # Calculate time between speeches (assume this is speaking time)
                interval_seconds = (next_speech.date - current_speech.date).total_seconds()
                
                # Cap individual speech time at 30 minutes to avoid outliers
                if interval_seconds > 1800:  # 30 minutes
                    interval_seconds = 1800
                elif interval_seconds < 10:  # Minimum 10 seconds
                    interval_seconds = 10
                    
                total_speaking_seconds += interval_seconds
            
            # Add time for the last speech (estimate 30 seconds)
            total_speaking_seconds += 30
        
        # Update the politician
        politician.total_time_seconds = int(total_speaking_seconds)
        politician.save(update_fields=['total_time_seconds'])
        
        logger.info(f"Updated politician {politician.pk} ({politician.full_name}) total speaking time: {int(total_speaking_seconds)} seconds ({int(total_speaking_seconds)//60} minutes)")
