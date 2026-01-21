"""
Template filters for localization support
"""
from django import template
import markdown
from django.utils.safestring import mark_safe
from django.utils.html import linebreaks as django_linebreaks

register = template.Library()

@register.filter
def localized_title(obj, language):
    """Get localized title for an object"""
    if hasattr(obj, 'get_localized_title'):
        # Check if the method accepts show_missing parameter
        try:
            return obj.get_localized_title(language, show_missing=True)
        except TypeError:
            # Fallback for methods that don't accept show_missing parameter
            return obj.get_localized_title(language)
    return getattr(obj, 'title', '')

@register.filter
def localized_ai_summary(obj, language):
    """Get localized AI summary for an object"""
    if hasattr(obj, 'get_localized_ai_summary'):
        return obj.get_localized_ai_summary(language, show_missing=True)
    return getattr(obj, 'ai_summary', '')

@register.filter
def localized_analysis(obj, language):
    """Get localized analysis for a politician profile"""
    if hasattr(obj, 'get_localized_analysis'):
        return obj.get_localized_analysis(language)
    return getattr(obj, 'analysis_et', '')

@register.filter
def localized_decision(obj, language):
    """Get localized decision for an AgendaDecision"""
    if hasattr(obj, 'get_localized_decision'):
        return obj.get_localized_decision(language, show_missing=True)
    return getattr(obj, 'decision_text', '')

@register.filter
def localized_summary(obj, language):
    """Get localized summary for an AgendaSummary"""
    if hasattr(obj, 'get_localized_summary'):
        return obj.get_localized_summary(language, show_missing=True)
    return getattr(obj, 'summary_text', '')

@register.filter
def localized_activity(obj, language):
    """Get localized activity description for an AgendaActivePolitician"""
    if hasattr(obj, 'get_localized_activity'):
        return obj.get_localized_activity(language, show_missing=True)
    return getattr(obj, 'activity_description', '')

@register.simple_tag(takes_context=True)
def localized_title_tag(context, obj):
    """Template tag to get localized title using current language from context"""
    language = context.get('current_language', 'et')
    if hasattr(obj, 'get_localized_title'):
        # Check if the method accepts show_missing parameter
        try:
            return obj.get_localized_title(language, show_missing=True)
        except TypeError:
            # Fallback for methods that don't accept show_missing parameter
            return obj.get_localized_title(language)
    return getattr(obj, 'title', '')

@register.simple_tag(takes_context=True)
def localized_ai_summary_tag(context, obj):
    """Template tag to get localized AI summary using current language from context"""
    language = context.get('current_language', 'et')
    
    # For AgendaItem objects, use structured_summary
    if hasattr(obj, 'structured_summary'):
        try:
            return obj.structured_summary.get_localized_summary(language, show_missing=True)
        except AttributeError:
            return ''
    
    # For Speech objects, keep using the old method for now
    if hasattr(obj, 'get_localized_ai_summary'):
        return obj.get_localized_ai_summary(language, show_missing=True)
    return getattr(obj, 'ai_summary', '')

@register.simple_tag(takes_context=True)
def localized_analysis_tag(context, obj):
    """Template tag to get localized analysis using current language from context"""
    language = context.get('current_language', 'et')
    if hasattr(obj, 'get_localized_analysis'):
        return obj.get_localized_analysis(language, show_missing=True)
    return getattr(obj, 'analysis_et', '')

@register.simple_tag(takes_context=True)
def localized_activity_tag(context, obj):
    """Template tag to get localized activity description using current language from context"""
    language = context.get('current_language', 'et')
    if hasattr(obj, 'get_localized_activity'):
        text = obj.get_localized_activity(language, show_missing=True)
        return mark_safe(django_linebreaks(text))
    text = getattr(obj, 'activity_description', '')
    return mark_safe(django_linebreaks(text))

@register.simple_tag(takes_context=True)
def localized_decision_tag(context, obj):
    """Template tag to get localized decision text using current language from context"""
    language = context.get('current_language', 'et')
    if hasattr(obj, 'get_localized_decision'):
        text = obj.get_localized_decision(language, show_missing=True)
        return mark_safe(django_linebreaks(text))
    text = getattr(obj, 'decision_text', '')
    return mark_safe(django_linebreaks(text))

@register.simple_tag(takes_context=True)
def localized_summary_tag(context, obj):
    """Template tag to get localized summary text using current language from context"""
    language = context.get('current_language', 'et')
    if hasattr(obj, 'get_localized_summary'):
        text = obj.get_localized_summary(language, show_missing=True)
        return mark_safe(django_linebreaks(text))
    text = getattr(obj, 'summary_text', '')
    return mark_safe(django_linebreaks(text))

@register.filter
def get_item(dictionary, key):
    """Get item from dictionary by key - useful for template dictionary lookups"""
    if isinstance(dictionary, dict):
        return dictionary.get(key, key)
    return key

@register.filter
def localized_content(obj, language):
    """Get localized content for a TextPage"""
    if hasattr(obj, 'get_localized_content'):
        return obj.get_localized_content(language)
    return getattr(obj, 'content', '')

@register.filter
def localized_meta_description(obj, language):
    """Get localized meta description for a TextPage"""
    if hasattr(obj, 'get_localized_meta_description'):
        return obj.get_localized_meta_description(language)
    return getattr(obj, 'meta_description', '')

@register.filter
def localized_keywords(obj, language):
    """Get localized keywords for a TextPage"""
    if hasattr(obj, 'get_localized_keywords'):
        return obj.get_localized_keywords(language)
    return getattr(obj, 'keywords', '')

@register.simple_tag(takes_context=True)
def localized_content_tag(context, obj):
    """Template tag to get localized content using current language from context"""
    language = context.get('current_language', 'et')
    if hasattr(obj, 'get_localized_content'):
        return obj.get_localized_content(language)
    return getattr(obj, 'content', '')

@register.simple_tag(takes_context=True)
def localized_meta_description_tag(context, obj):
    """Template tag to get localized meta description using current language from context"""
    language = context.get('current_language', 'et')
    if hasattr(obj, 'get_localized_meta_description'):
        return obj.get_localized_meta_description(language)
    return getattr(obj, 'meta_description', '')

@register.simple_tag(takes_context=True)
def localized_keywords_tag(context, obj):
    """Template tag to get localized keywords using current language from context"""
    language = context.get('current_language', 'et')
    if hasattr(obj, 'get_localized_keywords'):
        return obj.get_localized_keywords(language)
    return getattr(obj, 'keywords', '')

@register.filter
def markdown_to_html(text):
    """Convert Markdown text to HTML"""
    if not text:
        return ''
    
    # Configure markdown with extensions for better formatting
    md = markdown.Markdown(extensions=[
        'markdown.extensions.extra',      # Tables, fenced code blocks, etc.
        'markdown.extensions.nl2br',     # Convert newlines to <br>
        'markdown.extensions.sane_lists', # Better list handling
    ])
    
    html = md.convert(text)
    return mark_safe(html)
