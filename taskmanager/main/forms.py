from .models import Document
from django.forms import ModelForm, TextInput, Textarea, FileField, ClearableFileInput


class DocumentForm(ModelForm):
    class Meta:
        model = Document
        fields = ["name", "owner", "description", "file"]
        widgets = {
            "name": TextInput(attrs={
                'class': 'form-control',
                'placeholder': 'Введите название',
            }),
            "owner": TextInput(attrs={
                'class': 'form-control',
                'placeholder': 'Введите автора',
            }),
            "description": Textarea(attrs={
                'class': 'form-control',
                'placeholder': 'Введите описание',
            }),
            "file": ClearableFileInput(attrs={
                'class': 'form-control',
                'name': 'myfile1',
                'type': 'file',

            })
        }
