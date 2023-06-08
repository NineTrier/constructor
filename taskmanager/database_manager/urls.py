from django.urls import path
from . import views

# Все адреса веб-сервиса для работы с базой данных организации
urlpatterns = [
    # path(То что вставляется после \, То, что вызывается в данном случае, Название данного перенаправления)
    path('create/', views.CreateConnection.as_view(), name='create_connection'),
    path('setting_database/', views.setting_database, name='setting_database'),
    path('test_connection/', views.TestConnection, name='test_connection'),
    path('create_sql_variable_get/<str:pk>/', views.CreateSQLVariableGet.as_view(), name='create_sql_variable_get'),
    path('create_sql_variable_set/<str:pk>/', views.CreateSQLVariableSet.as_view(), name='create_sql_variable_set'),
    path('save_variable_sql_get/', views.SaveSqlVariableGet, name='save_var'),
    path('test_get/', views.TestGetFromDB, name='test_get'),
    path('update_sql_set/', views.UpdateSQLVariableSet, name='update_set'),
    path('update_sql_get/', views.UpdateSQLVariableGet, name='update_get'),
    path('delete_sql_set/', views.DeleteSQLVariableSet, name='delete_set'),
    path('delete_sql_get/', views.DeleteSQLVariableGet, name='delete_get'),
    path('get_tables/', views.get_all_table, name='get_tables'),
    path('get_table_body/', views.GetFromDBByTable),
    path('update_tables/', views.update_table, name='update_table')
]
