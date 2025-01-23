from django.urls import path
from . import views

# Все адреса веб-сервиса для работы с базой данных организации
urlpatterns = [
    # path(То что вставляется после \, То, что вызывается в данном случае, Название данного перенаправления)
    path('upload_csv/', views.upload_csv, name='upload_csv'),
    path('upload_csv_to_get_columns/', views.upload_csv_and_get_columns, name='upload_csv_to_get_columns'),
    path('view_data/', views.view_data, name='view_data'),
    path('object_manager/', views.object_manager, name='object_manager'),
    path('get_object/<int:pk>/', views.get_object, name='get_object'),
    path('delete_object/<int:pk>/', views.DeleteObject, name='delete_object'),
    path('get_data_from_object/<int:pk>/', views.post_data_from_object, name='get_data_from_object'),
    path('get_object_parameters/<int:pk>/', views.get_object_parameters, name='get_object_parameters'),
    path('get_objects_to_connect/', views.get_objects_to_connect, name='get_objects_to_connect'),
    
]
