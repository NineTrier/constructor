from django.urls import path
from . import views

# Все адреса веб-сервиса
urlpatterns = [
    # path(То что вставляется после \, То, что вызывается в данном случае, Название данного перенаправления)
    path('', views.index, name='home'),
    path('about', views.about, name='about'),
    path('match_machine', views.match_machine, name='match_machine'),
]
