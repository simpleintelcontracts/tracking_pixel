# tracker/urls.py
from django.urls import path
from . import views

urlpatterns = [
    path('collect/', views.CollectView.as_view(), name='collect'),
    path('collect.gif', views.collect_gif_view, name='collect_gif'),
    path('dashboard/', views.dashboard_view, name='dashboard'),
]
