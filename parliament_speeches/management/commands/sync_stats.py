"""
Management command to gather and update system statistics
"""
from django.core.management.base import BaseCommand
from django.db.models import Count, Q
from parliament_speeches.models import (
    StatisticsEntry, Speech, AgendaItem, PlenarySession, Politician, 
    AgendaSummary, PoliticianProfilePart, AgendaDecision, AgendaActivePolitician
)


class Command(BaseCommand):
    help = 'Gather and update system statistics in StatisticsEntry model'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show what would be updated without making changes'
        )

    def handle(self, *args, **options):
        dry_run = options.get('dry_run', False)
        
        if dry_run:
            self.stdout.write(self.style.WARNING("🔍 DRY RUN MODE - No statistics will be saved"))
        
        self.stdout.write("📊 Gathering system statistics...")
        
        # Define all statistics to collect
        statistics = [
            # Basic counts
            self.get_speeches_total(),
            self.get_agenda_items_total(),
            self.get_speech_ai_summaries_count(),
            self.get_structured_agenda_summaries_count(),
            self.get_structured_politician_profiles_available(),
            self.get_structured_politician_profiles_total_required(),
            self.get_plenary_sessions_count(),
            
            # Incomplete entity counts
            self.get_incomplete_speeches_count(),
            self.get_incomplete_agendas_count(),
            self.get_incomplete_plenary_sessions_count(),
            self.get_incomplete_politician_profiles_count(),
            
            # Agenda structured data
            self.get_agenda_decisions_count(),
            self.get_agenda_active_politicians_count(),
            
            # Translation coverage for English
            self.get_agenda_ai_summaries_en_coverage(),
            self.get_agenda_titles_en_coverage(),
            self.get_speech_ai_summaries_en_coverage(),
            self.get_agenda_decisions_en_coverage(),
            self.get_agenda_active_politicians_en_coverage(),
            self.get_politician_profiles_en_coverage(),
            
            # Translation coverage for Russian
            self.get_agenda_ai_summaries_ru_coverage(),
            self.get_agenda_titles_ru_coverage(),
            self.get_speech_ai_summaries_ru_coverage(),
            self.get_agenda_decisions_ru_coverage(),
            self.get_agenda_active_politicians_ru_coverage(),
            self.get_politician_profiles_ru_coverage(),
        ]
        
        # Update or create statistics entries
        for stat_data in statistics:
            if not dry_run:
                entry, created = StatisticsEntry.objects.update_or_create(
                    name=stat_data['name'],
                    defaults={
                        'name_ru': stat_data['name_ru'],
                        'name_en': stat_data['name_en'],
                        'value': stat_data['value'],
                        'percentage': stat_data.get('percentage'),
                    }
                )
                action = "Created" if created else "Updated"
                self.stdout.write(f"✅ {action}: {stat_data['name']} = {stat_data['value']}" + 
                                (f" ({stat_data['percentage']}%)" if stat_data.get('percentage') is not None else ""))
            else:
                self.stdout.write(f"🔍 Would update: {stat_data['name']} = {stat_data['value']}" + 
                                (f" ({stat_data['percentage']}%)" if stat_data.get('percentage') is not None else ""))
        
        self.stdout.write(self.style.SUCCESS(f"📊 Statistics gathering completed! ({len(statistics)} entries)"))

    def get_speeches_total(self):
        """Get total number of speeches"""
        count = Speech.objects.filter(event_type='SPEECH').count()
        return {
            'name': 'Kõned kokku',
            'name_en': 'Total Speeches',
            'name_ru': 'Всего выступлений',
            'value': count
        }

    def get_agenda_items_total(self):
        """Get total number of agenda items"""
        count = AgendaItem.objects.count()
        return {
            'name': 'Päevakorrapunktid kokku',
            'name_en': 'Total Agenda Items',
            'name_ru': 'Всего пунктов повестки',
            'value': count
        }

    def get_speech_ai_summaries_count(self):
        """Get number of speeches with AI summaries"""
        total_speeches = Speech.objects.filter(event_type='SPEECH').count()
        with_ai = Speech.objects.filter(
            event_type='SPEECH',
            ai_summary__isnull=False
        ).exclude(ai_summary='').count()
        
        percentage = round((with_ai / total_speeches * 100), 1) if total_speeches > 0 else 0
        
        return {
            'name': 'Kõnede AI kokkuvõtted',
            'name_en': 'Speech AI Summaries',
            'name_ru': 'ИИ-резюме выступлений',
            'value': with_ai,
            'percentage': percentage
        }

    def get_structured_agenda_summaries_count(self):
        """Get number of structured agenda summaries"""
        count = AgendaSummary.objects.count()
        total_agendas = AgendaItem.objects.count()
        percentage = round((count / total_agendas * 100), 1) if total_agendas > 0 else 0
        
        return {
            'name': 'Struktureeritud päevakorra kokkuvõtted',
            'name_en': 'Structured Agenda Summaries',
            'name_ru': 'Структурированные резюме повестки',
            'value': count,
            'percentage': percentage
        }

    def get_structured_politician_profiles_available(self):
        """Get number of structured politician profiles available with completion percentage"""
        available_count = PoliticianProfilePart.objects.count()
        
        # Calculate total required (reuse the logic from get_structured_politician_profiles_total_required)
        total_required = self._calculate_total_required_profiles()
        
        # Calculate percentage
        percentage = round((available_count / total_required * 100), 1) if total_required > 0 else 0
        
        return {
            'name': 'Struktureeritud poliitiku profiilid saadaval',
            'name_en': 'Structured Politician Profiles Available',
            'name_ru': 'Доступные структурированные профили политиков',
            'value': available_count,
            'percentage': percentage
        }

    def _calculate_total_required_profiles(self):
        """Helper method to calculate total required politician profiles"""
        from django.db.models import Count
        
        # Get active politicians who have speeches with their speech data
        politicians_with_speeches = Politician.objects.filter(
            active=True,
            speeches__event_type='SPEECH'
        ).annotate(
            speech_count=Count('speeches', filter=Q(speeches__event_type='SPEECH'))
        ).filter(speech_count__gt=0)
        
        # Based on profile_politician.py, there are 10 profile categories
        profile_categories_count = len(PoliticianProfilePart.PROFILE_CATEGORIES)
        
        total_required = 0
        
        for politician in politicians_with_speeches:
            # Get unique periods for this politician (same logic as profile_politician.py)
            speeches = Speech.objects.filter(
                politician=politician,
                event_type='SPEECH'
            ).select_related('agenda_item__plenary_session')
            
            if not speeches.exists():
                continue
            
            # Collect unique periods (same as _collect_periods_from_speeches method)
            agenda_ids = set()
            plenary_ids = set()
            months = set()
            years = set()
            
            for speech in speeches:
                agenda_ids.add(speech.agenda_item.id)
                plenary_ids.add(speech.agenda_item.plenary_session.id)
                months.add(f"{speech.date.month:02d}.{speech.date.year}")
                years.add(speech.date.year)
            
            # Calculate profiles needed for this politician
            # For each category: agenda profiles + plenary profiles + month profiles + year profiles + 1 ALL profile
            profiles_per_category = len(agenda_ids) + len(plenary_ids) + len(months) + len(years) + 1
            politician_total = profiles_per_category * profile_categories_count
            total_required += politician_total
        
        return total_required

    def get_structured_politician_profiles_total_required(self):
        """Compute total required politician profiles based on profile_politician.py logic"""
        total_required = self._calculate_total_required_profiles()
        
        return {
            'name': 'Struktureeritud poliitiku profiilid kokku vaja',
            'name_en': 'Structured Politician Profiles Total Required',
            'name_ru': 'Всего требуется структурированных профилей политиков',
            'value': total_required
        }

    def get_plenary_sessions_count(self):
        """Get total number of plenary sessions"""
        count = PlenarySession.objects.count()
        return {
            'name': 'Istungjärgud',
            'name_en': 'Plenary Sessions',
            'name_ru': 'Пленарные заседания',
            'value': count
        }

    def get_agenda_ai_summaries_en_coverage(self):
        """Get English translation coverage for agenda AI summaries"""
        total_with_ai = AgendaSummary.objects.count()
        
        with_en = AgendaSummary.objects.filter(
            summary_text_en__isnull=False
        ).exclude(summary_text_en='').count()
        
        percentage = round((with_en / total_with_ai * 100), 1) if total_with_ai > 0 else 0
        
        return {
            'name': 'Päevakorra AI kokkuvõtted inglise keeles',
            'name_en': 'Agenda AI Summaries in English',
            'name_ru': 'ИИ-резюме повестки на английском',
            'value': with_en,
            'percentage': percentage
        }

    def get_agenda_titles_en_coverage(self):
        """Get English translation coverage for agenda titles"""
        total_agendas = AgendaItem.objects.count()
        with_en = AgendaItem.objects.filter(
            title_en__isnull=False
        ).exclude(title_en='').count()
        
        percentage = round((with_en / total_agendas * 100), 1) if total_agendas > 0 else 0
        
        return {
            'name': 'Päevakorra pealkirjad inglise keeles',
            'name_en': 'Agenda Titles in English',
            'name_ru': 'Заголовки повестки на английском',
            'value': with_en,
            'percentage': percentage
        }

    def get_speech_ai_summaries_en_coverage(self):
        """Get English translation coverage for speech AI summaries"""
        total_with_ai = Speech.objects.filter(
            event_type='SPEECH',
            ai_summary__isnull=False
        ).exclude(ai_summary='').count()
        
        with_en = Speech.objects.filter(
            event_type='SPEECH',
            ai_summary__isnull=False,
            ai_summary_en__isnull=False
        ).exclude(ai_summary='').exclude(ai_summary_en='').count()
        
        percentage = round((with_en / total_with_ai * 100), 1) if total_with_ai > 0 else 0
        
        return {
            'name': 'Kõnede AI kokkuvõtted inglise keeles',
            'name_en': 'Speech AI Summaries in English',
            'name_ru': 'ИИ-резюме выступлений на английском',
            'value': with_en,
            'percentage': percentage
        }

    def get_agenda_ai_summaries_ru_coverage(self):
        """Get Russian translation coverage for agenda AI summaries"""
        total_with_ai = AgendaSummary.objects.count()
        
        with_ru = AgendaSummary.objects.filter(
            summary_text_ru__isnull=False
        ).exclude(summary_text_ru='').count()
        
        percentage = round((with_ru / total_with_ai * 100), 1) if total_with_ai > 0 else 0
        
        return {
            'name': 'Päevakorra AI kokkuvõtted vene keeles',
            'name_en': 'Agenda AI Summaries in Russian',
            'name_ru': 'ИИ-резюме повестки на русском',
            'value': with_ru,
            'percentage': percentage
        }

    def get_agenda_titles_ru_coverage(self):
        """Get Russian translation coverage for agenda titles"""
        total_agendas = AgendaItem.objects.count()
        with_ru = AgendaItem.objects.filter(
            title_ru__isnull=False
        ).exclude(title_ru='').count()
        
        percentage = round((with_ru / total_agendas * 100), 1) if total_agendas > 0 else 0
        
        return {
            'name': 'Päevakorra pealkirjad vene keeles',
            'name_en': 'Agenda Titles in Russian',
            'name_ru': 'Заголовки повестки на русском',
            'value': with_ru,
            'percentage': percentage
        }

    def get_speech_ai_summaries_ru_coverage(self):
        """Get Russian translation coverage for speech AI summaries"""
        total_with_ai = Speech.objects.filter(
            event_type='SPEECH',
            ai_summary__isnull=False
        ).exclude(ai_summary='').count()
        
        with_ru = Speech.objects.filter(
            event_type='SPEECH',
            ai_summary__isnull=False,
            ai_summary_ru__isnull=False
        ).exclude(ai_summary='').exclude(ai_summary_ru='').count()
        
        percentage = round((with_ru / total_with_ai * 100), 1) if total_with_ai > 0 else 0
        
        return {
            'name': 'Kõnede AI kokkuvõtted vene keeles',
            'name_en': 'Speech AI Summaries in Russian',
            'name_ru': 'ИИ-резюме выступлений на русском',
            'value': with_ru,
            'percentage': percentage
        }

    def get_agenda_decisions_count(self):
        """Get total number of agenda decisions"""
        count = AgendaDecision.objects.count()
        total_agendas = AgendaItem.objects.count()
        
        # Calculate how many agendas have at least one decision
        agendas_with_decisions = AgendaItem.objects.filter(decisions__isnull=False).distinct().count()
        percentage = round((agendas_with_decisions / total_agendas * 100), 1) if total_agendas > 0 else 0
        
        return {
            'name': 'Päevakorra otsused',
            'name_en': 'Agenda Decisions',
            'name_ru': 'Решения повестки',
            'value': count,
            'percentage': percentage
        }

    def get_agenda_active_politicians_count(self):
        """Get total number of agenda items with active politicians identified"""
        count = AgendaActivePolitician.objects.count()
        total_agendas = AgendaItem.objects.count()
        percentage = round((count / total_agendas * 100), 1) if total_agendas > 0 else 0
        
        return {
            'name': 'Aktiivsed poliitikud päevakorras',
            'name_en': 'Active Politicians in Agendas',
            'name_ru': 'Активные политики в повестке',
            'value': count,
            'percentage': percentage
        }

    def get_agenda_decisions_en_coverage(self):
        """Get English translation coverage for agenda decisions"""
        total_decisions = AgendaDecision.objects.count()
        
        with_en = AgendaDecision.objects.filter(
            decision_text_en__isnull=False
        ).exclude(decision_text_en='').count()
        
        percentage = round((with_en / total_decisions * 100), 1) if total_decisions > 0 else 0
        
        return {
            'name': 'Päevakorra otsused inglise keeles',
            'name_en': 'Agenda Decisions in English',
            'name_ru': 'Решения повестки на английском',
            'value': with_en,
            'percentage': percentage
        }

    def get_agenda_decisions_ru_coverage(self):
        """Get Russian translation coverage for agenda decisions"""
        total_decisions = AgendaDecision.objects.count()
        
        with_ru = AgendaDecision.objects.filter(
            decision_text_ru__isnull=False
        ).exclude(decision_text_ru='').count()
        
        percentage = round((with_ru / total_decisions * 100), 1) if total_decisions > 0 else 0
        
        return {
            'name': 'Päevakorra otsused vene keeles',
            'name_en': 'Agenda Decisions in Russian',
            'name_ru': 'Решения повестки на русском',
            'value': with_ru,
            'percentage': percentage
        }

    def get_agenda_active_politicians_en_coverage(self):
        """Get English translation coverage for agenda active politicians"""
        total_active = AgendaActivePolitician.objects.count()
        
        with_en = AgendaActivePolitician.objects.filter(
            activity_description_en__isnull=False
        ).exclude(activity_description_en='').count()
        
        percentage = round((with_en / total_active * 100), 1) if total_active > 0 else 0
        
        return {
            'name': 'Aktiivsed poliitikud kirjeldused inglise keeles',
            'name_en': 'Active Politicians Descriptions in English',
            'name_ru': 'Описания активных политиков на английском',
            'value': with_en,
            'percentage': percentage
        }

    def get_agenda_active_politicians_ru_coverage(self):
        """Get Russian translation coverage for agenda active politicians"""
        total_active = AgendaActivePolitician.objects.count()
        
        with_ru = AgendaActivePolitician.objects.filter(
            activity_description_ru__isnull=False
        ).exclude(activity_description_ru='').count()
        
        percentage = round((with_ru / total_active * 100), 1) if total_active > 0 else 0
        
        return {
            'name': 'Aktiivsed poliitikud kirjeldused vene keeles',
            'name_en': 'Active Politicians Descriptions in Russian',
            'name_ru': 'Описания активных политиков на русском',
            'value': with_ru,
            'percentage': percentage
        }

    def get_politician_profiles_en_coverage(self):
        """Get English translation coverage for politician profiles"""
        total_profiles = PoliticianProfilePart.objects.count()
        
        with_en = PoliticianProfilePart.objects.filter(
            analysis_en__isnull=False
        ).exclude(analysis_en='').count()
        
        percentage = round((with_en / total_profiles * 100), 1) if total_profiles > 0 else 0
        
        return {
            'name': 'Struktureeritud poliitiku profiilid inglise keeles',
            'name_en': 'Structured Politician Profiles in English',
            'name_ru': 'Структурированные профили политиков на английском',
            'value': with_en,
            'percentage': percentage
        }

    def get_politician_profiles_ru_coverage(self):
        """Get Russian translation coverage for politician profiles"""
        total_profiles = PoliticianProfilePart.objects.count()
        
        with_ru = PoliticianProfilePart.objects.filter(
            analysis_ru__isnull=False
        ).exclude(analysis_ru='').count()
        
        percentage = round((with_ru / total_profiles * 100), 1) if total_profiles > 0 else 0
        
        return {
            'name': 'Struktureeritud poliitiku profiilid vene keeles',
            'name_en': 'Structured Politician Profiles in Russian',
            'name_ru': 'Структурированные профили политиков на русском',
            'value': with_ru,
            'percentage': percentage
        }

    def get_incomplete_speeches_count(self):
        """Get total number of incomplete speeches"""
        total_speeches = Speech.objects.filter(event_type='SPEECH').count()
        incomplete_count = Speech.objects.filter(event_type='SPEECH', is_incomplete=True).count()
        percentage = round((incomplete_count / total_speeches * 100), 1) if total_speeches > 0 else 0
        
        return {
            'name': 'Puudulikud kõned',
            'name_en': 'Incomplete Speeches',
            'name_ru': 'Неполные выступления',
            'value': incomplete_count,
            'percentage': percentage
        }

    def get_incomplete_agendas_count(self):
        """Get total number of incomplete agendas"""
        total_agendas = AgendaItem.objects.count()
        incomplete_count = AgendaItem.objects.filter(is_incomplete=True).count()
        percentage = round((incomplete_count / total_agendas * 100), 1) if total_agendas > 0 else 0
        
        return {
            'name': 'Puudulikud päevakorrad',
            'name_en': 'Incomplete Agendas',
            'name_ru': 'Неполные повестки',
            'value': incomplete_count,
            'percentage': percentage
        }

    def get_incomplete_plenary_sessions_count(self):
        """Get total number of incomplete plenary sessions"""
        total_sessions = PlenarySession.objects.count()
        incomplete_count = PlenarySession.objects.filter(is_incomplete=True).count()
        percentage = round((incomplete_count / total_sessions * 100), 1) if total_sessions > 0 else 0
        
        return {
            'name': 'Puudulikud istungid',
            'name_en': 'Incomplete Plenary Sessions',
            'name_ru': 'Неполные пленарные заседания',
            'value': incomplete_count,
            'percentage': percentage
        }

    def get_incomplete_politician_profiles_count(self):
        """Get total number of incomplete politician profiles"""
        total_profiles = PoliticianProfilePart.objects.count()
        incomplete_count = PoliticianProfilePart.objects.filter(is_incomplete=True).count()
        percentage = round((incomplete_count / total_profiles * 100), 1) if total_profiles > 0 else 0
        
        return {
            'name': 'Puudulikud poliitiku profiilid',
            'name_en': 'Incomplete Politician Profiles',
            'name_ru': 'Неполные профили политиков',
            'value': incomplete_count,
            'percentage': percentage
        }
