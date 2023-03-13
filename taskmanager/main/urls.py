from django.urls import path
from . import views
from .views import DocumentCreate

# Все адреса веб-сервиса
urlpatterns = [
    # path(То что вставляется после \, То, что вызывается в данном случае, Название данного перенаправления)
    path('', views.index, name='home'),
    path('about', views.about, name='about'),
    path('documents', DocumentCreate.as_view(), name='documents'),
    path('download', views.download),
    path('change', views.change_doc),
    path('upload_change', views.uploadRed),
    path('doc', views.ViewDocument),
    path('docupdate', views.UpdateDocument),
    path('newdocument', views.create_New_Document, name='newdocuments'),
]
