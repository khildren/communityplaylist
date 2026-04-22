from django.urls import path
from . import views

urlpatterns = [
    path('',                      views.board_list,  name='board_list'),
    path('new/',                  views.board_new,   name='board_new'),
    path('aid/',                  views.board_aid,   name='board_aid'),
    path('<int:pk>-<slug:slug>/', views.board_topic, name='board_topic'),
    # Free & Trade
    path('give/',                      views.give_list,   name='give_list'),
    path('give/new/',                  views.give_new,    name='give_new'),
    path('give/<int:pk>-<slug:slug>/', views.give_detail, name='give_detail'),
    path('give/<int:pk>/claim/',       views.give_claim,  name='give_claim'),
]
