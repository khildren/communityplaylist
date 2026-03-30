from django.urls import path
from . import views

urlpatterns = [
    path('', views.event_list, name='event_list'),
    path('submit/', views.event_submit, name='event_submit'),
    path('archive/', views.event_archive, name='event_archive'),
    path('genres/', views.genre_autocomplete, name='genre_autocomplete'),
    path('events/<slug:slug>/', views.event_detail, name='event_detail'),
    path('events/<slug:slug>/claim/', views.claim_event, name='claim_event'),
    path('events/<slug:slug>/edit/', views.event_edit, name='event_edit'),
    # Calendar
    path('feed/events.ics', views.calendar_feed, name='calendar_feed'),
    path('subscribe/', views.calendar_subscribe, name='calendar_subscribe'),
    path('features/', views.features_page, name='features'),
    # Auth
    path('register/', views.register_view, name='register'),
    path('login/', views.login_view, name='login'),
    path('logout/', views.logout_view, name='logout'),
    path('dashboard/', views.dashboard, name='dashboard'),
]
