from django.contrib import admin
from .models import VariableSQLGet, VariableSQLSet, Connection, VariableSQLGet_Variable, VariableSQLSet_Variable, VariableSQLSet_VariableSQLGet


admin.site.register(VariableSQLGet)
admin.site.register(VariableSQLSet)
admin.site.register(Connection)
admin.site.register(VariableSQLSet_VariableSQLGet)
admin.site.register(VariableSQLGet_Variable)
admin.site.register(VariableSQLSet_Variable)