from django.contrib import admin
import database_manager.models as models

admin.site.register([
    models.Connection,
    models.Connection_Organisation,
    models.Dialect,
    models.VariableSQLGet,
    models.VariableSQLSet,
    models.VariableSQLSet_VariableSQLGet
])
