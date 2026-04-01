from django.urls import path
from . import views

urlpatterns = [
    path('',                      views.board_list,  name='board_list'),
    path('new/',                  views.board_new,   name='board_new'),
    path('aid/',                  views.board_aid,   name='board_aid'),
    path('<int:pk>-<slug:slug>/', views.board_topic, name='board_topic'),
]
