from django.contrib.auth.forms import AuthenticationForm, UsernameField
from .models import Profile, Organisation
from django import forms
from django.core.files.images import get_image_dimensions


class UserLoginForm(AuthenticationForm):

    def __init__(self, *args, **kwargs):
        super(UserLoginForm, self).__init__(*args, **kwargs)

    username = UsernameField(widget=forms.TextInput(
        attrs={'class': 'form-control', 'placeholder': '', 'id': 'username'}))
    password = forms.CharField(widget=forms.PasswordInput(
        attrs={
            'class': 'form-control',
            'placeholder': '',
            'id': 'password',
        }
    ))


class ProfileForm(forms.ModelForm):
    class Meta:
        model = Profile
        fields = '__all__'
        Organisation = forms.ModelChoiceField(queryset=Organisation.objects.all(), empty_label=None, to_field_name="organisation")
        widgets = {
            'firstName': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': '',
                'id': 'firstName'
            }),
            'lastName': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': '',
                'id': 'lastName'
            }),
            'middleName': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': '',
                'id': 'middleName'
            }),
            'profile_pic': forms.ClearableFileInput(attrs={
                'class': 'form-control',
                'name': 'profile_pic',
                'type': 'file',
                'id': 'inputFile',
            })
        }
    def clean_avatar(self):
        avatar = self.cleaned_data['avatar']
        try:
            w, h = get_image_dimensions(avatar)

            #validate dimensions
            max_width = max_height = 100
            if w > max_width or h > max_height:
                raise forms.ValidationError(
                    u'Please use an image that is '
                     '%s x %s pixels or smaller.' % (max_width, max_height))

            #validate content type
            main, sub = avatar.content_type.split('/')
            if not (main == 'image' and sub in ['jpeg', 'pjpeg', 'gif', 'png']):
                raise forms.ValidationError(u'Please use a JPEG, '
                    'GIF or PNG image.')

            #validate file size
            if len(avatar) > (20 * 1024):
                raise forms.ValidationError(
                    u'Avatar file size may not exceed 20k.')
        except AttributeError:
            """
            Handles case when we are updating the user profile
            and do not supply a new avatar
            """
            pass
