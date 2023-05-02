from django.urls import path
from . import views
from .views import DocumentCreate

# Все адреса веб-сервиса
urlpatterns = [
    # path(То что вставляется после \, То, что вызывается в данном случае, Название данного перенаправления)
    path('upload', DocumentCreate.as_view(), name='documents'),
    path('download', views.download),
    # path('change', views.change_doc),
    # path('upload_change', views.uploadRed),
    path('view', views.ViewDocument),
    path('update', views.UpdateDocument),
    path('new', views.create_New_Document, name='newdocuments'),
    path('savedelement/delete', views.DeleteSavedElement),
    path('savedelement/create', views.SaveSavedElement),
    path('variable/delete', views.DeleteVariable),
    path('variable/create', views.SaveVariable),
    path('delete', views.DeleteDocument)
]
