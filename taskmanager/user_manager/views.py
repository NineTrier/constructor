from typing import Any, Dict, Optional

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required, permission_required
from django.contrib.auth.mixins import LoginRequiredMixin, PermissionRequiredMixin, UserPassesTestMixin
from django.contrib.auth.views import LoginView, LogoutView
from django.core.exceptions import PermissionDenied
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, render
from django.urls import reverse_lazy
from django.views.decorators.http import require_GET, require_POST
from django.views.generic import DetailView
from django.views.generic.edit import FormView, UpdateView

from .forms import ProfileForm, UserLoginForm, UserWithProfileCreationForm, UserWithProfileUpdateForm
from .models import Organisation, Profile
from .roles import ROLE_GROUP_PREFIX, get_role_definition

User = get_user_model()


class ProfileContextMixin:
    """
    Подмешивает в контекст профиль текущего пользователя.
    Это позволяет шаблонам, унаследованным от base.html, корректно
    отображать имя и аватар пользователя в шапке.
    """

    def get_context_data(self, **kwargs: Any) -> Dict[str, Any]:
        context = super().get_context_data(**kwargs)
        if self.request.user.is_authenticated:
            context["profile"] = Profile.for_user(self.request.user)
        return context


class Login(ProfileContextMixin, LoginView):
    authentication_form = UserLoginForm
    template_name = "user_manager/login.html"

    def get_success_url(self) -> str:
        redirect_to = self.get_redirect_url()
        if redirect_to:
            return redirect_to
        return reverse_lazy("home")


class Logout(LogoutView):
    next_page = reverse_lazy("home")


class UserCreateView(ProfileContextMixin, LoginRequiredMixin, PermissionRequiredMixin, FormView):
    permission_required = "auth.add_user"
    raise_exception = True
    form_class = UserWithProfileCreationForm
    template_name = "user_manager/user_create.html"
    success_url = reverse_lazy("user_manager:user_create")

    def form_valid(self, form: UserWithProfileCreationForm):
        user = form.save()
        messages.success(self.request, f"Учетная запись «{user.username}» создана.")
        return super().form_valid(form)


class ProfileDetailView(ProfileContextMixin, LoginRequiredMixin, DetailView):
    template_name = "user_manager/profile.html"
    context_object_name = "profile"

    def dispatch(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        self.target_user = get_object_or_404(User, pk=kwargs["pk"])
        if request.user != self.target_user and not request.user.has_perm("auth.view_user"):
            raise PermissionDenied
        return super().dispatch(request, *args, **kwargs)

    def get_object(self, queryset=None):
        return Profile.for_user(self.target_user)

    def get_context_data(self, **kwargs: Any) -> Dict[str, Any]:
        context = super().get_context_data(**kwargs)
        webpush_settings = getattr(settings, "WEBPUSH_SETTINGS", {})
        context["organisations"] = Organisation.objects.order_by("name")
        context["vapid_key"] = webpush_settings.get("VAPID_PUBLIC_KEY")
        return context


class ProfileUpdateView(ProfileContextMixin, LoginRequiredMixin, UserPassesTestMixin, UpdateView):
    template_name = "user_manager/change_profile.html"
    form_class = ProfileForm
    context_object_name = "profile"

    def dispatch(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        self.target_user = get_object_or_404(User, pk=kwargs["pk"])
        return super().dispatch(request, *args, **kwargs)

    def get_object(self, queryset=None):
        return Profile.for_user(self.target_user)

    def test_func(self) -> bool:
        target_user = getattr(self, "target_user", None)
        if target_user is None:
            return False
        if self.request.user == target_user:
            return True
        return self.request.user.has_perm("auth.change_user")

    def handle_no_permission(self):
        if self.request.user.is_authenticated:
            raise PermissionDenied
        return super().handle_no_permission()

    def form_valid(self, form: ProfileForm):
        messages.success(self.request, "Профиль обновлен.")
        return super().form_valid(form)

    def get_success_url(self) -> str:
        return reverse_lazy("user_manager:profile", kwargs={"pk": self.target_user.pk})


@login_required
@require_POST
def set_profile_image(request: HttpRequest) -> JsonResponse:
    profile = Profile.for_user(request.user)
    file = request.FILES.get("profile_pic")
    if not file:
        return JsonResponse({"error": "Не удалось определить файл"}, status=400)
    profile.profile_pic = file
    profile.save()
    return JsonResponse({"file": profile.profile_pic.url})


@login_required
@require_GET
def get_profile_image(request: HttpRequest) -> JsonResponse:
    user_id = request.GET.get("user")
    target_pk: Optional[int]
    if user_id:
        try:
            target_pk = int(user_id)
        except ValueError:
            return JsonResponse({"error": "Некорректный идентификатор пользователя"}, status=400)
    else:
        target_pk = request.user.pk
    user = get_object_or_404(User, pk=target_pk)
    if request.user != user and not request.user.has_perm("auth.view_user"):
        raise PermissionDenied
    profile = Profile.for_user(user)
    if profile.profile_pic:
        return JsonResponse({"file": profile.profile_pic.url})
    return JsonResponse({}, status=204)


@login_required
@require_GET
def get_profiles(request: HttpRequest) -> JsonResponse:
    profiles = [
        {"id": profile.id, "profile": str(profile)}
        for profile in Profile.objects.select_related("user").order_by("lastName", "firstName")
    ]
    return JsonResponse({"profiles": profiles})


@login_required
@require_GET
def get_departments(request: HttpRequest) -> JsonResponse:
    departments = [
        {"id": organisation.id, "department": organisation.name or ""}
        for organisation in Organisation.objects.order_by("name")
    ]
    return JsonResponse({"departments": departments})


class UserUpdateView(ProfileContextMixin, LoginRequiredMixin, PermissionRequiredMixin, FormView):
    permission_required = "auth.change_user"
    raise_exception = True
    form_class = UserWithProfileUpdateForm
    template_name = "user_manager/user_edit.html"

    def dispatch(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        self.target_user = get_object_or_404(User, pk=kwargs["pk"])
        return super().dispatch(request, *args, **kwargs)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["instance"] = self.target_user
        return kwargs

    def form_valid(self, form: UserWithProfileUpdateForm):
        user = form.save()
        messages.success(self.request, f"Учетная запись «{user.username}» обновлена.")
        return super().form_valid(form)

    def get_success_url(self) -> str:
        return reverse_lazy("user_manager:user_edit", kwargs={"pk": self.target_user.pk})


@login_required
@permission_required("auth.view_user", raise_exception=True)
def user_list_view(request: HttpRequest) -> HttpResponse:
    users_qs = User.objects.select_related("profile").order_by("username")
    users_data = []
    for user in users_qs:
        profile = Profile.for_user(user)
        role_labels = []
        for group in user.groups.all():
            if group.name.startswith(ROLE_GROUP_PREFIX):
                code = group.name[len(ROLE_GROUP_PREFIX):]
                definition = get_role_definition(code)
                role_labels.append(definition.name if definition else code)
        users_data.append({"user": user, "profile": profile, "roles": role_labels})
    context = {
        "users": users_data,
    }
    if request.user.is_authenticated:
        context["profile"] = Profile.for_user(request.user)
    return render(request, "user_manager/user_list.html", context)
