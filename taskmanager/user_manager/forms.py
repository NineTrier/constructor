from typing import List

from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.forms import AuthenticationForm, UserCreationForm, UsernameField
from django.utils.translation import gettext_lazy as _

from .models import Organisation, Profile
from .roles import (
    ROLE_GROUP_PREFIX,
    ensure_roles_exist,
    get_role_group,
    role_choices,
)


User = get_user_model()


class UserLoginForm(AuthenticationForm):
    username = UsernameField(
        widget=forms.TextInput(
            attrs={
                "class": "form-control",
                "placeholder": "",
                "id": "username",
            }
        )
    )
    password = forms.CharField(
        strip=False,
        widget=forms.PasswordInput(
            attrs={
                "class": "form-control",
                "placeholder": "",
                "id": "password",
            }
        ),
    )


class ProfileForm(forms.ModelForm):
    class Meta:
        model = Profile
        fields = (
            "firstName",
            "lastName",
            "middleName",
            "organisation",
            "canAddOrganisationDocument",
            "profile_pic",
        )
        widgets = {
            "firstName": forms.TextInput(attrs={"class": "form-control", "id": "firstName"}),
            "lastName": forms.TextInput(attrs={"class": "form-control", "id": "lastName"}),
            "middleName": forms.TextInput(attrs={"class": "form-control", "id": "middleName"}),
            "organisation": forms.Select(attrs={"class": "form-select", "id": "organisation"}),
            "canAddOrganisationDocument": forms.CheckboxInput(attrs={"class": "form-check-input"}),
            "profile_pic": forms.ClearableFileInput(
                attrs={"class": "form-control", "name": "profile_pic", "type": "file", "id": "inputFile"}
            ),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["organisation"].queryset = Organisation.objects.order_by("name")


class UserWithProfileCreationForm(UserCreationForm):
    email = forms.EmailField(
        label=_("Email"),
        required=True,
        widget=forms.EmailInput(attrs={"class": "form-control", "id": "email"}),
    )
    first_name = forms.CharField(
        label=_("Имя"),
        required=False,
        widget=forms.TextInput(attrs={"class": "form-control", "id": "first_name"}),
    )
    last_name = forms.CharField(
        label=_("Фамилия"),
        required=False,
        widget=forms.TextInput(attrs={"class": "form-control", "id": "last_name"}),
    )
    middle_name = forms.CharField(
        label=_("Отчество"),
        required=False,
        widget=forms.TextInput(attrs={"class": "form-control", "id": "middle_name"}),
    )
    organisation = forms.ModelChoiceField(
        label=_("Организация"),
        queryset=Organisation.objects.order_by("name"),
        required=False,
        widget=forms.Select(attrs={"class": "form-select", "id": "organisation"}),
    )
    roles = forms.MultipleChoiceField(
        label=_("Роли"),
        required=False,
        widget=forms.CheckboxSelectMultiple(),
        choices=(),
    )

    class Meta(UserCreationForm.Meta):
        model = User
        fields = ("username", "email", "first_name", "last_name")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        ensure_roles_exist()
        self.fields["username"].widget.attrs.update({"class": "form-control", "id": "username"})
        self.fields["password1"].widget.attrs.update({"class": "form-control", "id": "password1"})
        self.fields["password2"].widget.attrs.update({"class": "form-control", "id": "password2"})
        self.fields["roles"].choices = role_choices()

    def save(self, commit: bool = True) -> User:
        user = super().save(commit=False)
        user.email = self.cleaned_data["email"]
        user.first_name = self.cleaned_data.get("first_name", "")
        user.last_name = self.cleaned_data.get("last_name", "")
        if commit:
            user.save()
            ensure_roles_exist()
            selected_roles: List[str] = self.cleaned_data.get("roles", [])
            user.groups.clear()
            for role_code in selected_roles:
                group = get_role_group(role_code)
                if group:
                    user.groups.add(group)
            profile = Profile.for_user(user)
            profile.middleName = self.cleaned_data.get("middle_name", "")
            profile.firstName = self.cleaned_data.get("first_name", "")
            profile.lastName = self.cleaned_data.get("last_name", "")
            profile.organisation = self.cleaned_data.get("organisation")
            profile.save()
        return user


class UserWithProfileUpdateForm(forms.ModelForm):
    email = forms.EmailField(
        label=_("Email"),
        required=True,
        widget=forms.EmailInput(attrs={"class": "form-control", "id": "email"}),
    )
    first_name = forms.CharField(
        label=_("Имя"),
        required=False,
        widget=forms.TextInput(attrs={"class": "form-control", "id": "first_name"}),
    )
    last_name = forms.CharField(
        label=_("Фамилия"),
        required=False,
        widget=forms.TextInput(attrs={"class": "form-control", "id": "last_name"}),
    )
    middle_name = forms.CharField(
        label=_("Отчество"),
        required=False,
        widget=forms.TextInput(attrs={"class": "form-control", "id": "middle_name"}),
    )
    organisation = forms.ModelChoiceField(
        label=_("Организация"),
        queryset=Organisation.objects.order_by("name"),
        required=False,
        widget=forms.Select(attrs={"class": "form-select", "id": "organisation"}),
    )
    roles = forms.MultipleChoiceField(
        label=_("Роли"),
        required=False,
        widget=forms.CheckboxSelectMultiple(),
        choices=(),
    )
    is_active = forms.BooleanField(
        label=_("Активен"),
        required=False,
        widget=forms.CheckboxInput(attrs={"class": "form-check-input", "id": "is_active"}),
    )

    class Meta:
        model = User
        fields = ("username", "email", "first_name", "last_name", "is_active")
        widgets = {
            "username": forms.TextInput(attrs={"class": "form-control", "id": "username"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        ensure_roles_exist()
        self.fields["roles"].choices = role_choices()
        user = self.instance
        profile = Profile.for_user(user)
        self.fields["organisation"].queryset = Organisation.objects.order_by("name")
        self.fields["organisation"].initial = profile.organisation_id if profile else None
        self.fields["middle_name"].initial = profile.middleName if profile else ""
        self.fields["first_name"].initial = user.first_name
        self.fields["last_name"].initial = user.last_name
        self.fields["email"].initial = user.email
        self.fields["roles"].initial = [
            group.name[len(ROLE_GROUP_PREFIX):]
            for group in user.groups.all()
            if group.name.startswith(ROLE_GROUP_PREFIX)
        ]
        self.fields["is_active"].initial = user.is_active

    def save(self, commit: bool = True) -> User:
        user = super().save(commit=False)
        user.email = self.cleaned_data.get("email", "")
        user.first_name = self.cleaned_data.get("first_name", "")
        user.last_name = self.cleaned_data.get("last_name", "")
        user.is_active = self.cleaned_data.get("is_active", False)
        if commit:
            user.save()
            ensure_roles_exist()
            selected_roles: List[str] = self.cleaned_data.get("roles", [])
            user.groups.clear()
            for role_code in selected_roles:
                group = get_role_group(role_code)
                if group:
                    user.groups.add(group)
            profile = Profile.for_user(user)
            profile.middleName = self.cleaned_data.get("middle_name", "")
            profile.firstName = self.cleaned_data.get("first_name", "")
            profile.lastName = self.cleaned_data.get("last_name", "")
            profile.organisation = self.cleaned_data.get("organisation")
            profile.save()
        return user
