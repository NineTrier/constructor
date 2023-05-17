from django.contrib.auth.forms import AuthenticationForm, UsernameField
from .models import Connection, VariableSQLGet, VariableSQLSet
from django import forms
from django.core.files.images import get_image_dimensions


class ConnectionForm(forms.ModelForm):
    class Meta:
        model = Connection
        fields = '__all__'
        widgets = {
            'dialect': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': '',
                'id': 'dialect'
            }),
            'username': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': '',
                'id': 'username'
            }),
            'password': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': '',
                'id': 'password'
            }),
            'host': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': '',
                'id': 'host'
            }),
            'port': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': '',
                'id': 'port'
            }),
            'service': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': '',
                'id': 'service'
            })  
        }

class SQLVariableFormGet(forms.ModelForm):
    class Meta:
        model = VariableSQLGet
        fields = '__all__'
        widgets = {
            'name': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': '',
                'id': 'name'
            }),
            'sql': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': '',
                'id': 'sql'
            })
        }

class SQLVariableFormSet(forms.ModelForm):
    class Meta:
        model = VariableSQLGet
        fields = '__all__'
        widgets = {
            'name': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': '',
                'id': 'name'
            }),
            'sql': forms.Textarea(attrs={
                'class': 'form-control',
                'placeholder': '',
                'id': 'sql'
            })
        }
