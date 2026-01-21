from django.contrib import admin
from django.urls import reverse
from django.utils.html import format_html
from .models import (Politician, Faction, PoliticianFaction, PlenarySession, AgendaItem, Speech, 
                     PoliticianProfilePart, AgendaSummary, AgendaDecision, AgendaActivePolitician, StatisticsEntry, TextPage)


@admin.register(Politician)
class PoliticianAdmin(admin.ModelAdmin):
    list_display = ['full_name', 'active', 'gender', 'parliament_seniority', 'speeches_count', 'profiling_progress', 'created_at']
    list_filter = ['active', 'gender', 'parliament_seniority']
    search_fields = ['first_name', 'last_name', 'full_name', 'email']
    readonly_fields = ['uuid', 'created_at', 'updated_at']
    
    def speeches_count(self, obj):
        return obj.speeches.count()
    speeches_count.short_description = 'Speeches'
    speeches_count.admin_order_field = 'speeches__count'
    
    def profiling_progress(self, obj):
        if obj.profiles_required > 0:
            return f"{obj.profiles_already_profiled}/{obj.profiles_required} ({obj.profiling_percentage}%)"
        return "0/0 (0%)"
    profiling_progress.short_description = 'Profiling Progress'
    profiling_progress.admin_order_field = 'profiles_already_profiled'
    
    def get_queryset(self, request):
        return super().get_queryset(request).prefetch_related('speeches')
    fieldsets = (
        ('Basic Information', {
            'fields': ('uuid', 'first_name', 'last_name', 'full_name', 'active')
        }),
        ('Contact Information', {
            'fields': ('email', 'phone')
        }),
        ('Additional Information', {
            'fields': ('gender', 'date_of_birth', 'parliament_seniority', 'profiles_required', 'profiles_already_profiled')
        }),
        ('Metadata', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )


@admin.register(Faction)
class FactionAdmin(admin.ModelAdmin):
    list_display = ['name', 'members_count', 'created_at']
    search_fields = ['name']
    readonly_fields = ['uuid', 'created_at', 'updated_at']
    
    def members_count(self, obj):
        return obj.members.count()
    members_count.short_description = 'Members'
    members_count.admin_order_field = 'members__count'
    
    def get_queryset(self, request):
        return super().get_queryset(request).prefetch_related('members')


@admin.register(PoliticianFaction)
class PoliticianFactionAdmin(admin.ModelAdmin):
    list_display = ['politician', 'faction', 'start_date', 'end_date']
    list_filter = ['faction', 'start_date', 'end_date']
    search_fields = ['politician__full_name', 'faction__name']
    readonly_fields = ['created_at', 'updated_at']


@admin.register(PlenarySession)
class PlenarySessionAdmin(admin.ModelAdmin):
    list_display = ['title_preview', 'date', 'membership', 'plenary_session', 'agenda_items_count', 'edited']
    list_filter = ['membership', 'edited', 'date']
    search_fields = ['title']
    readonly_fields = ['created_at', 'updated_at']
    date_hierarchy = 'date'
    
    def title_preview(self, obj):
        return obj.title[:100] + "..." if len(obj.title) > 100 else obj.title
    title_preview.short_description = 'Title'
    
    def agenda_items_count(self, obj):
        return obj.agenda_items.count()
    agenda_items_count.short_description = 'Agenda Items'
    agenda_items_count.admin_order_field = 'agenda_items__count'
    
    def get_queryset(self, request):
        return super().get_queryset(request).prefetch_related('agenda_items')


@admin.register(AgendaItem)
class AgendaItemAdmin(admin.ModelAdmin):
    list_display = ['title_preview', 'plenary_session', 'date', 'speeches_count', 'view_complete_speech']
    list_filter = ['plenary_session__membership', 'date']
    search_fields = ['title', 'plenary_session__title']
    readonly_fields = ['uuid', 'created_at', 'updated_at']
    date_hierarchy = 'date'
    actions = ['view_multiple_complete_speeches']
    
    def title_preview(self, obj):
        return obj.title[:100] + "..." if len(obj.title) > 100 else obj.title
    title_preview.short_description = 'Title'
    
    def speeches_count(self, obj):
        return obj.speeches.count()
    speeches_count.short_description = 'Speeches'
    speeches_count.admin_order_field = 'speeches__count'
    
    def view_complete_speech(self, obj):
        if obj.speeches.exists():
            url = reverse('admin:parliament_speeches_agendaitem_complete_speech', args=[obj.pk])
            return format_html('<a href="{}" class="button">View Complete Speech</a>', url)
        return '-'
    view_complete_speech.short_description = 'Complete Speech'
    view_complete_speech.allow_tags = True
    
    def get_queryset(self, request):
        return super().get_queryset(request).select_related('plenary_session').prefetch_related('speeches')
    
    def get_urls(self):
        from django.urls import path
        urls = super().get_urls()
        custom_urls = [
            path(
                '<int:agenda_item_id>/complete-speech/',
                self.admin_site.admin_view(self.complete_speech_view),
                name='parliament_speeches_agendaitem_complete_speech',
            ),
        ]
        return custom_urls + urls
    
    def complete_speech_view(self, request, agenda_item_id):
        from django.shortcuts import get_object_or_404, render
        from django.contrib.admin.views.decorators import staff_member_required
        
        agenda_item = get_object_or_404(AgendaItem, pk=agenda_item_id)
        speeches = agenda_item.speeches.filter(event_type='SPEECH').order_by('date')
        
        context = {
            'title': f'Complete Speech - {agenda_item.title[:50]}...',
            'agenda_item': agenda_item,
            'speeches': speeches,
            'opts': self.model._meta,
            'has_change_permission': self.has_change_permission(request, agenda_item),
        }
        
        return render(request, 'admin/parliament_speeches/agendaitem/complete_speech.html', context)
    
    def view_multiple_complete_speeches(self, request, queryset):
        """Admin action to view complete speeches for multiple agenda items"""
        from django.shortcuts import render
        
        # Get all speeches for selected agenda items, ordered by agenda item date and speech date
        agenda_items = queryset.select_related('plenary_session').prefetch_related('speeches__politician').order_by('date')
        
        all_speeches = []
        for agenda_item in agenda_items:
            speeches = agenda_item.speeches.filter(event_type='SPEECH').order_by('date')
            if speeches.exists():
                all_speeches.append({
                    'agenda_item': agenda_item,
                    'speeches': speeches
                })
        
        context = {
            'title': f'Complete Speeches - {queryset.count()} Agenda Items',
            'agenda_items_with_speeches': all_speeches,
            'total_agenda_items': queryset.count(),
            'opts': self.model._meta,
        }
        
        return render(request, 'admin/parliament_speeches/agendaitem/multiple_complete_speeches.html', context)
    
    view_multiple_complete_speeches.short_description = "View complete speeches for selected agenda items"


@admin.register(Speech)
class SpeechAdmin(admin.ModelAdmin):
    list_display = ['speaker', 'politician', 'event_type', 'date', 'text_preview_admin', 'has_ai_summary', 'agenda_item']
    list_filter = ['event_type', 'date', 'politician']
    search_fields = ['speaker', 'text', 'politician__full_name']
    readonly_fields = ['uuid', 'created_at', 'updated_at']
    date_hierarchy = 'date'
    raw_id_fields = ['politician', 'agenda_item']
    actions = ['generate_ai_summaries_action']
    
    fieldsets = (
        ('Basic Information', {
            'fields': ('uuid', 'agenda_item', 'politician', 'event_type', 'date')
        }),
        ('Speech Content', {
            'fields': ('speaker', 'text', 'link')
        }),
        ('Metadata', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )
    
    def text_preview_admin(self, obj):
        return obj.text_preview
    text_preview_admin.short_description = 'Text Preview'
    
    def has_ai_summary(self, obj):
        return bool(obj.ai_summary)
    has_ai_summary.boolean = True
    has_ai_summary.short_description = 'Summary'
    
    def generate_ai_summaries_action(self, request, queryset):
        """Admin action to generate summaries for selected speeches"""
        from django.shortcuts import render
        from django.contrib import messages
        
        # Filter to speeches that don't have summaries yet
        speeches_without_summary = queryset.filter(event_type='SPEECH')
        
        if not speeches_without_summary.exists():
            messages.warning(request, "All selected speeches already have summaries.")
            return
        
        context = {
            'title': f'Generate Summaries for {speeches_without_summary.count()} Speeches',
            'speeches': speeches_without_summary[:20],  # Show first 20 for preview
            'total_count': speeches_without_summary.count(),
            'opts': self.model._meta,
        }
        
        return render(request, 'admin/parliament_speeches/speech/generate_summaries_confirmation.html', context)
    
    generate_ai_summaries_action.short_description = "Generate summaries for selected speeches"
    
    def get_queryset(self, request):
        return super().get_queryset(request).select_related('politician', 'agenda_item')


@admin.register(AgendaSummary)
class AgendaSummaryAdmin(admin.ModelAdmin):
    list_display = ['agenda_item_preview', 'has_xml_response', 'created_at', 'updated_at']
    list_filter = ['created_at', 'updated_at']
    search_fields = ['agenda_item__title', 'summary_text']
    readonly_fields = ['created_at', 'updated_at']
    raw_id_fields = ['agenda_item']
    
    fieldsets = (
        ('Basic Information', {
            'fields': ('agenda_item',)
        }),
        ('Summary (Estonian)', {
            'fields': ('summary_text',),
        }),
        ('Summary (English)', {
            'fields': ('summary_text_en',),
            'classes': ('collapse',)
        }),
        ('Summary (Russian)', {
            'fields': ('summary_text_ru',),
            'classes': ('collapse',)
        }),
        ('XML Response', {
            'fields': ('xml_response',),
            'classes': ('collapse',)
        }),
        ('Metadata', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )
    
    def agenda_item_preview(self, obj):
        return obj.agenda_item.title[:100] + "..." if len(obj.agenda_item.title) > 100 else obj.agenda_item.title
    agenda_item_preview.short_description = 'Agenda Item'
    
    def has_xml_response(self, obj):
        return bool(obj.xml_response)
    has_xml_response.boolean = True
    has_xml_response.short_description = 'Has XML'


@admin.register(AgendaDecision)
class AgendaDecisionAdmin(admin.ModelAdmin):
    list_display = ['agenda_item_preview', 'politician', 'decision_preview', 'is_collective', 'created_at']
    list_filter = ['created_at', 'politician']
    search_fields = ['agenda_item__title', 'politician__full_name', 'decision_text']
    readonly_fields = ['created_at', 'updated_at']
    raw_id_fields = ['agenda_item', 'politician']
    
    fieldsets = (
        ('Basic Information', {
            'fields': ('agenda_item', 'politician')
        }),
        ('Decision (Estonian)', {
            'fields': ('decision_text',),
        }),
        ('Decision (English)', {
            'fields': ('decision_text_en',),
            'classes': ('collapse',)
        }),
        ('Decision (Russian)', {
            'fields': ('decision_text_ru',),
            'classes': ('collapse',)
        }),
        ('Metadata', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )
    
    def agenda_item_preview(self, obj):
        return obj.agenda_item.title[:50] + "..." if len(obj.agenda_item.title) > 50 else obj.agenda_item.title
    agenda_item_preview.short_description = 'Agenda Item'
    
    def decision_preview(self, obj):
        return obj.decision_text[:100] + "..." if len(obj.decision_text) > 100 else obj.decision_text
    decision_preview.short_description = 'Decision'
    
    def is_collective(self, obj):
        return obj.politician is None
    is_collective.boolean = True
    is_collective.short_description = 'Collective Decision'


@admin.register(AgendaActivePolitician)
class AgendaActivePoliticianAdmin(admin.ModelAdmin):
    list_display = ['agenda_item_preview', 'politician', 'activity_preview', 'created_at']
    list_filter = ['politician', 'created_at']
    search_fields = ['agenda_item__title', 'politician__full_name', 'activity_description']
    readonly_fields = ['created_at', 'updated_at']
    raw_id_fields = ['agenda_item', 'politician']
    
    fieldsets = (
        ('Basic Information', {
            'fields': ('agenda_item', 'politician')
        }),
        ('Activity (Estonian)', {
            'fields': ('activity_description',),
        }),
        ('Activity (English)', {
            'fields': ('activity_description_en',),
            'classes': ('collapse',)
        }),
        ('Activity (Russian)', {
            'fields': ('activity_description_ru',),
            'classes': ('collapse',)
        }),
        ('Metadata', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )
    
    def agenda_item_preview(self, obj):
        return obj.agenda_item.title[:50] + "..." if len(obj.agenda_item.title) > 50 else obj.agenda_item.title
    agenda_item_preview.short_description = 'Agenda Item'
    
    def activity_preview(self, obj):
        return obj.activity_description[:100] + "..." if len(obj.activity_description) > 100 else obj.activity_description
    activity_preview.short_description = 'Activity'


# Custom admin site with counters
class ParliamentAdminSite(admin.AdminSite):
    site_header = 'Estonian Parliament Speech Management'
    site_title = 'Parliament Admin'
    index_title = 'Parliament speeches and politicians management'
    
    def index(self, request, extra_context=None):
        """
        Display the main admin index page, which lists all of the installed
        apps that have been registered in this site with counters.
        """
        extra_context = extra_context or {}
        
        # Add model counts to context
        model_counts = {
            'politicians': Politician.objects.count(),
            'factions': Faction.objects.count(),
            'politician_factions': PoliticianFaction.objects.count(),
            'plenary_sessions': PlenarySession.objects.count(),
            'agenda_items': AgendaItem.objects.count(),
            'speeches': Speech.objects.count(),
            'politician_profile_parts': PoliticianProfilePart.objects.count(),
            'agenda_summaries': AgendaSummary.objects.count(),
            'agenda_decisions': AgendaDecision.objects.count(),
            'agenda_active_politicians': AgendaActivePolitician.objects.count(),
            'text_pages': TextPage.objects.count(),
        }
        extra_context['model_counts'] = model_counts
        
        return super().index(request, extra_context)
    
    def each_context(self, request):
        """
        Return a dictionary of variables to put in the template context for
        every page in the admin site.
        """
        context = super().each_context(request)
        
        # Add model counts to every admin page
        context.update({
            'model_counts': {
                'politicians': Politician.objects.count(),
                'factions': Faction.objects.count(),
                'politician_factions': PoliticianFaction.objects.count(),
                'plenary_sessions': PlenarySession.objects.count(),
                'agenda_items': AgendaItem.objects.count(),
                'speeches': Speech.objects.count(),
                'politician_profile_parts': PoliticianProfilePart.objects.count(),
                'agenda_summaries': AgendaSummary.objects.count(),
                'agenda_decisions': AgendaDecision.objects.count(),
                'agenda_active_politicians': AgendaActivePolitician.objects.count(),
                'text_pages': TextPage.objects.count(),
            }
        })
        
        return context


# Create custom admin site instance
admin_site = ParliamentAdminSite(name='parliament_admin')

# Register models with the custom admin site
admin_site.register(Politician, PoliticianAdmin)
admin_site.register(Faction, FactionAdmin)
admin_site.register(PoliticianFaction, PoliticianFactionAdmin)
admin_site.register(PlenarySession, PlenarySessionAdmin)
admin_site.register(AgendaItem, AgendaItemAdmin)
admin_site.register(Speech, SpeechAdmin)
admin_site.register(AgendaSummary, AgendaSummaryAdmin)
admin_site.register(AgendaDecision, AgendaDecisionAdmin)
admin_site.register(AgendaActivePolitician, AgendaActivePoliticianAdmin)


# Old profile admin classes removed - using PoliticianProfilePart instead


@admin.register(PoliticianProfilePart)
class PoliticianProfilePartAdmin(admin.ModelAdmin):
    list_display = ['politician', 'category', 'period_type', 'period_description_short', 'speeches_analyzed', 'created_at']
    list_filter = ['category', 'period_type', 'created_at']
    search_fields = ['politician__full_name', 'analysis']
    readonly_fields = ['created_at', 'updated_at']
    raw_id_fields = ['politician', 'agenda_item', 'plenary_session']
    
    fieldsets = (
        ('Basic Information', {
            'fields': ('politician', 'category', 'period_type')
        }),
        ('Period Identifiers', {
            'fields': ('agenda_item', 'plenary_session', 'month', 'year'),
            'description': 'Only fill the field that corresponds to the period_type above'
        }),
        ('Analysis Data', {
            'fields': ('speeches_analyzed', 'date_range_start', 'date_range_end')
        }),
        ('Analysis (Estonian)', {
            'fields': ('analysis',),
        }),
        ('Analysis (English)', {
            'fields': ('analysis_en',),
            'classes': ('collapse',)
        }),
        ('Analysis (Russian)', {
            'fields': ('analysis_ru',),
            'classes': ('collapse',)
        }),
        ('Metrics', {
            'fields': ('metrics',),
            'classes': ('collapse',)
        }),
        ('Metadata', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )
    
    def period_description_short(self, obj):
        """Get shortened period description for list display"""
        desc = obj.get_period_description()
        return desc[:50] + "..." if len(desc) > 50 else desc
    period_description_short.short_description = 'Period'
    period_description_short.admin_order_field = 'period_type'


# Register with custom admin site
admin_site.register(PoliticianProfilePart, PoliticianProfilePartAdmin)


# MediaReaction admin removed - will be rewritten from scratch later


@admin.register(StatisticsEntry)
class StatisticsEntryAdmin(admin.ModelAdmin):
    list_display = ['name', 'value', 'percentage', 'updated_at']
    list_filter = ['created_at', 'updated_at']
    search_fields = ['name', 'name_en', 'name_ru']
    readonly_fields = ['created_at', 'updated_at']
    
    fieldsets = (
        ('Basic Information', {
            'fields': ('name', 'name_en', 'name_ru')
        }),
        ('Values', {
            'fields': ('value', 'percentage')
        }),
        ('Metadata', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )
    
    def has_add_permission(self, request):
        # Only allow adding through the gather_stats management command
        return request.user.is_superuser
    
    def has_delete_permission(self, request, obj=None):
        # Only allow deleting for superusers
        return request.user.is_superuser


@admin.register(TextPage)
class TextPageAdmin(admin.ModelAdmin):
    list_display = ['title', 'slug', 'is_published', 'show_in_menu', 'menu_order', 'updated_at']
    list_filter = ['is_published', 'show_in_menu', 'created_at', 'updated_at']
    search_fields = ['title', 'title_en', 'title_ru', 'slug', 'content']
    readonly_fields = ['created_at', 'updated_at']
    prepopulated_fields = {'slug': ('title',)}
    
    fieldsets = (
        ('Basic Information', {
            'fields': ('slug', 'is_published', 'show_in_menu', 'menu_order')
        }),
        ('Estonian Content', {
            'fields': ('title', 'meta_description', 'keywords', 'content'),
        }),
        ('English Content', {
            'fields': ('title_en', 'meta_description_en', 'keywords_en', 'content_en'),
            'classes': ('collapse',)
        }),
        ('Russian Content', {
            'fields': ('title_ru', 'meta_description_ru', 'keywords_ru', 'content_ru'),
            'classes': ('collapse',)
        }),
        ('Metadata', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )
    
    class Media:
        css = {
            'all': ('admin/css/widgets.css',)
        }
        js = ('admin/js/admin/RelatedObjectLookups.js',)


# Register TextPage and StatisticsEntry with custom admin site after class definitions
admin_site.register(TextPage, TextPageAdmin)
admin_site.register(StatisticsEntry, StatisticsEntryAdmin)


# Also keep the default admin registrations for backward compatibility
admin.site.site_header = 'Estonian Parliament Speech Management'
admin.site.site_title = 'Parliament Admin'
admin.site.index_title = 'Parliament speeches and politicians management'
