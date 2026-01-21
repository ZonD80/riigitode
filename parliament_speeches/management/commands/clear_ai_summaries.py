"""
Management command to clear AI summaries from various models
"""
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from parliament_speeches.models import (
    AgendaItem, Speech, PoliticianProfilePart, 
    MediaReaction, 
    Politician, PlenarySession
)


class Command(BaseCommand):
    help = 'Clear AI summaries from database based on specified parameters. Use --except-politician-id to exclude speeches by a specific politician.'

    def add_arguments(self, parser):
        # ID-based filters
        parser.add_argument(
            '--agenda-id',
            type=int,
            help='Clear AI summaries for specific agenda item ID'
        )
        parser.add_argument(
            '--politician-id',
            type=int,
            help='Clear AI summaries for specific politician ID'
        )
        parser.add_argument(
            '--plenary-session-id',
            type=int,
            help='Clear AI summaries for all agenda items in specific plenary session ID'
        )
        parser.add_argument(
            '--speech-id',
            type=int,
            help='Clear AI summary for specific speech ID'
        )
        parser.add_argument(
            '--for-speeches-in-agenda-id',
            type=int,
            help='Clear AI summaries for all speeches in specific agenda item ID'
        )
        parser.add_argument(
            '--for-speeches-in-plenary-session-id',
            type=int,
            help='Clear AI summaries for all speeches in specific plenary session ID'
        )
        parser.add_argument(
            '--for-speeches-of-politician-id',
            type=int,
            help='Clear AI summaries for all speeches by specific politician ID'
        )
        parser.add_argument(
            '--except-politician-id',
            type=int,
            help='Exclude speeches by this politician ID from clearing (works with other speech-clearing parameters)'
        )
        
        # Control options
        parser.add_argument(
            '--confirm',
            action='store_true',
            help='Confirm that you want to clear AI summaries'
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show what would be cleared without actually clearing'
        )
        parser.add_argument(
            '--verbose',
            action='store_true',
            help='Show detailed information about what is being cleared'
        )

    def handle(self, *args, **options):
        # Check if any parameters are provided
        has_params = any([
            options['agenda_id'], 
            options['politician_id'], 
            options['plenary_session_id'], 
            options['speech_id'],
            options['for_speeches_in_agenda_id'],
            options['for_speeches_in_plenary_session_id'],
            options['for_speeches_of_politician_id']
        ])
        
        # Collect items to clear
        agenda_items_to_clear = []
        speeches_to_clear = []
        politician_profiles_to_clear = []
        media_reactions_to_clear = []
        
        # If no parameters provided, clear everything
        if not has_params:
            # Get all items with AI summaries
            all_agenda_items = AgendaItem.objects.all()
            for agenda_item in all_agenda_items:
                if self._has_ai_summary(agenda_item):
                    agenda_items_to_clear.append(agenda_item)
            
            all_speeches = Speech.objects.all()
            for speech in all_speeches:
                if self._has_speech_ai_summary(speech) and not self._should_exclude_speech(speech, options):
                    speeches_to_clear.append(speech)
            
            all_profiles = PoliticianProfilePart.objects.all()
            for profile in all_profiles:
                if self._has_profile_ai_summary(profile):
                    politician_profiles_to_clear.append(profile)
            
            all_reactions = MediaReaction.objects.all()
            for reaction in all_reactions:
                if self._has_media_reaction_ai_summary(reaction):
                    media_reactions_to_clear.append(reaction)
        else:
            # Process specific parameters
            
            # Process agenda-id parameter
            if options['agenda_id']:
                try:
                    agenda_item = AgendaItem.objects.get(id=options['agenda_id'])
                    if self._has_ai_summary(agenda_item):
                        agenda_items_to_clear.append(agenda_item)
                    
                    # Also get speeches for this agenda item
                    speeches = Speech.objects.filter(agenda_item=agenda_item)
                    for speech in speeches:
                        if self._has_speech_ai_summary(speech) and not self._should_exclude_speech(speech, options):
                            speeches_to_clear.append(speech)
                            
                except AgendaItem.DoesNotExist:
                    raise CommandError(f"Agenda item with ID {options['agenda_id']} does not exist")
            
            # Process speech-id parameter
            if options['speech_id']:
                try:
                    speech = Speech.objects.get(id=options['speech_id'])
                    if self._has_speech_ai_summary(speech) and not self._should_exclude_speech(speech, options):
                        speeches_to_clear.append(speech)
                except Speech.DoesNotExist:
                    raise CommandError(f"Speech with ID {options['speech_id']} does not exist")
            
            # Process plenary-session-id parameter
            if options['plenary_session_id']:
                try:
                    plenary_session = PlenarySession.objects.get(id=options['plenary_session_id'])
                    agenda_items = AgendaItem.objects.filter(plenary_session=plenary_session)
                    
                    for agenda_item in agenda_items:
                        if self._has_ai_summary(agenda_item):
                            agenda_items_to_clear.append(agenda_item)
                        
                        # Also get speeches for each agenda item
                        speeches = Speech.objects.filter(agenda_item=agenda_item)
                        for speech in speeches:
                            if self._has_speech_ai_summary(speech) and not self._should_exclude_speech(speech, options):
                                speeches_to_clear.append(speech)
                                
                except PlenarySession.DoesNotExist:
                    raise CommandError(f"Plenary session with ID {options['plenary_session_id']} does not exist")
            
            # Process politician-id parameter
            if options['politician_id']:
                try:
                    politician = Politician.objects.get(id=options['politician_id'])
                    
                    # Get politician profiles
                    profiles = PoliticianProfilePart.objects.filter(politician=politician)
                    for profile in profiles:
                        if self._has_profile_ai_summary(profile):
                            politician_profiles_to_clear.append(profile)
                    
                    # Get media reactions
                    media_reactions = MediaReaction.objects.filter(politician=politician)
                    for reaction in media_reactions:
                        if self._has_media_reaction_ai_summary(reaction):
                            media_reactions_to_clear.append(reaction)
                    
                    # Get speeches by this politician
                    speeches = Speech.objects.filter(politician=politician)
                    for speech in speeches:
                        if self._has_speech_ai_summary(speech) and not self._should_exclude_speech(speech, options):
                            speeches_to_clear.append(speech)
                            
                except Politician.DoesNotExist:
                    raise CommandError(f"Politician with ID {options['politician_id']} does not exist")
            
            # Process for-speeches-in-agenda-id parameter
            if options['for_speeches_in_agenda_id']:
                try:
                    agenda_item = AgendaItem.objects.get(id=options['for_speeches_in_agenda_id'])
                    speeches = Speech.objects.filter(agenda_item=agenda_item)
                    for speech in speeches:
                        if self._has_speech_ai_summary(speech) and not self._should_exclude_speech(speech, options):
                            speeches_to_clear.append(speech)
                except AgendaItem.DoesNotExist:
                    raise CommandError(f"Agenda item with ID {options['for_speeches_in_agenda_id']} does not exist")
            
            # Process for-speeches-in-plenary-session-id parameter
            if options['for_speeches_in_plenary_session_id']:
                try:
                    plenary_session = PlenarySession.objects.get(id=options['for_speeches_in_plenary_session_id'])
                    agenda_items = AgendaItem.objects.filter(plenary_session=plenary_session)
                    for agenda_item in agenda_items:
                        speeches = Speech.objects.filter(agenda_item=agenda_item)
                        for speech in speeches:
                            if self._has_speech_ai_summary(speech) and not self._should_exclude_speech(speech, options):
                                speeches_to_clear.append(speech)
                except PlenarySession.DoesNotExist:
                    raise CommandError(f"Plenary session with ID {options['for_speeches_in_plenary_session_id']} does not exist")
            
            # Process for-speeches-of-politician-id parameter
            if options['for_speeches_of_politician_id']:
                try:
                    politician = Politician.objects.get(id=options['for_speeches_of_politician_id'])
                    speeches = Speech.objects.filter(politician=politician)
                    for speech in speeches:
                        if self._has_speech_ai_summary(speech) and not self._should_exclude_speech(speech, options):
                            speeches_to_clear.append(speech)
                except Politician.DoesNotExist:
                    raise CommandError(f"Politician with ID {options['for_speeches_of_politician_id']} does not exist")
        
        # Remove duplicates
        agenda_items_to_clear = list(set(agenda_items_to_clear))
        speeches_to_clear = list(set(speeches_to_clear))
        politician_profiles_to_clear = list(set(politician_profiles_to_clear))
        media_reactions_to_clear = list(set(media_reactions_to_clear))
        
        # Count total items
        total_items = (
            len(agenda_items_to_clear) + 
            len(speeches_to_clear) + 
            len(politician_profiles_to_clear) + 
            len(media_reactions_to_clear)
        )
        
        if total_items == 0:
            self.stdout.write(self.style.SUCCESS("No AI summaries found to clear with the given parameters."))
            return
        
        # Display what will be cleared
        self.stdout.write(f"Found {total_items} items with AI summaries to clear:")
        if agenda_items_to_clear:
            self.stdout.write(f"  - {len(agenda_items_to_clear)} agenda items")
        if speeches_to_clear:
            self.stdout.write(f"  - {len(speeches_to_clear)} speeches")
        if politician_profiles_to_clear:
            self.stdout.write(f"  - {len(politician_profiles_to_clear)} politician profiles")
        if media_reactions_to_clear:
            self.stdout.write(f"  - {len(media_reactions_to_clear)} media reactions")
        
        # Verbose output
        if options['verbose']:
            self._show_verbose_details(
                agenda_items_to_clear, speeches_to_clear, 
                politician_profiles_to_clear,
                media_reactions_to_clear
            )
        
        # Dry run mode
        if options['dry_run']:
            self.stdout.write(self.style.WARNING("DRY RUN MODE - No data will be cleared"))
            return
        
        # Confirm before clearing
        if not options['confirm']:
            self.stdout.write(
                self.style.ERROR(
                    f"This will permanently clear AI summaries from {total_items} items!\n"
                    "Use --confirm flag if you're sure you want to proceed."
                )
            )
            return
        
        # Perform the clearing
        self.stdout.write(
            self.style.WARNING(
                f"WARNING: About to clear AI summaries from {total_items} items permanently!\n"
                "This action cannot be undone."
            )
        )
        
        try:
            with transaction.atomic():
                cleared_count = 0
                
                # Clear agenda item AI summaries (structured summaries)
                for agenda_item in agenda_items_to_clear:
                    try:
                        if hasattr(agenda_item, 'structured_summary'):
                            agenda_item.structured_summary.delete()
                            cleared_count += 1
                    except Exception as e:
                        self.stdout.write(self.style.ERROR(f"Error clearing structured summary for agenda {agenda_item.id}: {e}"))
                
                # Clear speech AI summaries
                for speech in speeches_to_clear:
                    speech.ai_summary = None
                    speech.ai_summary_en = None
                    speech.ai_summary_ru = None
                    speech.save()
                    cleared_count += 1
                
                # Clear politician profile AI summaries
                for profile in politician_profiles_to_clear:
                    profile.analysis = ""
                    profile.analysis_en = None
                    profile.analysis_ru = None
                    profile.save()
                    cleared_count += 1
                
                # Clear media reaction AI summaries
                for reaction in media_reactions_to_clear:
                    reaction.media_analysis_et = ""
                    reaction.media_analysis_en = None
                    reaction.media_analysis_ru = None
                    reaction.media_summary_et = ""
                    reaction.media_summary_en = None
                    reaction.media_summary_ru = None
                    reaction.save()
                    cleared_count += 1
                
                self.stdout.write(
                    self.style.SUCCESS(
                        f"Successfully cleared AI summaries from {cleared_count} items."
                    )
                )
                
        except Exception as e:
            raise CommandError(f"Error during clearing: {str(e)}")
    
    def _has_ai_summary(self, agenda_item):
        """Check if agenda item has any AI summary"""
        return hasattr(agenda_item, 'structured_summary')
    
    def _has_speech_ai_summary(self, speech):
        """Check if speech has any AI summary"""
        return bool(
            speech.ai_summary or 
            speech.ai_summary_en or 
            speech.ai_summary_ru
        )
    
    def _has_profile_ai_summary(self, profile):
        """Check if politician profile has any AI analysis"""
        return bool(
            profile.analysis or 
            profile.analysis_en or 
            profile.analysis_ru
        )
    
    def _has_media_reaction_ai_summary(self, reaction):
        """Check if media reaction has any AI analysis"""
        return bool(
            reaction.media_analysis_et or 
            reaction.media_analysis_en or 
            reaction.media_analysis_ru or
            reaction.media_summary_et or
            reaction.media_summary_en or
            reaction.media_summary_ru
        )
    
    def _show_verbose_details(self, agenda_items, speeches, profiles, reactions):
        """Show detailed information about items to be cleared"""
        if agenda_items:
            self.stdout.write("\nAgenda Items:")
            for item in agenda_items[:10]:  # Show max 10 items
                self.stdout.write(f"  - ID {item.id}: {item.title[:80]}...")
            if len(agenda_items) > 10:
                self.stdout.write(f"  ... and {len(agenda_items) - 10} more")
        
        if speeches:
            self.stdout.write("\nSpeeches:")
            for speech in speeches[:10]:  # Show max 10 items
                self.stdout.write(f"  - ID {speech.id}: {speech.speaker} - {speech.date.date()}")
            if len(speeches) > 10:
                self.stdout.write(f"  ... and {len(speeches) - 10} more")
        
        if profiles:
            self.stdout.write("\nPolitician Profiles:")
            for profile in profiles[:10]:  # Show max 10 items
                self.stdout.write(f"  - ID {profile.id}: {profile.politician.full_name} - {profile.get_category_display()}")
            if len(profiles) > 10:
                self.stdout.write(f"  ... and {len(profiles) - 10} more")
        
        if reactions:
            self.stdout.write("\nMedia Reactions:")
            for reaction in reactions[:10]:  # Show max 10 items
                self.stdout.write(f"  - ID {reaction.id}: {reaction.politician.full_name} - {reaction.get_category_display()}")
            if len(reactions) > 10:
                self.stdout.write(f"  ... and {len(reactions) - 10} more")
    
    def _should_exclude_speech(self, speech, options):
        """Check if speech should be excluded based on --except-politician-id parameter"""
        except_politician_id = options.get('except_politician_id')
        if except_politician_id and speech.politician and speech.politician.id == except_politician_id:
            return True
        return False
