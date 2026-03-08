from django.urls import path

from . import views

#
# URL configuration for the database_manager application.
#
# This file mirrors the upstream routing but adds a route for saving
# row‑level links (`save_row_link`). All other routes are preserved to
# maintain backwards compatibility.
#

urlpatterns = [
    # Upload a new CSV and create an object
    path('upload_csv/', views.upload_csv, name='upload_csv'),
    path('create_new_object/', views.create_new_object, name='create_new_object'),
    path('update_csv/<int:pk>/', views.update_csv, name='update_csv'),
    # Edit object metadata and link parameters
    path('update_object/<int:pk>/', views.update_object, name='update_object'),
    path('update_object/<int:pk>/add_param_object_link/', views.add_objects_links, name='add_objects_link'),
    path('delete_param/<int:pk>/', views.delete_param, name='delete_param'),
    # Helpers for client‑side CSV previews
    path('upload_csv_to_get_columns/', views.upload_csv_and_get_columns, name='upload_csv_to_get_columns'),
    path('view_data/', views.view_data, name='view_data'),
    # Object management
    path('object_manager/', views.object_manager, name='object_manager'),
    path('get_object/<int:pk>/', views.get_object, name='get_object'),
    path('api/v1/objects/', views.api_v1_objects_list, name='api_v1_objects_list'),
    path('api/v1/objects/<int:pk>/records/', views.api_v1_object_records, name='api_v1_object_records'),
    path('api/v1/objects/<int:pk>/links-meta/', views.api_v1_object_links_meta, name='api_v1_object_links_meta'),
    path('api/v1/objects/<int:pk>/links-meta/<int:meta_id>/', views.api_v1_object_links_meta_detail, name='api_v1_object_links_meta_detail'),
    path('api/v1/objects/<int:pk>/records/<str:record_uid>/', views.api_v1_object_record_detail, name='api_v1_object_record_detail'),
    path('api/v1/objects/<int:pk>/records/<str:record_uid>/links/', views.api_v1_record_links, name='api_v1_record_links'),
    # Row operations
    path('add_element_to_object/<int:pk>/', views.add_element_to_object, name='add_element'),
    path('update_element_to_object/<int:pk>/', views.update_element_to_object, name='update_element'),
    path('delete_element_to_object/<int:pk>/', views.delete_element_to_object, name='delete_element'),
    # Object deletion
    path('delete_object/<int:pk>/', views.DeleteObject, name='delete_object'),
    # Data retrieval
    path('get_data_from_object/<int:pk>/', views.post_data_from_object, name='get_data_from_object'),
    path('get_object_parameters/<int:pk>/', views.get_object_parameters, name='get_object_parameters'),
    path('get_row_links/<int:pk>/', views.get_row_links, name='get_row_links'),
    path('get_objects_to_connect/', views.get_objects_to_connect, name='get_objects_to_connect'),
    # Excel generation
    path('file_changer/<int:pk>/', views.generate_excel_file, name='file_changer'),
    # New API endpoint for saving row‑level links between parent and child rows
    path('save_row_link/<int:object_link_id>/', views.save_row_link, name='save_row_link'),
    # Endpoint to remove an object link
    path('delete_object_link/<int:pk>/', views.delete_object_link, name='delete_object_link'),
]
