from django.contrib import admin
from .models import Profile, Organisation


admin.site.register(Organisation)
admin.site.register(Profile)