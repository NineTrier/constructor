from django.contrib import admin
from .models import Documents, Fonts, SavedElements, DocType, VariableBlock


admin.site.register(Documents)
admin.site.register(Fonts)
admin.site.register(SavedElements)
admin.site.register(DocType)
admin.site.register(VariableBlock)
