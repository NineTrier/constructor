from django.urls import path
from . import views
from .views import DocumentCreate

# Все адреса веб-сервиса
urlpatterns = [
    # path(То что вставляется после \, То, что вызывается в данном случае, Название данного перенаправления)
    path('', views.index, name='home'),
    path('about', views.about, name='about'),
    path('document/upload', DocumentCreate.as_view(), name='documents'),
    path('document/download', views.download),
    path('change', views.change_doc),
    path('upload_change', views.uploadRed),
    path('document/view', views.ViewDocument),
    path('document/update', views.UpdateDocument),
    path('document/new', views.create_New_Document, name='newdocuments'),
    path('document/savedelement/delete', views.DeleteSavedElement),
    path('document/savedelement/create', views.SaveSavedElement),
    path('document/variable/delete', views.DeleteVariable),
    path('document/variable/create', views.SaveVariable),
    path('document/delete', views.DeleteDocument)
]
