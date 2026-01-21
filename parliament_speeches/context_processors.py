"""
Context processors for making translation functions available in templates
"""
from .translation import translate
from .models import PlenarySession, AgendaItem, Speech, Politician, StatisticsEntry, TextPage
from django.db.models import Q, Count

class TranslationObject:
    """
    Object that allows dot notation access to translations
    """
    def __init__(self, language):
        self.language = language
    
    def __getattr__(self, key):
        """Allow dot notation access like t.SITE_NAME"""
        result = translate(key, self.language)
        return result

def translation_context(request):
    """
    Context processor to make translation functions available in all templates
    
    Args:
        request: Django request object
        
    Returns:
        Dictionary with translation context
    """
    # Get the current language from the request (set by middleware)
    current_language = getattr(request, 'LANGUAGE_CODE', 'et')
    
    # Create translation object that supports dot notation
    t_obj = TranslationObject(current_language)
    
    def t_func(key, **kwargs):
        """Template translation function"""
        return translate(key, current_language, **kwargs)
    
    # Get menu pages
    menu_pages = TextPage.objects.filter(is_published=True, show_in_menu=True).order_by('menu_order', 'title')
    
    return {
        't': t_obj,  # For dot notation: {{ t.SITE_NAME }}
        't_func': t_func,  # For function calls: {{ t_func 'SITE_NAME' }}
        'current_language': current_language,
        'translate': lambda key, **kwargs: translate(key, current_language, **kwargs),
        'supported_languages': [
            {'code': 'et', 'name': 'Eesti'},
            {'code': 'en', 'name': 'English'},
            {'code': 'ru', 'name': 'Русский'},
        ],
        'menu_pages': menu_pages,
    }

def model_counts(request):
    """
    Context processor to provide model counts for statistics from StatisticsEntry
    
    Args:
        request: Django request object
        
    Returns:
        Dictionary with model counts and statistics from StatisticsEntry
    """
    # Get current language
    current_language = getattr(request, 'LANGUAGE_CODE', 'et')
    
    # Create a dictionary to store statistics by key names for easy template access
    stats_dict = {}
    
    # Get all statistics entries
    statistics = StatisticsEntry.objects.all()
    
    # Map statistics to expected template variable names
    stat_mapping = {
        'Kõned kokku': 'speeches',
        'Päevakorrapunktid kokku': 'agenda_items',
        'Kõnede AI kokkuvõtted': ('speeches_with_ai', 'speeches_ai_percentage'),
        'Struktureeritud päevakorra kokkuvõtted': ('agendas_with_ai', 'agendas_ai_percentage'),
        'Struktureeritud poliitiku profiilid saadaval': ('politician_profiles_available', 'politician_profiles_available_percentage'),
        'Struktureeritud poliitiku profiilid kokku vaja': 'politician_profiles_required',
        'Istungjärgud': 'plenary_sessions',
        
        # Agenda structured data
        'Päevakorra otsused': ('agenda_decisions_count', 'agenda_decisions_percentage'),
        'Aktiivsed poliitikud päevakorras': ('agenda_active_politicians_count', 'agenda_active_politicians_percentage'),
        
        # English translations
        'Päevakorra AI kokkuvõtted inglise keeles': ('agendas_ai_en_count', 'agendas_ai_en_percentage'),
        'Päevakorra pealkirjad inglise keeles': ('agendas_title_en_count', 'agendas_title_en_percentage'),
        'Kõnede AI kokkuvõtted inglise keeles': ('speeches_ai_en_count', 'speeches_ai_en_percentage'),
        'Päevakorra otsused inglise keeles': ('agenda_decisions_en_count', 'agenda_decisions_en_percentage'),
        'Aktiivsed poliitikud kirjeldused inglise keeles': ('agenda_active_politicians_en_count', 'agenda_active_politicians_en_percentage'),
        'Struktureeritud poliitiku profiilid inglise keeles': ('politician_profiles_en_count', 'politician_profiles_en_percentage'),
        
        # Russian translations
        'Päevakorra AI kokkuvõtted vene keeles': ('agendas_ai_ru_count', 'agendas_ai_ru_percentage'),
        'Päevakorra pealkirjad vene keeles': ('agendas_title_ru_count', 'agendas_title_ru_percentage'),
        'Kõnede AI kokkuvõtted vene keeles': ('speeches_ai_ru_count', 'speeches_ai_ru_percentage'),
        'Päevakorra otsused vene keeles': ('agenda_decisions_ru_count', 'agenda_decisions_ru_percentage'),
        'Aktiivsed poliitikud kirjeldused vene keeles': ('agenda_active_politicians_ru_count', 'agenda_active_politicians_ru_percentage'),
        'Struktureeritud poliitiku profiilid vene keeles': ('politician_profiles_ru_count', 'politician_profiles_ru_percentage'),
    }
    
    # Process each statistic
    for stat in statistics:
        if stat.name in stat_mapping:
            mapping = stat_mapping[stat.name]
            if isinstance(mapping, tuple):
                # Has both value and percentage
                stats_dict[mapping[0]] = stat.value
                if stat.percentage is not None:
                    stats_dict[mapping[1]] = stat.percentage
            else:
                # Only value
                stats_dict[mapping] = stat.value
    
    # Fallback values for missing statistics (backwards compatibility)
    fallback_stats = {
        'plenary_sessions': PlenarySession.objects.count() if 'plenary_sessions' not in stats_dict else stats_dict['plenary_sessions'],
        'agenda_items': stats_dict.get('agenda_items', AgendaItem.objects.count()),
        'politicians': Politician.objects.count(),
        'speeches': stats_dict.get('speeches', Speech.objects.count()),
        'speeches_with_ai': stats_dict.get('speeches_with_ai', 0),
        'speeches_ai_percentage': stats_dict.get('speeches_ai_percentage', 0),
        'agendas_with_ai': stats_dict.get('agendas_with_ai', 0),
        'agendas_ai_percentage': stats_dict.get('agendas_ai_percentage', 0),
        
        # Translation completion statistics
        'speeches_ai_en_count': stats_dict.get('speeches_ai_en_count', 0),
        'speeches_ai_ru_count': stats_dict.get('speeches_ai_ru_count', 0),
        'speeches_ai_en_percentage': stats_dict.get('speeches_ai_en_percentage', 0),
        'speeches_ai_ru_percentage': stats_dict.get('speeches_ai_ru_percentage', 0),
        
        'agendas_ai_en_count': stats_dict.get('agendas_ai_en_count', 0),
        'agendas_ai_ru_count': stats_dict.get('agendas_ai_ru_count', 0),
        'agendas_ai_en_percentage': stats_dict.get('agendas_ai_en_percentage', 0),
        'agendas_ai_ru_percentage': stats_dict.get('agendas_ai_ru_percentage', 0),
        
        'agendas_title_en_count': stats_dict.get('agendas_title_en_count', 0),
        'agendas_title_ru_count': stats_dict.get('agendas_title_ru_count', 0),
        'agendas_title_en_percentage': stats_dict.get('agendas_title_en_percentage', 0),
        'agendas_title_ru_percentage': stats_dict.get('agendas_title_ru_percentage', 0),
        
        # Agenda structured data
        'agenda_decisions_count': stats_dict.get('agenda_decisions_count', 0),
        'agenda_decisions_percentage': stats_dict.get('agenda_decisions_percentage', 0),
        'agenda_active_politicians_count': stats_dict.get('agenda_active_politicians_count', 0),
        'agenda_active_politicians_percentage': stats_dict.get('agenda_active_politicians_percentage', 0),
        
        # Agenda decisions translations
        'agenda_decisions_en_count': stats_dict.get('agenda_decisions_en_count', 0),
        'agenda_decisions_en_percentage': stats_dict.get('agenda_decisions_en_percentage', 0),
        'agenda_decisions_ru_count': stats_dict.get('agenda_decisions_ru_count', 0),
        'agenda_decisions_ru_percentage': stats_dict.get('agenda_decisions_ru_percentage', 0),
        
        # Agenda active politicians translations
        'agenda_active_politicians_en_count': stats_dict.get('agenda_active_politicians_en_count', 0),
        'agenda_active_politicians_en_percentage': stats_dict.get('agenda_active_politicians_en_percentage', 0),
        'agenda_active_politicians_ru_count': stats_dict.get('agenda_active_politicians_ru_count', 0),
        'agenda_active_politicians_ru_percentage': stats_dict.get('agenda_active_politicians_ru_percentage', 0),
        
        # Keep these for backwards compatibility (not in StatisticsEntry yet)
        'plenary_sessions_title_en_count': 0,
        'plenary_sessions_title_ru_count': 0,
        'plenary_sessions_title_en_percentage': 0,
        'plenary_sessions_title_ru_percentage': 0,
    }
    
    # Merge stats_dict into fallback_stats
    fallback_stats.update(stats_dict)
    
    # Get first and last speech dates (not in StatisticsEntry as they are not counts)
    first_speech = Speech.objects.order_by('date').first()
    last_speech = Speech.objects.order_by('-date').first()
    fallback_stats['first_speech_date'] = first_speech.date if first_speech else None
    fallback_stats['last_speech_date'] = last_speech.date if last_speech else None
    
    # Add statistics entries for template iteration with localized names
    statistics_entries = []
    for stat in statistics:
        statistics_entries.append({
            'name': stat.get_localized_name(current_language),
            'value': stat.value,
            'percentage': stat.percentage,
            'original_name': stat.name
        })
    
    return {
        'model_counts': fallback_stats,
        'statistics_entries': statistics_entries
    }