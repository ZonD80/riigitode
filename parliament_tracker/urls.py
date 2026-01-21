"""
URL configuration for parliament_tracker project.
"""
from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static
from parliament_speeches.admin import admin_site
from parliament_speeches import views

urlpatterns = [
    path('', views.home, name='home'),
    path('plenary-sessions/', views.plenary_sessions_list, name='plenary_sessions_list'),
    path('plenary-sessions/<int:session_id>/', views.plenary_session_detail, name='plenary_session_detail'),
    path('politicians-agendas/', views.politicians_agendas_list, name='politicians_agendas_list'),
    path('agenda-politicians/', views.agenda_politicians_summary, name='agenda_politicians_summary'),
    path('decisions/', views.decisions_list, name='decisions_list'),
    path('politicians/', views.politicians_list, name='politicians_list'),
    path('politician/<int:politician_id>/', views.politician_detail, name='politician_detail'),
    path('politician/<int:politician_id>/activity/', views.politician_activity_graph, name='politician_activity_graph'),
    path('politician/<int:politician_id>/activity/<str:date_str>/', views.politician_daily_agendas, name='politician_daily_agendas'),
    path('politician/<int:politician_id>/profiling/', views.politician_profiling, name='politician_profiling'),
    path('politician/<int:politician_id>/profiling/agendas/<str:category>/', views.politician_profiling_agendas, name='politician_profiling_agendas'),
    path('politician/<int:politician_id>/profiling/sessions/<str:category>/', views.politician_profiling_sessions, name='politician_profiling_sessions'),
    path('politician/<int:politician_id>/profiling/months/<str:category>/', views.politician_profiling_months, name='politician_profiling_months'),
    path('politician/<int:politician_id>/profiling/years/<str:category>/', views.politician_profiling_years, name='politician_profiling_years'),
    path('politician/<int:politician_id>/profiling/agenda/<str:category>/<int:agenda_id>/', views.politician_profiling_agenda_detail, name='politician_profiling_agenda_detail'),
    path('politician/<int:politician_id>/profiling/session/<str:category>/<int:session_id>/', views.politician_profiling_session_detail, name='politician_profiling_session_detail'),
    path('politician/<int:politician_id>/profiling/month/<str:category>/<str:month>/', views.politician_profiling_month_detail, name='politician_profiling_month_detail'),
    path('politician/<int:politician_id>/profiling/year/<str:category>/<int:year>/', views.politician_profiling_year_detail, name='politician_profiling_year_detail'),
    path('agenda/<int:agenda_id>/', views.agenda_detail, name='agenda_detail'),
    path('api-transparency-report/', views.api_transparency_report, name='api_transparency_report'),
    path('api-transparency-report/<int:year>/', views.api_transparency_report, name='api_transparency_report_year'),
    path('page/<slug:slug>/', views.text_page, name='text_page'),
    path('admin/', admin.site.urls),
    path('parliament-admin/', admin_site.urls),  # Custom admin with counters
]

# Serve media files in development
if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
