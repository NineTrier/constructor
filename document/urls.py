from django.urls import path
from . import views
from .views import DocumentCreate

# Все адреса веб-сервиса для работы с документом
urlpatterns = [
    # path(То что вставляется после \, То, что вызывается в данном случае, Название данного перенаправления)
    path('upload', DocumentCreate.as_view(), name='documents'), # загрузка документа на сервер
    path('download', views.download), # выгрузка документа
    path('view', views.ViewDocument), # просмотр документа
    path('viewAndCreate', views.ViewDocumentAndCreateDocument), # просмотр документа
    path('createDocumentsMultiple', views.CreateDocumentMultiple), # просмотр документа
    path('update', views.UpdateDocument), # обновление документа
    path('new', views.create_New_Document, name='newdocuments'), # создание нового документа
    # path('savedelement/delete', views.DeleteSavedElement), # удаление сохранённого элемента
    # path('savedelement/create', views.SaveSavedElement), # создание сохранённого элемента
    path('variable/delete', views.DeleteVariable), # удаление переменной
    path('variable/create', views.SaveVariable), # создание переменной
    path('delete', views.DeleteDocument), # удаление документа
    # path('save_image', views.SaveCover), # сохранение обложки
    path('save_doc_image', views.SaveImage), # сохранение картинки
    path('document_add', views.AddDocumentToUser), # копирование документа
    path('document_copy', views.CopyDocument), # копирование документа
    path('doctype_add', views.CreateDocType), # добавление типа документа
    path('doctype_remove', views.DeleteDocType), # удаление типа документа
    path('acceptFilters', views.AcceptFilters),
    path('connect_objects_to_document/<int:pk>/', views.connect_objects_to_document, name='connect_objects_to_document'),
    path('delete_object_from_document/<int:pk>/', views.delete_object_from_document, name='delete_object_from_document'),
]
