# tracker/urls.py
from django.urls import path
from .views import CollectView, collect_gif_view

urlpatterns = [
    path('collect/', CollectView.as_view(), name='collect_pixel'),
    path('collect.gif', collect_gif_view, name='collect_gif_pixel'),
]
