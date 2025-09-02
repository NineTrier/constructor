from django.contrib import admin
from .models import DocumentsPattern, Fonts, SavedElements, DocType, VariableBlock, Document_ParentDocument


admin.site.register(DocumentsPattern)
admin.site.register(Fonts)
admin.site.register(SavedElements)
admin.site.register(DocType)
admin.site.register(VariableBlock)
admin.site.register(Document_ParentDocument)
