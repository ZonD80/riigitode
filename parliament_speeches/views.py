from django.shortcuts import render, get_object_or_404, redirect
from django.core.paginator import Paginator
from django.db.models import Q, Count
from django.http import JsonResponse
from .models import (PlenarySession, AgendaItem, Speech, Politician, PoliticianProfilePart,
                     AgendaSummary, AgendaDecision, AgendaActivePolitician, TextPage, ParliamentParseError)
from datetime import datetime, date
from django.db.models.functions import TruncDate
from django.db.models import Sum, F, DurationField, Avg
from collections import defaultdict
import json


def calculate_politician_speaking_time_for_agenda(politician, agenda_item):
    """Calculate speaking time for a specific politician in a specific agenda item"""
    speeches = agenda_item.speeches.filter(
        politician=politician,
        event_type='SPEECH'
    ).order_by('date')
    
    if not speeches.exists():
        return 0
    
    if speeches.count() == 1:
        # Single speech, estimate 30 seconds
        return 30
    
    total_speaking_seconds = 0
    speeches_list = list(speeches)
    
    # Calculate intervals between consecutive speeches by this politician
    for i in range(len(speeches_list) - 1):
        current_speech = speeches_list[i]
        next_speech = speeches_list[i + 1]
        
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
    
    return int(total_speaking_seconds)


def format_speaking_time(seconds):
    """Format speaking time in seconds to human readable format"""
    if not seconds:
        return None
    
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    remaining_seconds = seconds % 60
    
    if hours > 0:
        return f"{hours}h {minutes}m"
    elif minutes > 0:
        return f"{minutes}m"
    else:
        return f"{remaining_seconds}s"


def home(request):
    """Home page with slogan and navigation"""
    # Get all active politicians who have at least one speech
    politicians_with_profiles = Politician.objects.filter(
        active=True
    ).annotate(
        speech_count=Count('speeches')
    ).filter(
        speech_count__gt=0
    ).select_related()
    
    # Sort by profiling percentage (calculated property) - show all politicians
    politicians_for_chart = sorted(
        politicians_with_profiles, 
        key=lambda p: p.profiling_percentage, 
        reverse=True
    )
    
    context = {
        'politicians_for_chart': politicians_for_chart,
    }
    return render(request, 'parliament_speeches/home.html', context)


def plenary_sessions_list(request):
    """List plenary sessions with their agendas"""
    search_query = request.GET.get('search', '')
    date_from = request.GET.get('date_from', '')
    date_to = request.GET.get('date_to', '')
    
    sessions = PlenarySession.objects.all()
    
    # Apply search filter
    if search_query:
        sessions = sessions.filter(
            Q(title__icontains=search_query) |
            Q(agenda_items__title__icontains=search_query)
        ).distinct()
    
    # Apply date filters
    if date_from:
        try:
            date_from_parsed = datetime.strptime(date_from, '%Y-%m-%d').date()
            sessions = sessions.filter(date__date__gte=date_from_parsed)
        except ValueError:
            pass
    
    if date_to:
        try:
            date_to_parsed = datetime.strptime(date_to, '%Y-%m-%d').date()
            sessions = sessions.filter(date__date__lte=date_to_parsed)
        except ValueError:
            pass
    
    sessions = sessions.prefetch_related('agenda_items').order_by('-date')
    
    # Add AI summary stats and structured data for each session
    sessions_with_stats = []
    for session in sessions:
        agenda_items = session.agenda_items.all()
        ai_summaries_count = sum(1 for item in agenda_items if hasattr(item, 'structured_summary'))
        session.ai_summaries_count = ai_summaries_count
        session.ai_summaries_percentage = (ai_summaries_count / len(agenda_items) * 100) if agenda_items else 0
        
        # Add structured data for each agenda item
        agenda_items_with_structured_data = []
        for agenda_item in agenda_items:
            # Add structured data
            try:
                agenda_item.structured_summary = AgendaSummary.objects.get(agenda_item=agenda_item)
            except AgendaSummary.DoesNotExist:
                agenda_item.structured_summary = None
                
            agenda_item.structured_decisions = AgendaDecision.objects.filter(agenda_item=agenda_item).select_related('politician')
            agenda_items_with_structured_data.append(agenda_item)
        
        # Replace the agenda_items relation with our enhanced data
        session.agenda_items_with_structured_data = agenda_items_with_structured_data
        sessions_with_stats.append(session)
    
    paginator = Paginator(sessions_with_stats, 10)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)
    
    context = {
        'page_obj': page_obj,
        'search_query': search_query,
        'date_from': date_from,
        'date_to': date_to,
    }
    
    return render(request, 'parliament_speeches/plenary_sessions_list.html', context)


def plenary_session_detail(request, session_id):
    """Detail view for a single plenary session showing only its agenda items"""
    session = get_object_or_404(PlenarySession, pk=session_id)
    
    search_query = request.GET.get('search', '')
    
    # Get agenda items for this session
    agenda_items = session.agenda_items.all()
    
    # Apply search filter to agenda items if provided
    if search_query:
        agenda_items = agenda_items.filter(
            Q(title__icontains=search_query)
        )
    
    agenda_items = agenda_items.order_by('date')
    
    # Add AI summary stats and structured data for agenda items
    agenda_items_with_stats = []
    for agenda_item in agenda_items:
        speeches = agenda_item.speeches.filter(event_type='SPEECH')
        speeches_with_ai = speeches.filter(ai_summary__isnull=False, ai_summary__gt='').count()
        total_speeches = speeches.count()
        speeches_ai_percentage = (speeches_with_ai / total_speeches * 100) if total_speeches > 0 else 0
        
        agenda_item.speeches_count = total_speeches
        agenda_item.speeches_with_ai = speeches_with_ai
        agenda_item.speeches_ai_percentage = round(speeches_ai_percentage, 1)
        
        # Add structured data
        try:
            agenda_item.structured_summary = AgendaSummary.objects.get(agenda_item=agenda_item)
        except AgendaSummary.DoesNotExist:
            agenda_item.structured_summary = None
            
        agenda_item.structured_decisions = AgendaDecision.objects.filter(agenda_item=agenda_item).select_related('politician')
        
        agenda_items_with_stats.append(agenda_item)
    
    # Calculate session-level AI summary stats
    total_agenda_items = len(agenda_items_with_stats)
    agenda_items_with_ai_summary = sum(1 for item in agenda_items_with_stats if hasattr(item, 'structured_summary'))
    session_ai_percentage = (agenda_items_with_ai_summary / total_agenda_items * 100) if total_agenda_items > 0 else 0
    
    # Calculate translation statistics for this session
    session_agenda_items = session.agenda_items.all()
    session_speeches = Speech.objects.filter(agenda_item__plenary_session=session)
    
    # Agenda translation stats
    agendas_with_title_en = session_agenda_items.filter(title_en__isnull=False).exclude(title_en='').count()
    agendas_with_title_ru = session_agenda_items.filter(title_ru__isnull=False).exclude(title_ru='').count()
    agendas_with_ai_en = session_agenda_items.filter(structured_summary__summary_text_en__isnull=False).exclude(structured_summary__summary_text_en='').count()
    agendas_with_ai_ru = session_agenda_items.filter(structured_summary__summary_text_ru__isnull=False).exclude(structured_summary__summary_text_ru='').count()
    
    # Speech translation stats
    speeches_with_ai = session_speeches.filter(ai_summary__isnull=False).exclude(ai_summary='').count()
    speeches_with_ai_en = session_speeches.filter(ai_summary_en__isnull=False).exclude(ai_summary_en='').count()
    speeches_with_ai_ru = session_speeches.filter(ai_summary_ru__isnull=False).exclude(ai_summary_ru='').count()
    
    # Calculate percentages
    total_session_agenda_items = session_agenda_items.count()
    total_session_speeches = session_speeches.count()
    
    agendas_title_en_percentage = (agendas_with_title_en / total_session_agenda_items * 100) if total_session_agenda_items > 0 else 0
    agendas_title_ru_percentage = (agendas_with_title_ru / total_session_agenda_items * 100) if total_session_agenda_items > 0 else 0
    agendas_ai_en_percentage = (agendas_with_ai_en / agenda_items_with_ai_summary * 100) if agenda_items_with_ai_summary > 0 else 0
    agendas_ai_ru_percentage = (agendas_with_ai_ru / agenda_items_with_ai_summary * 100) if agenda_items_with_ai_summary > 0 else 0
    
    speeches_ai_en_percentage = (speeches_with_ai_en / speeches_with_ai * 100) if speeches_with_ai > 0 else 0
    speeches_ai_ru_percentage = (speeches_with_ai_ru / speeches_with_ai * 100) if speeches_with_ai > 0 else 0
    
    paginator = Paginator(agenda_items_with_stats, 10)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)
    
    context = {
        'session': session,
        'page_obj': page_obj,
        'search_query': search_query,
        'total_agenda_items': total_agenda_items,
        'agenda_items_with_ai_summary': agenda_items_with_ai_summary,
        'session_ai_percentage': round(session_ai_percentage, 1),
        
        # Translation statistics for this session
        'session_translation_stats': {
            'total_agenda_items': total_session_agenda_items,
            'total_speeches': total_session_speeches,
            'speeches_with_ai': speeches_with_ai,
            
            'agendas_title_en_count': agendas_with_title_en,
            'agendas_title_ru_count': agendas_with_title_ru,
            'agendas_title_en_percentage': round(agendas_title_en_percentage, 1),
            'agendas_title_ru_percentage': round(agendas_title_ru_percentage, 1),
            
            'agendas_ai_en_count': agendas_with_ai_en,
            'agendas_ai_ru_count': agendas_with_ai_ru,
            'agendas_ai_en_percentage': round(agendas_ai_en_percentage, 1),
            'agendas_ai_ru_percentage': round(agendas_ai_ru_percentage, 1),
            
            'speeches_ai_en_count': speeches_with_ai_en,
            'speeches_ai_ru_count': speeches_with_ai_ru,
            'speeches_ai_en_percentage': round(speeches_ai_en_percentage, 1),
            'speeches_ai_ru_percentage': round(speeches_ai_ru_percentage, 1),
        }
    }
    
    return render(request, 'parliament_speeches/plenary_session_detail.html', context)


def politicians_agendas_list(request):
    """List politicians and their agendas - OPTIMIZED VERSION"""
    search_query = request.GET.get('search', '')
    date_from = request.GET.get('date_from', '')
    date_to = request.GET.get('date_to', '')
    
    # Build base query for politicians
    politicians = Politician.objects.filter(active=True)
    
    # Apply search filter
    if search_query:
        politicians = politicians.filter(
            Q(full_name__icontains=search_query) |
            Q(first_name__icontains=search_query) |
            Q(last_name__icontains=search_query)
        )
    
    # Filter politicians who have agendas (with optional date filters)
    agenda_filter = Q(speeches__event_type='SPEECH')
    if date_from:
        try:
            date_from_parsed = datetime.strptime(date_from, '%Y-%m-%d').date()
            agenda_filter &= Q(speeches__agenda_item__date__date__gte=date_from_parsed)
        except ValueError:
            pass
    
    if date_to:
        try:
            date_to_parsed = datetime.strptime(date_to, '%Y-%m-%d').date()
            agenda_filter &= Q(speeches__agenda_item__date__date__lte=date_to_parsed)
        except ValueError:
            pass
    
    # Annotate politicians with agenda count
    politicians = politicians.filter(
        speeches__agenda_item__isnull=False,
        speeches__event_type='SPEECH'
    ).annotate(
        agenda_count=Count('speeches__agenda_item', distinct=True, filter=agenda_filter)
    ).filter(agenda_count__gt=0).order_by('last_name', 'first_name')
    
    # Paginate at politician level BEFORE fetching agendas
    paginator = Paginator(politicians, 10)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)
    
    # Now fetch agendas ONLY for politicians on current page
    politicians_with_agendas = []
    for politician in page_obj:
        # Build agenda query with filters
        agendas_query = AgendaItem.objects.filter(
            speeches__politician=politician,
            speeches__event_type='SPEECH'
        )
        
        # Apply date filters
        if date_from:
            try:
                date_from_parsed = datetime.strptime(date_from, '%Y-%m-%d').date()
                agendas_query = agendas_query.filter(date__date__gte=date_from_parsed)
            except ValueError:
                pass
        
        if date_to:
            try:
                date_to_parsed = datetime.strptime(date_to, '%Y-%m-%d').date()
                agendas_query = agendas_query.filter(date__date__lte=date_to_parsed)
            except ValueError:
                pass
        
        # Get distinct agendas, prefetch related data, and limit to top 5
        agendas = agendas_query.distinct().select_related(
            'plenary_session'
        ).prefetch_related(
            'structured_summary',
            'speeches'
        ).order_by('-date')
        
        # Get total count before limiting
        total_agenda_count = agendas.count()
        
        # Limit to first 5 for display
        agendas_limited = list(agendas[:5])
        
        # Calculate AI summary stats
        ai_summaries_count = agendas.filter(structured_summary__isnull=False).count()
        ai_summaries_percentage = (ai_summaries_count / total_agenda_count * 100) if total_agenda_count > 0 else 0
        
        # Calculate speaking time and speech count for each agenda (only for the 5 displayed)
        for agenda in agendas_limited:
            # Use annotation to get speech count more efficiently
            politician_speech_count = Speech.objects.filter(
                politician=politician,
                agenda_item=agenda,
                event_type='SPEECH'
            ).count()
            
            # Calculate speaking time
            speaking_time = calculate_politician_speaking_time_for_agenda(politician, agenda)
            
            agenda.politician_speaking_time_seconds = speaking_time
            agenda.politician_speaking_time_formatted = format_speaking_time(speaking_time)
            agenda.politician_speech_count = politician_speech_count
        
        # Create a custom agendas object that has .count() method
        class AgendasList(list):
            def __init__(self, items, total_count):
                super().__init__(items)
                self._total_count = total_count
            
            def count(self):
                return self._total_count
        
        agendas_with_count = AgendasList(agendas_limited, total_agenda_count)
        
        politicians_with_agendas.append({
            'politician': politician,
            'agendas': agendas_with_count,
            'ai_summaries_count': ai_summaries_count,
            'ai_summaries_percentage': round(ai_summaries_percentage, 1)
        })
    
    # Replace page_obj items with our enriched data
    page_obj.object_list = politicians_with_agendas
    
    context = {
        'page_obj': page_obj,
        'search_query': search_query,
        'date_from': date_from,
        'date_to': date_to,
    }
    
    return render(request, 'parliament_speeches/politicians_agendas_list.html', context)


def agenda_detail(request, agenda_id):
    """Detail view for an agenda item with speeches (similar to admin complete-speech)"""
    agenda_item = get_object_or_404(AgendaItem, pk=agenda_id)
    speeches = agenda_item.speeches.filter(event_type='SPEECH').select_related(
        'politician'
    ).prefetch_related(
        'politician__faction_memberships__faction'
    ).order_by('date')
    
    # Calculate AI summary statistics for speeches
    total_speeches = speeches.count()
    speeches_with_ai = speeches.filter(ai_summary__isnull=False, ai_summary__gt='').count()
    speeches_ai_percentage = (speeches_with_ai / total_speeches * 100) if total_speeches > 0 else 0
    
    # Get unique politicians who spoke in this agenda
    participating_politicians = Politician.objects.filter(
        speeches__agenda_item=agenda_item,
        speeches__event_type='SPEECH'
    ).prefetch_related(
        'faction_memberships__faction'
    ).distinct().order_by('last_name', 'first_name')
    
    # Calculate speaking time for each politician for pie chart
    politician_speaking_data = []
    for politician in participating_politicians:
        speaking_time_seconds = calculate_politician_speaking_time_for_agenda(politician, agenda_item)
        speech_count = agenda_item.speeches.filter(politician=politician, event_type='SPEECH').count()
        
        politician_speaking_data.append({
            'politician': politician,
            'speaking_time_seconds': speaking_time_seconds,
            'speaking_time_formatted': format_speaking_time(speaking_time_seconds),
            'speech_count': speech_count
        })
    
    # Sort by speaking time (descending)
    politician_speaking_data.sort(key=lambda x: x['speaking_time_seconds'], reverse=True)
    
    # Get structured data from new models
    try:
        agenda_summary = AgendaSummary.objects.get(agenda_item=agenda_item)
    except AgendaSummary.DoesNotExist:
        agenda_summary = None
    
    agenda_decisions = AgendaDecision.objects.filter(agenda_item=agenda_item).select_related('politician')
    
    try:
        active_politician = AgendaActivePolitician.objects.get(agenda_item=agenda_item)
    except AgendaActivePolitician.DoesNotExist:
        active_politician = None
    
    context = {
        'agenda_item': agenda_item,
        'speeches': speeches,
        'total_speeches': total_speeches,
        'speeches_with_ai': speeches_with_ai,
        'speeches_ai_percentage': round(speeches_ai_percentage, 1),
        'participating_politicians': participating_politicians,
        'politician_speaking_data': politician_speaking_data,
        # New structured data
        'agenda_summary': agenda_summary,
        'agenda_decisions': agenda_decisions,
        'active_politician': active_politician,
    }
    
    return render(request, 'parliament_speeches/agenda_detail.html', context)


def decisions_list(request):
    """List all agenda decisions with filtering by date, grouped by day"""
    search_query = request.GET.get('search', '')
    date_from = request.GET.get('date_from', '')
    date_to = request.GET.get('date_to', '')
    
    # Get all decisions with related agenda items and plenary sessions
    decisions = AgendaDecision.objects.select_related(
        'agenda_item', 
        'agenda_item__plenary_session', 
        'politician'
    ).all()
    
    # Apply search filter
    if search_query:
        decisions = decisions.filter(
            Q(decision_text__icontains=search_query) |
            Q(decision_text_en__icontains=search_query) |
            Q(decision_text_ru__icontains=search_query) |
            Q(agenda_item__title__icontains=search_query) |
            Q(politician__full_name__icontains=search_query)
        )
    
    # Apply date filters based on agenda item's plenary session date
    if date_from:
        try:
            from_date = datetime.strptime(date_from, '%Y-%m-%d').date()
            decisions = decisions.filter(agenda_item__plenary_session__date__gte=from_date)
        except ValueError:
            pass
            
    if date_to:
        try:
            to_date = datetime.strptime(date_to, '%Y-%m-%d').date()
            decisions = decisions.filter(agenda_item__plenary_session__date__lte=to_date)
        except ValueError:
            pass
    
    # Order by most recent agenda sessions
    decisions = decisions.order_by('-agenda_item__plenary_session__date', '-created_at')
    
    # Group decisions by date, then by agenda within each date
    decisions_by_date = defaultdict(lambda: defaultdict(list))
    for decision in decisions:
        session_date = decision.agenda_item.plenary_session.date.date()
        agenda_id = decision.agenda_item.id
        decisions_by_date[session_date][agenda_id].append(decision)
    
    # Convert to nested structure for template iteration
    # Structure: [(date, agendas_for_date, total_decisions, num_agendas), ...]
    grouped_decisions = []
    for date in sorted(decisions_by_date.keys(), reverse=True):
        agendas_for_date = []
        for agenda_id in decisions_by_date[date]:
            # Get the agenda item (we can use the first decision's agenda_item)
            agenda_item = decisions_by_date[date][agenda_id][0].agenda_item
            decisions_for_agenda = decisions_by_date[date][agenda_id]
            agendas_for_date.append((agenda_item, decisions_for_agenda))
        
        # Sort agendas by title for consistent ordering
        agendas_for_date.sort(key=lambda x: x[0].title)
        
        # Calculate statistics for this date
        total_decisions_for_date = sum(len(decisions_list) for _, decisions_list in agendas_for_date)
        num_agendas = len(agendas_for_date)
        
        grouped_decisions.append((date, agendas_for_date, total_decisions_for_date, num_agendas))
    
    # Pagination on the grouped data
    paginator = Paginator(grouped_decisions, 10)  # 10 days per page
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)
    
    # Calculate total decisions count for display
    total_decisions = sum(
        len(decisions_list) 
        for _, agendas_for_date, _, _ in grouped_decisions 
        for _, decisions_list in agendas_for_date
    )
    
    context = {
        'page_obj': page_obj,
        'search_query': search_query,
        'date_from': date_from,
        'date_to': date_to,
        'total_decisions': total_decisions,
    }
    
    return render(request, 'parliament_speeches/decisions_list.html', context)


def politicians_list(request):
    """List politicians with their summaries and basic info"""
    search_query = request.GET.get('search', '')
    
    politicians = Politician.objects.filter(active=True)
    
    # Apply search filter
    if search_query:
        politicians = politicians.filter(
            Q(full_name__icontains=search_query) |
            Q(first_name__icontains=search_query) |
            Q(last_name__icontains=search_query)
        )
    
    # Add statistics for each politician
    politicians_with_stats = []
    for politician in politicians:
        # Count agenda items where this politician spoke
        agenda_items_count = AgendaItem.objects.filter(
            speeches__politician=politician
        ).distinct().count()
        
        # Count speeches
        speeches_count = Speech.objects.filter(
            politician=politician,
            event_type='SPEECH'
        ).count()
        
        # Count agendas with AI summaries
        agendas_with_ai = AgendaItem.objects.filter(
            speeches__politician=politician,
            structured_summary__isnull=False
        ).distinct().count()
        
        ai_summary_percentage = (agendas_with_ai / agenda_items_count * 100) if agenda_items_count > 0 else 0
        
        politicians_with_stats.append({
            'politician': politician,
            'agenda_items_count': agenda_items_count,
            'speeches_count': speeches_count,
            'agendas_with_ai': agendas_with_ai,
            'ai_summary_percentage': round(ai_summary_percentage, 1)
        })
    
    # Sort by agenda items count (most active first)
    politicians_with_stats.sort(key=lambda x: x['agenda_items_count'], reverse=True)
    
    paginator = Paginator(politicians_with_stats, 20)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)
    
    context = {
        'page_obj': page_obj,
        'search_query': search_query,
    }
    
    return render(request, 'parliament_speeches/politicians_list.html', context)


def politician_detail(request, politician_id):
    """Detail view for a specific politician showing their agendas"""
    politician = get_object_or_404(Politician, pk=politician_id)
    
    date_from = request.GET.get('date_from', '')
    date_to = request.GET.get('date_to', '')
    most_active = request.GET.get('most_active', '') == 'on'
    
    # Get agendas for this politician
    if most_active:
        # Filter by agendas where politician was most active (AgendaActivePolitician)
        active_politician_agendas = AgendaActivePolitician.objects.filter(
            politician=politician
        ).select_related('agenda_item', 'agenda_item__plenary_session')
        
        # Apply date filters to active politician agendas
        if date_from:
            try:
                date_from_parsed = datetime.strptime(date_from, '%Y-%m-%d').date()
                active_politician_agendas = active_politician_agendas.filter(
                    agenda_item__date__date__gte=date_from_parsed
                )
            except ValueError:
                pass
        
        if date_to:
            try:
                date_to_parsed = datetime.strptime(date_to, '%Y-%m-%d').date()
                active_politician_agendas = active_politician_agendas.filter(
                    agenda_item__date__date__lte=date_to_parsed
                )
            except ValueError:
                pass
        
        # Get the agenda items from active politician records
        agenda_ids = [ap.agenda_item.id for ap in active_politician_agendas]
        agendas = AgendaItem.objects.filter(id__in=agenda_ids)
        
        # Store activity descriptions for later use
        activity_descriptions = {ap.agenda_item.id: ap for ap in active_politician_agendas}
    else:
        # Get all agendas where this politician spoke
        agendas = AgendaItem.objects.filter(
            speeches__politician=politician
        ).distinct()
        
        # Apply date filters
        if date_from:
            try:
                date_from_parsed = datetime.strptime(date_from, '%Y-%m-%d').date()
                agendas = agendas.filter(date__date__gte=date_from_parsed)
            except ValueError:
                pass
        
        if date_to:
            try:
                date_to_parsed = datetime.strptime(date_to, '%Y-%m-%d').date()
                agendas = agendas.filter(date__date__lte=date_to_parsed)
            except ValueError:
                pass
        
        activity_descriptions = {}
    
    agendas = agendas.select_related('plenary_session').order_by('-date')
    
    # Calculate AI summary stats for this politician's agendas
    ai_summaries_count = sum(1 for agenda in agendas if hasattr(agenda, 'structured_summary'))
    ai_summaries_percentage = (ai_summaries_count / len(agendas) * 100) if agendas else 0
    
    # Calculate speaking time per agenda for this politician and add structured data
    agendas_with_speaking_time = []
    for agenda in agendas:
        speaking_time = calculate_politician_speaking_time_for_agenda(politician, agenda)
        politician_speech_count = agenda.speeches.filter(politician=politician, event_type='SPEECH').count()
        
        agenda.politician_speaking_time_seconds = speaking_time
        agenda.politician_speaking_time_formatted = format_speaking_time(speaking_time)
        agenda.politician_speech_count = politician_speech_count
        
        # Add structured data
        try:
            agenda.structured_summary = AgendaSummary.objects.get(agenda_item=agenda)
        except AgendaSummary.DoesNotExist:
            agenda.structured_summary = None
            
        agenda.structured_decisions = AgendaDecision.objects.filter(agenda_item=agenda).select_related('politician')
        
        # Add activity description if politician was most active
        if agenda.id in activity_descriptions:
            agenda.activity_description_obj = activity_descriptions[agenda.id]
        else:
            agenda.activity_description_obj = None
        
        agendas_with_speaking_time.append(agenda)
    
    paginator = Paginator(agendas_with_speaking_time, 10)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)
    
    context = {
        'politician': politician,
        'page_obj': page_obj,
        'ai_summaries_count': ai_summaries_count,
        'ai_summaries_percentage': round(ai_summaries_percentage, 1),
        'date_from': date_from,
        'date_to': date_to,
        'most_active': most_active,
    }
    
    return render(request, 'parliament_speeches/politician_detail.html', context)


def politician_activity_graph(request, politician_id):
    """Activity graph view for a specific politician showing daily speaking time"""
    politician = get_object_or_404(Politician, pk=politician_id)
    
    # Get all agendas where this politician spoke
    agendas = AgendaItem.objects.filter(
        speeches__politician=politician,
        speeches__event_type='SPEECH'
    ).distinct().order_by('date')
    
    # Create activity data grouped by date with agenda details
    activity_data = defaultdict(lambda: {'minutes': 0, 'agendas': []})  # date -> {minutes, agendas}
    
    for agenda in agendas:
        speaking_time_seconds = calculate_politician_speaking_time_for_agenda(politician, agenda)
        speaking_time_minutes = speaking_time_seconds / 60  # Convert to minutes
        
        # Group by date (not datetime)
        agenda_date = agenda.date.date()
        activity_data[agenda_date]['minutes'] += speaking_time_minutes
        activity_data[agenda_date]['agendas'].append({
            'id': agenda.pk,
            'title': agenda.title,
            'speaking_time_minutes': round(speaking_time_minutes, 1),
            'speech_count': agenda.speeches.filter(politician=politician, event_type='SPEECH').count()
        })
    
    # Convert to list of dictionaries for chart.js
    chart_data = []
    for agenda_date, data in sorted(activity_data.items()):
        chart_data.append({
            'date': agenda_date.isoformat(),  # Format as YYYY-MM-DD
            'minutes': round(data['minutes'], 1),
            'agendas': data['agendas']
        })
    
    # Identify peaks and valleys (direction changes)
    def identify_peaks_valleys(data_points):
        if len(data_points) < 3:
            # If less than 3 points, all are significant
            return [True] * len(data_points)
        
        is_significant = [False] * len(data_points)
        
        # First and last points are always significant
        is_significant[0] = True
        is_significant[-1] = True
        
        # Check for direction changes
        for i in range(1, len(data_points) - 1):
            prev_val = data_points[i-1]['minutes']
            curr_val = data_points[i]['minutes']
            next_val = data_points[i+1]['minutes']
            
            # Peak: current value is higher than both neighbors
            is_peak = curr_val > prev_val and curr_val > next_val
            # Valley: current value is lower than both neighbors
            is_valley = curr_val < prev_val and curr_val < next_val
            
            if is_peak or is_valley:
                is_significant[i] = True
        
        return is_significant
    
    # Mark significant points
    significant_points = identify_peaks_valleys(chart_data)
    for i, point in enumerate(chart_data):
        point['is_significant'] = significant_points[i]
    
    # Calculate statistics
    total_days_active = len(chart_data)
    total_minutes = sum(item['minutes'] for item in chart_data)
    avg_minutes_per_active_day = total_minutes / total_days_active if total_days_active > 0 else 0
    max_minutes_day = max((item['minutes'] for item in chart_data), default=0)
    
    context = {
        'politician': politician,
        'chart_data': chart_data,
        'chart_data_json': json.dumps(chart_data),
        'total_days_active': total_days_active,
        'total_minutes': round(total_minutes, 1),
        'avg_minutes_per_active_day': round(avg_minutes_per_active_day, 1),
        'max_minutes_day': round(max_minutes_day, 1),
    }
    
    return render(request, 'parliament_speeches/politician_activity_graph.html', context)


def politician_daily_agendas(request, politician_id, date_str):
    """View showing all agendas for a specific politician on a specific date"""
    politician = get_object_or_404(Politician, pk=politician_id)
    
    # Parse the date string (YYYY-MM-DD format)
    try:
        target_date = datetime.strptime(date_str, '%Y-%m-%d').date()
    except ValueError:
        # If date format is invalid, redirect to activity graph
        return redirect('politician_activity_graph', politician_id=politician_id)
    
    # Get all agendas for this politician on this date
    agendas = AgendaItem.objects.filter(
        speeches__politician=politician,
        speeches__event_type='SPEECH',
        date__date=target_date
    ).distinct().select_related('plenary_session').order_by('date')
    
    # Calculate speaking details for each agenda
    agendas_with_details = []
    total_speaking_time_seconds = 0
    total_speeches = 0
    
    for agenda in agendas:
        speaking_time_seconds = calculate_politician_speaking_time_for_agenda(politician, agenda)
        speech_count = agenda.speeches.filter(politician=politician, event_type='SPEECH').count()
        
        agendas_with_details.append({
            'agenda': agenda,
            'speaking_time_seconds': speaking_time_seconds,
            'speaking_time_formatted': format_speaking_time(speaking_time_seconds),
            'speech_count': speech_count
        })
        
        total_speaking_time_seconds += speaking_time_seconds
        total_speeches += speech_count
    
    context = {
        'politician': politician,
        'target_date': target_date,
        'agendas_with_details': agendas_with_details,
        'total_speaking_time_formatted': format_speaking_time(total_speaking_time_seconds),
        'total_speeches': total_speeches,
    }
    
    return render(request, 'parliament_speeches/politician_daily_agendas.html', context)


def politician_profiling(request, politician_id):
    """Profiling view for a specific politician showing AI-generated analysis using PoliticianProfilePart"""
    politician = get_object_or_404(Politician, pk=politician_id)
    
    # Get all available categories
    available_categories = PoliticianProfilePart.PROFILE_CATEGORIES
    
    # Get overall profiles (period_type='ALL') for each category
    overall_profiles = PoliticianProfilePart.objects.filter(
        politician=politician, 
        period_type='ALL'
    ).order_by('category')
    
    overall_profiles_by_category = {profile.category: profile for profile in overall_profiles}
    
    # Calculate profile statistics based on speeches data (similar to profile_politician.py)
    speeches = Speech.objects.filter(
        politician=politician,
        event_type='SPEECH'
    ).select_related('agenda_item__plenary_session')
    
    if speeches.exists():
        # Collect periods from speeches data
        agenda_ids = set(speech.agenda_item.id for speech in speeches)
        plenary_ids = set(speech.agenda_item.plenary_session.id for speech in speeches)
        months = set(f"{speech.date.month:02d}.{speech.date.year}" for speech in speeches)
        years = set(speech.date.year for speech in speeches)
        
        # Count existing and missing profiles for each category
        profile_stats = {}
        for category_code, category_name in available_categories:
            stats = {
                'category_code': category_code,
                'category_name': category_name,
                'has_overall': category_code in overall_profiles_by_category,
                'agenda_count': len(agenda_ids),
                'session_count': len(plenary_ids), 
                'month_count': len(months),
                'year_count': len(years),
                'agenda_existing': 0,
                'session_existing': 0,
                'month_existing': 0,
                'year_existing': 0,
            }
            
            # Count existing profiles for this category
            existing_profiles = PoliticianProfilePart.objects.filter(
                politician=politician,
                category=category_code
            )
            
            stats['agenda_existing'] = existing_profiles.filter(period_type='AGENDA').count()
            stats['session_existing'] = existing_profiles.filter(period_type='PLENARY_SESSION').count()
            stats['month_existing'] = existing_profiles.filter(period_type='MONTH').count()
            stats['year_existing'] = existing_profiles.filter(period_type='YEAR').count()
            
            profile_stats[category_code] = stats
    else:
        profile_stats = {}
        for category_code, category_name in available_categories:
            profile_stats[category_code] = {
                'category_code': category_code,
                'category_name': category_name,
                'has_overall': category_code in overall_profiles_by_category,
                'agenda_count': 0,
                'session_count': 0,
                'month_count': 0,
                'year_count': 0,
                'agenda_existing': 0,
                'session_existing': 0,
                'month_existing': 0,
                'year_existing': 0,
            }
    
    # Calculate overall metrics from overall profiles
    total_speeches_analyzed = 0
    if overall_profiles.exists():
        total_speeches_analyzed = sum(profile.speeches_analyzed for profile in overall_profiles)
        
        # Get date range from overall profiles
        date_ranges = [(p.date_range_start, p.date_range_end) for p in overall_profiles if p.date_range_start and p.date_range_end]
        if date_ranges:
            overall_start = min(dr[0] for dr in date_ranges)
            overall_end = max(dr[1] for dr in date_ranges)
            analysis_period = f"{overall_start} - {overall_end}"
        else:
            analysis_period = None
    else:
        analysis_period = None
    
    # Calculate basic stats
    total_speeches = speeches.count()
    total_agendas = len(set(speech.agenda_item.id for speech in speeches)) if speeches.exists() else 0
    
    overall_metrics = {
        'total_speeches': total_speeches,
        'total_agendas': total_agendas,
        'total_speeches_analyzed': total_speeches_analyzed,
        'analysis_period': analysis_period
    }
    
    # Check if we have any profile data at all
    has_any_profiles = overall_profiles.exists()
    
    context = {
        'politician': politician,
        'has_profile_data': has_any_profiles,
        'overall_profiles_by_category': overall_profiles_by_category,
        'profile_stats': profile_stats,
        'overall_metrics': overall_metrics,
        'available_categories': available_categories,
    }
    
    return render(request, 'parliament_speeches/politician_profiling.html', context)


def politician_profiling_agendas(request, politician_id, category='ALL'):
    """List all agendas for a politician with their profiling status"""
    politician = get_object_or_404(Politician, pk=politician_id)
    
    # Get all available categories
    available_categories = PoliticianProfilePart.PROFILE_CATEGORIES
    
    # Get filter parameter
    filter_session = request.GET.get('session')
    
    # Get all agendas where this politician spoke
    agendas = AgendaItem.objects.filter(
        speeches__politician=politician
    ).distinct().select_related('plenary_session')
    
    # Filter by session if specified
    if filter_session:
        agendas = agendas.filter(plenary_session_id=int(filter_session))
    
    agendas = agendas.order_by('-date')
    
    # Get existing agenda profiles for this politician
    existing_agenda_profiles = PoliticianProfilePart.objects.filter(
        politician=politician,
        period_type='AGENDA'
    ).select_related('agenda_item')
    
    # If a specific category is selected, filter profiles for profile display
    if category != 'ALL':
        category_profiles = existing_agenda_profiles.filter(category=category)
        # Create mapping of agenda_id -> profile object for displaying analysis
        agenda_profiles_map = {}
        for profile in category_profiles:
            if profile.agenda_item:
                agenda_profiles_map[profile.agenda_item.id] = profile
    else:
        # Create mapping of agenda_id -> set of categories (for ALL view)
        agenda_profiles_map = defaultdict(set)
        for profile in existing_agenda_profiles:
            if profile.agenda_item:
                agenda_profiles_map[profile.agenda_item.id].add(profile.category)
    
    # Prepare agenda data with profile status
    agenda_data = []
    for agenda in agendas:
        if category == 'ALL':
            existing_categories = agenda_profiles_map.get(agenda.id, set())
            agenda_info = {
                'agenda': agenda,
                'profile_count': len(existing_categories),
                'total_categories': len(available_categories),
                'existing_categories': existing_categories,
                'missing_categories': set(cat[0] for cat in available_categories) - existing_categories,
                'is_fully_profiled': len(existing_categories) == len(available_categories),
                'profile': None
            }
        else:
            # For specific category, include the profile object
            profile = agenda_profiles_map.get(agenda.id)
            agenda_info = {
                'agenda': agenda,
                'has_profile': profile is not None,
                'profile': profile
            }
        
        agenda_data.append(agenda_info)
    
    context = {
        'politician': politician,
        'agenda_data': agenda_data,
        'available_categories': available_categories,
        'selected_category': category,
        'filter_session': filter_session,
        'total_agendas': len(agenda_data),
        'fully_profiled_count': sum(1 for a in agenda_data if (a.get('is_fully_profiled', False) if category == 'ALL' else a.get('has_profile', False)))
    }
    
    return render(request, 'parliament_speeches/politician_profiling_agendas.html', context)


def politician_profiling_sessions(request, politician_id, category='ALL'):
    """List all plenary sessions for a politician with their profiling status"""
    politician = get_object_or_404(Politician, pk=politician_id)
    
    # Get all available categories
    available_categories = PoliticianProfilePart.PROFILE_CATEGORIES
    
    # Get filter parameter
    filter_month = request.GET.get('month')  # Format: MM.YYYY
    
    # Get all plenary sessions where this politician spoke
    sessions = PlenarySession.objects.filter(
        agenda_items__speeches__politician=politician
    ).distinct()
    
    # Filter by month if specified
    if filter_month:
        month_num, year = filter_month.split('.')
        sessions = sessions.filter(date__year=int(year), date__month=int(month_num))
    
    sessions = sessions.order_by('-date')
    
    # Get existing session profiles for this politician
    existing_session_profiles = PoliticianProfilePart.objects.filter(
        politician=politician,
        period_type='PLENARY_SESSION'
    ).select_related('plenary_session')
    
    # If a specific category is selected, filter profiles for profile display
    if category != 'ALL':
        category_profiles = existing_session_profiles.filter(category=category)
        # Create mapping of session_id -> profile object for displaying analysis
        session_profiles_map = {}
        for profile in category_profiles:
            if profile.plenary_session:
                session_profiles_map[profile.plenary_session.id] = profile
    else:
        # Create mapping of session_id -> set of categories (for ALL view)
        session_profiles_map = defaultdict(set)
        for profile in existing_session_profiles:
            if profile.plenary_session:
                session_profiles_map[profile.plenary_session.id].add(profile.category)
    
    # Prepare session data with profile status
    session_data = []
    for session in sessions:
        if category == 'ALL':
            existing_categories = session_profiles_map.get(session.id, set())
            session_info = {
                'session': session,
                'session_id': session.id,
                'profile_count': len(existing_categories),
                'total_categories': len(available_categories),
                'existing_categories': existing_categories,
                'missing_categories': set(cat[0] for cat in available_categories) - existing_categories,
                'is_fully_profiled': len(existing_categories) == len(available_categories),
                'profile': None
            }
        else:
            # For specific category, include the profile object
            profile = session_profiles_map.get(session.id)
            session_info = {
                'session': session,
                'session_id': session.id,
                'has_profile': profile is not None,
                'profile': profile
            }
        
        session_data.append(session_info)
    
    context = {
        'politician': politician,
        'session_data': session_data,
        'available_categories': available_categories,
        'selected_category': category,
        'filter_month': filter_month,
        'total_sessions': len(session_data),
        'fully_profiled_count': sum(1 for s in session_data if (s.get('is_fully_profiled', False) if category == 'ALL' else s.get('has_profile', False)))
    }
    
    return render(request, 'parliament_speeches/politician_profiling_sessions.html', context)


def politician_profiling_months(request, politician_id, category='ALL'):
    """List all months for a politician with their profiling status"""
    politician = get_object_or_404(Politician, pk=politician_id)
    
    # Get all available categories
    available_categories = PoliticianProfilePart.PROFILE_CATEGORIES
    
    # Get filter parameter
    filter_year = request.GET.get('year')
    
    # Get all months where this politician spoke
    speeches = Speech.objects.filter(
        politician=politician,
        event_type='SPEECH'
    ).select_related('agenda_item__plenary_session')
    
    # Filter by year if specified
    if filter_year:
        speeches = speeches.filter(date__year=int(filter_year))
    
    # Create set of (year, month) tuples for proper sorting
    months_set = set((speech.date.year, speech.date.month) for speech in speeches)
    # Sort by year descending, then month descending
    months_sorted = sorted(months_set, key=lambda x: (x[0], x[1]), reverse=True)
    # Convert back to MM.YYYY format for consistency with database
    months_list = [f"{month:02d}.{year}" for year, month in months_sorted]
    
    # Get existing month profiles for this politician
    existing_month_profiles = PoliticianProfilePart.objects.filter(
        politician=politician,
        period_type='MONTH'
    )
    
    # If a specific category is selected, filter profiles for profile display
    if category != 'ALL':
        category_profiles = existing_month_profiles.filter(category=category)
        # Create mapping of month -> profile object for displaying analysis
        month_profiles_map = {}
        for profile in category_profiles:
            if profile.month:
                month_profiles_map[profile.month] = profile
    else:
        # Create mapping of month -> set of categories (for ALL view)
        month_profiles_map = defaultdict(set)
        for profile in existing_month_profiles:
            if profile.month:
                month_profiles_map[profile.month].add(profile.category)
    
    # Prepare month data with profile status
    month_data = []
    for month in months_list:
        # Count speeches in this month
        month_num, year = month.split('.')
        speeches_count = speeches.filter(
            date__month=int(month_num), 
            date__year=int(year)
        ).count()
        
        # Calculate date range for this month
        month_num = int(month_num)
        year = int(year)
        
        # Calculate last day of month
        import calendar
        last_day = calendar.monthrange(year, month_num)[1]
        
        if category == 'ALL':
            existing_categories = month_profiles_map.get(month, set())
            month_info = {
                'month': month,
                'speeches_count': speeches_count,
                'profile_count': len(existing_categories),
                'total_categories': len(available_categories),
                'existing_categories': existing_categories,
                'missing_categories': set(cat[0] for cat in available_categories) - existing_categories,
                'is_fully_profiled': len(existing_categories) == len(available_categories),
                'date_from': f"{year}-{month_num:02d}-01",
                'date_to': f"{year}-{month_num:02d}-{last_day:02d}",
                'profile': None
            }
        else:
            # For specific category, include the profile object
            profile = month_profiles_map.get(month)
            month_info = {
                'month': month,
                'speeches_count': speeches_count,
                'has_profile': profile is not None,
                'profile': profile,
                'date_from': f"{year}-{month_num:02d}-01",
                'date_to': f"{year}-{month_num:02d}-{last_day:02d}"
            }
        
        month_data.append(month_info)
    
    context = {
        'politician': politician,
        'month_data': month_data,
        'available_categories': available_categories,
        'selected_category': category,
        'filter_year': filter_year,
        'total_months': len(month_data),
        'fully_profiled_count': sum(1 for m in month_data if (m.get('is_fully_profiled', False) if category == 'ALL' else m.get('has_profile', False)))
    }
    
    return render(request, 'parliament_speeches/politician_profiling_months.html', context)


def politician_profiling_years(request, politician_id, category='ALL'):
    """List all years for a politician with their profiling status"""
    politician = get_object_or_404(Politician, pk=politician_id)
    
    # Get all available categories
    available_categories = PoliticianProfilePart.PROFILE_CATEGORIES
    
    # Get all years where this politician spoke
    speeches = Speech.objects.filter(
        politician=politician,
        event_type='SPEECH'
    ).select_related('agenda_item__plenary_session')
    
    years_set = set(speech.date.year for speech in speeches)
    years_list = sorted(years_set, reverse=True)
    
    # Get existing year profiles for this politician
    existing_year_profiles = PoliticianProfilePart.objects.filter(
        politician=politician,
        period_type='YEAR'
    )
    
    # If a specific category is selected, filter profiles for profile display
    if category != 'ALL':
        category_profiles = existing_year_profiles.filter(category=category)
        # Create mapping of year -> profile object for displaying analysis
        year_profiles_map = {}
        for profile in category_profiles:
            if profile.year:
                year_profiles_map[profile.year] = profile
    else:
        # Create mapping of year -> set of categories (for ALL view)
        year_profiles_map = defaultdict(set)
        for profile in existing_year_profiles:
            if profile.year:
                year_profiles_map[profile.year].add(profile.category)
    
    # Prepare year data with profile status
    year_data = []
    for year in years_list:
        # Count speeches in this year
        speeches_count = speeches.filter(date__year=year).count()
        
        if category == 'ALL':
            existing_categories = year_profiles_map.get(year, set())
            year_info = {
                'year': year,
                'speeches_count': speeches_count,
                'profile_count': len(existing_categories),
                'total_categories': len(available_categories),
                'existing_categories': existing_categories,
                'missing_categories': set(cat[0] for cat in available_categories) - existing_categories,
                'is_fully_profiled': len(existing_categories) == len(available_categories),
                'date_from': f"{year}-01-01",
                'date_to': f"{year}-12-31",
                'profile': None
            }
        else:
            # For specific category, include the profile object
            profile = year_profiles_map.get(year)
            year_info = {
                'year': year,
                'speeches_count': speeches_count,
                'has_profile': profile is not None,
                'profile': profile,
                'date_from': f"{year}-01-01",
                'date_to': f"{year}-12-31"
            }
        
        year_data.append(year_info)
    
    context = {
        'politician': politician,
        'year_data': year_data,
        'available_categories': available_categories,
        'selected_category': category,
        'total_years': len(year_data),
        'fully_profiled_count': sum(1 for y in year_data if (y.get('is_fully_profiled', False) if category == 'ALL' else y.get('has_profile', False)))
    }
    
    return render(request, 'parliament_speeches/politician_profiling_years.html', context)


def politician_profiling_agenda_detail(request, politician_id, category, agenda_id):
    """Detail view for a specific agenda's profiling"""
    politician = get_object_or_404(Politician, pk=politician_id)
    agenda = get_object_or_404(AgendaItem, pk=agenda_id)
    
    # Get all available categories
    available_categories = PoliticianProfilePart.PROFILE_CATEGORIES
    
    # Get profile parts for this politician and agenda
    profile_filter = {
        'politician': politician,
        'period_type': 'AGENDA',
        'agenda_item': agenda
    }
    
    if category != 'ALL':
        profile_filter['category'] = category
    
    profiles = PoliticianProfilePart.objects.filter(**profile_filter).order_by('category')
    
    profiles_by_category = {profile.category: profile for profile in profiles}
    
    # Calculate which categories are missing
    existing_categories = set(profiles_by_category.keys())
    missing_categories = set(cat[0] for cat in available_categories) - existing_categories
    
    context = {
        'politician': politician,
        'agenda': agenda,
        'selected_category': category,
        'profiles_by_category': profiles_by_category,
        'available_categories': available_categories,
        'missing_categories': missing_categories,
        'has_profiles': bool(profiles_by_category)
    }
    
    return render(request, 'parliament_speeches/politician_profiling_agenda_detail.html', context)


def politician_profiling_session_detail(request, politician_id, category, session_id):
    """Detail view for a specific plenary session's profiling"""
    politician = get_object_or_404(Politician, pk=politician_id)
    session = get_object_or_404(PlenarySession, pk=session_id)
    
    # Get all available categories
    available_categories = PoliticianProfilePart.PROFILE_CATEGORIES
    
    # Get profile parts for this politician and session
    profile_filter = {
        'politician': politician,
        'period_type': 'PLENARY_SESSION',
        'plenary_session': session
    }
    
    if category != 'ALL':
        profile_filter['category'] = category
    
    profiles = PoliticianProfilePart.objects.filter(**profile_filter).order_by('category')
    
    profiles_by_category = {profile.category: profile for profile in profiles}
    
    # Calculate which categories are missing
    existing_categories = set(profiles_by_category.keys())
    missing_categories = set(cat[0] for cat in available_categories) - existing_categories
    
    context = {
        'politician': politician,
        'session': session,
        'selected_category': category,
        'profiles_by_category': profiles_by_category,
        'available_categories': available_categories,
        'missing_categories': missing_categories,
        'has_profiles': bool(profiles_by_category)
    }
    
    return render(request, 'parliament_speeches/politician_profiling_session_detail.html', context)


def politician_profiling_month_detail(request, politician_id, category, month):
    """Detail view for a specific month's profiling"""
    politician = get_object_or_404(Politician, pk=politician_id)
    
    # Get all available categories
    available_categories = PoliticianProfilePart.PROFILE_CATEGORIES
    
    # Get profile parts for this politician and month
    profile_filter = {
        'politician': politician,
        'period_type': 'MONTH',
        'month': month
    }
    
    if category != 'ALL':
        profile_filter['category'] = category
    
    profiles = PoliticianProfilePart.objects.filter(**profile_filter).order_by('category')
    
    profiles_by_category = {profile.category: profile for profile in profiles}
    
    # Calculate which categories are missing
    existing_categories = set(profiles_by_category.keys())
    missing_categories = set(cat[0] for cat in available_categories) - existing_categories
    
    context = {
        'politician': politician,
        'month': month,
        'selected_category': category,
        'profiles_by_category': profiles_by_category,
        'available_categories': available_categories,
        'missing_categories': missing_categories,
        'has_profiles': bool(profiles_by_category)
    }
    
    return render(request, 'parliament_speeches/politician_profiling_month_detail.html', context)


def politician_profiling_year_detail(request, politician_id, category, year):
    """Detail view for a specific year's profiling"""
    politician = get_object_or_404(Politician, pk=politician_id)
    
    # Get all available categories
    available_categories = PoliticianProfilePart.PROFILE_CATEGORIES
    
    # Get profile parts for this politician and year
    profile_filter = {
        'politician': politician,
        'period_type': 'YEAR',
        'year': year
    }
    
    if category != 'ALL':
        profile_filter['category'] = category
    
    profiles = PoliticianProfilePart.objects.filter(**profile_filter).order_by('category')
    
    profiles_by_category = {profile.category: profile for profile in profiles}
    
    # Calculate which categories are missing
    existing_categories = set(profiles_by_category.keys())
    missing_categories = set(cat[0] for cat in available_categories) - existing_categories
    
    context = {
        'politician': politician,
        'year': year,
        'selected_category': category,
        'profiles_by_category': profiles_by_category,
        'available_categories': available_categories,
        'missing_categories': missing_categories,
        'has_profiles': bool(profiles_by_category)
    }
    
    return render(request, 'parliament_speeches/politician_profiling_year_detail.html', context)


def agenda_politicians_summary(request):
    """Summary page showing politicians most active in agendas with pie chart"""
    date_from = request.GET.get('date_from', '')
    date_to = request.GET.get('date_to', '')
    politician_id = request.GET.get('politician', '')
    
    # Build base query for active politicians
    active_politicians_query = AgendaActivePolitician.objects.select_related(
        'politician', 'agenda_item', 'agenda_item__plenary_session'
    )
    
    # Apply date filters
    if date_from:
        try:
            date_from_parsed = datetime.strptime(date_from, '%Y-%m-%d').date()
            active_politicians_query = active_politicians_query.filter(
                agenda_item__date__date__gte=date_from_parsed
            )
        except ValueError:
            pass
    
    if date_to:
        try:
            date_to_parsed = datetime.strptime(date_to, '%Y-%m-%d').date()
            active_politicians_query = active_politicians_query.filter(
                agenda_item__date__date__lte=date_to_parsed
            )
        except ValueError:
            pass
    
    # Get aggregated data for pie chart
    politician_activity_count = {}
    for active_pol in active_politicians_query:
        # Skip if politician is None
        if active_pol.politician is None:
            continue
        
        pol_id = active_pol.politician.id
        if pol_id not in politician_activity_count:
            politician_activity_count[pol_id] = {
                'politician': active_pol.politician,
                'count': 0,
                'agendas': []
            }
        politician_activity_count[pol_id]['count'] += 1
        politician_activity_count[pol_id]['agendas'].append(active_pol)
    
    # Sort by count (descending)
    politicians_data = sorted(
        politician_activity_count.values(),
        key=lambda x: x['count'],
        reverse=True
    )
    
    # If politician filter is applied, show their agendas
    selected_politician = None
    politician_agendas = []
    if politician_id:
        try:
            selected_politician = Politician.objects.get(pk=politician_id)
            if int(politician_id) in politician_activity_count:
                politician_agendas = politician_activity_count[int(politician_id)]['agendas']
                # Sort by date (most recent first)
                politician_agendas = sorted(
                    politician_agendas,
                    key=lambda x: x.agenda_item.date,
                    reverse=True
                )
        except (Politician.DoesNotExist, ValueError):
            pass
    
    # Paginate politician agendas if a politician is selected
    page_obj = None
    if politician_agendas:
        paginator = Paginator(politician_agendas, 10)
        page_number = request.GET.get('page')
        page_obj = paginator.get_page(page_number)
    
    # Prepare data for JavaScript (for pie chart)
    chart_data = [
        {
            'politician_id': data['politician'].id,
            'name': data['politician'].full_name,
            'count': data['count'],
            'photo': data['politician'].photo.url if data['politician'].photo else None
        }
        for data in politicians_data
    ]
    
    context = {
        'politicians_data': politicians_data,
        'chart_data_json': json.dumps(chart_data),
        'date_from': date_from,
        'date_to': date_to,
        'selected_politician': selected_politician,
        'page_obj': page_obj,
        'total_agendas': len(politician_agendas) if politician_agendas else 0,
    }
    
    return render(request, 'parliament_speeches/agenda_politicians_summary.html', context)


def text_page(request, slug):
    """Display a text page by slug"""
    page = get_object_or_404(TextPage, slug=slug, is_published=True)
    
    context = {
        'page': page,
    }
    
    return render(request, 'parliament_speeches/text_page.html', context)


def api_transparency_report(request, year=None):
    """Display Parliament API transparency and integrity report"""
    
    # If no year specified, show list of available years
    if year is None:
        # Get all available years from parse errors
        available_years = ParliamentParseError.objects.exclude(
            year__isnull=True
        ).values_list('year', flat=True).distinct().order_by('-year')
        
        # Get statistics for each year
        year_stats = []
        for yr in available_years:
            year_errors = ParliamentParseError.objects.filter(year=yr).exclude(entity_type='parse_run')
            total_errors = year_errors.count()
            
            # Get date range for this year
            parse_run_error = ParliamentParseError.objects.filter(
                entity_type='parse_run',
                year=yr
            ).first()
            
            date_range = None
            if parse_run_error and parse_run_error.error_details:
                import re
                date_pattern = r'Date range: (\d{4}-\d{2}-\d{2}) to (\d{4}-\d{2}-\d{2})'
                match = re.search(date_pattern, parse_run_error.error_details)
                if match:
                    date_range = {
                        'start': match.group(1),
                        'end': match.group(2)
                    }
            
            last_run = ParliamentParseError.objects.filter(year=yr).order_by('-created_at').first()
            
            year_stats.append({
                'year': yr,
                'total_errors': total_errors,
                'date_range': date_range,
                'last_run_time': last_run.created_at if last_run else None
            })
        
        context = {
            'year_stats': year_stats,
        }
        
        return render(request, 'parliament_speeches/api_transparency_report_years.html', context)
    
    # Show detailed report for specific year
    # Get filter parameters
    error_type_filter = request.GET.get('error_type', '')
    entity_type_filter = request.GET.get('entity_type', '')
    
    # Get all errors for this year (exclude parse_run metadata unless specifically filtered)
    errors = ParliamentParseError.objects.filter(year=year)
    
    # Apply filters
    if error_type_filter:
        errors = errors.filter(error_type=error_type_filter)
    if entity_type_filter:
        errors = errors.filter(entity_type=entity_type_filter)
    else:
        # By default, exclude parse_run metadata from the list
        errors = errors.exclude(entity_type='parse_run')
    
    errors = errors.order_by('-created_at')
    
    # Get statistics (exclude parse_run from counts)
    total_errors = ParliamentParseError.objects.filter(year=year).exclude(entity_type='parse_run').count()
    errors_by_type = {}
    for error_type, _ in ParliamentParseError.ERROR_TYPES:
        count = ParliamentParseError.objects.filter(
            year=year,
            error_type=error_type
        ).exclude(entity_type='parse_run').count()
        if count > 0:
            errors_by_type[error_type] = count
    
    errors_by_entity_type = {}
    entity_types = ParliamentParseError.objects.filter(year=year).exclude(
        entity_type='parse_run'
    ).values_list('entity_type', flat=True).distinct()
    for entity_type in entity_types:
        if entity_type:
            count = ParliamentParseError.objects.filter(year=year, entity_type=entity_type).count()
            errors_by_entity_type[entity_type] = count
    
    # Get most recent parse run time for this year
    latest_error = ParliamentParseError.objects.filter(year=year).order_by('-created_at').first()
    last_run_time = latest_error.created_at if latest_error else None
    
    # Calculate date range from parse_run metadata
    parse_date_range = None
    import re
    
    # Get date range from parse_run entity type
    parse_run_error = ParliamentParseError.objects.filter(
        entity_type='parse_run',
        year=year
    ).first()
    
    if parse_run_error and parse_run_error.error_details:
        date_pattern = r'Date range: (\d{4}-\d{2}-\d{2}) to (\d{4}-\d{2}-\d{2})'
        match = re.search(date_pattern, parse_run_error.error_details)
        if match:
            parse_date_range = {
                'start': match.group(1),
                'end': match.group(2)
            }
    
    # If not found in parse_run, try from other error details (fallback)
    if not parse_date_range and latest_error and latest_error.error_details:
        date_pattern = r'Date range: (\d{4}-\d{2}-\d{2}) to (\d{4}-\d{2}-\d{2})'
        match = re.search(date_pattern, latest_error.error_details)
        if match:
            parse_date_range = {
                'start': match.group(1),
                'end': match.group(2)
            }
    
    # If still not found, try to infer from session errors (last resort)
    if not parse_date_range:
        session_errors = ParliamentParseError.objects.filter(
            entity_type='session',
            year=year,
            created_at=last_run_time  # Only errors from the last run
        ) if last_run_time else ParliamentParseError.objects.filter(entity_type='session', year=year)
        
        if session_errors.exists():
            # Extract dates from error details
            dates = []
            for error in session_errors:
                if error.error_details:
                    # Look for "Date: YYYY-MM-DD" pattern in error details
                    date_match = re.search(r'Date: (\d{4}-\d{2}-\d{2})', error.error_details)
                    if date_match:
                        dates.append(date_match.group(1))
            
            if dates:
                dates.sort()
                parse_date_range = {
                    'start': dates[0],
                    'end': dates[-1]
                }
    
    # Paginate errors
    paginator = Paginator(errors, 50)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)
    
    context = {
        'year': year,
        'page_obj': page_obj,
        'total_errors': total_errors,
        'errors_by_type': errors_by_type,
        'errors_by_entity_type': errors_by_entity_type,
        'last_run_time': last_run_time,
        'parse_date_range': parse_date_range,
        'error_type_filter': error_type_filter,
        'entity_type_filter': entity_type_filter,
        'available_error_types': ParliamentParseError.ERROR_TYPES,
        'available_entity_types': sorted([et for et in set(entity_types) if et is not None]),
    }
    
    return render(request, 'parliament_speeches/api_transparency_report.html', context)
