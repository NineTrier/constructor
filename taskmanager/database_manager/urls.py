from django.urls import path
from . import views
from .views import CreateConnection, UpdateSQLVariableGet, DeleteSQLVariableGet, DeleteSQLVariableSet, UpdateSQLVariableSet, TestGetFromDB, setting_database, TestConnection, SaveSqlVariableGet, CreateSQLVariableGet, CreateSQLVariableSet

# Все адреса веб-сервиса
urlpatterns = [
    # path(То что вставляется после \, То, что вызывается в данном случае, Название данного перенаправления)
    path('create/', CreateConnection.as_view(), name='create_connection'),
    path('setting_database/', setting_database, name='setting_database'),
    path('test_connection/', TestConnection, name='test_connection'),
    path('create_sql_variable_get/', CreateSQLVariableGet.as_view(), name='create_sql_variable_get'),
    path('create_sql_variable_set/', CreateSQLVariableSet.as_view(), name='create_sql_variable_set'),
    path('save_variable_sql_get/', SaveSqlVariableGet, name='save_var'),
    path('test_get/', TestGetFromDB, name='test_get'),
    path('update_sql_set/', UpdateSQLVariableSet, name='update_set'),
    path('update_sql_get/', UpdateSQLVariableGet, name='update_get'),
    path('delete_sql_set/', DeleteSQLVariableSet, name='delete_set'),
    path('delete_sql_get/', DeleteSQLVariableGet, name='delete_get')
]
