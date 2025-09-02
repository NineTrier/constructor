from .models import DocumentsPattern, DocType
from django import forms


class DocumentForm(forms.ModelForm):
    class Meta:
        model = DocumentsPattern
        fields = ["name", "owner", "description", "file", "type", "documentOfOrganisation"]
        doctypes = forms.ModelChoiceField(queryset=DocType.objects.all(), empty_label=None, to_field_name="type")
        
        widgets = {
            "name": forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': 'Введите название',
                'value': "Документ1",
            }),
            "owner": forms.Select(attrs={
                'class': 'form-control',
                'placeholder': 'Введите автора',
            }),
            "description": forms.Textarea(attrs={
                'class': 'form-control',
                'placeholder': 'Введите описание',
                'required': False,
            }),
            "file": forms.ClearableFileInput(attrs={
                'class': 'form-control',
                'name': 'myfile1',
                'type': 'file',
                'id': 'inputFile',
            }),
            "documentOfOrganisation": forms.CheckboxInput()
        }
