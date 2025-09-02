from django.urls import path
from . import views

# Все адреса веб-сервиса для работы с базой данных организации
urlpatterns = [
    # path(То что вставляется после \, То, что вызывается в данном случае, Название данного перенаправления)
    path('upload_csv/', views.upload_csv, name='upload_csv'),
    path('create_new_object/', views.create_new_object, name='create_new_object'),
    path('update_csv/<int:pk>/', views.update_csv, name='update_csv'),
    path('update_object/<int:pk>/', views.update_object, name='update_object'),
    path('update_object/<int:pk>/add_param_object_link/', views.add_objects_links, name='add_objects_link'),
    path('delete_param/<int:pk>/', views.delete_param, name='delete_param'),
    path('upload_csv_to_get_columns/', views.upload_csv_and_get_columns, name='upload_csv_to_get_columns'),
    path('view_data/', views.view_data, name='view_data'),
    path('object_manager/', views.object_manager, name='object_manager'),
    path('get_object/<int:pk>/', views.get_object, name='get_object'),
    path('add_element_to_object/<int:pk>/', views.add_element_to_object, name='add_element'),
    path('update_element_to_object/<int:pk>/', views.update_element_to_object, name='update_element'),
    path('delete_element_to_object/<int:pk>/', views.delete_element_to_object, name='delete_element'),
    path('delete_object/<int:pk>/', views.DeleteObject, name='delete_object'),
    path('get_data_from_object/<int:pk>/', views.post_data_from_object, name='get_data_from_object'),
    path('get_object_parameters/<int:pk>/', views.get_object_parameters, name='get_object_parameters'),
    path('get_objects_to_connect/', views.get_objects_to_connect, name='get_objects_to_connect'),
    path('file_changer/<int:pk>/', views.generate_excel_file, name='file_changer'),
]
