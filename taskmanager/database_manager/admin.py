from django.contrib import admin
from .models import Dialect ,VariableSQLGet, VariableSQLSet, Connection,  VariableSQLSet_VariableSQLGet


admin.site.register(VariableSQLGet)
admin.site.register(VariableSQLSet)
admin.site.register(Connection)
admin.site.register(VariableSQLSet_VariableSQLGet)
admin.site.register(Dialect)
