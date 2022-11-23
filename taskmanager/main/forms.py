from .models import Documents
from django.forms import ModelForm, TextInput, Textarea, FileField, ClearableFileInput


class DocumentForm(ModelForm):
    class Meta:
        model = Documents
        fields = ["name", "owner", "description", "file"]
        widgets = {
            "name": TextInput(attrs={
                'class': 'form-control',
                'placeholder': 'Введите название',
                'value': "Документ1",
            }),
            "owner": TextInput(attrs={
                'class': 'form-control',
                'placeholder': 'Введите автора',
                'value': 'Александр',
            }),
            "description": Textarea(attrs={
                'class': 'form-control',
                'placeholder': 'Введите описание',
                'required': False,
            }),
            "file": ClearableFileInput(attrs={
                'class': 'form-control',
                'name': 'myfile1',
                'type': 'file',
                'id': 'inputFile',
            })
        }
