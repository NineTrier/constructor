from django.contrib import admin
import database_manager.models as models

admin.site.register([
    models.Object,
    models.Parameter,
])
