from django.urls import path

from . import views

app_name = "user_manager"

urlpatterns = [
    path("login/", views.Login.as_view(), name="login"),
    path("logout/", views.Logout.as_view(), name="logout"),
    path("users/create/", views.UserCreateView.as_view(), name="user_create"),
    path("users/", views.user_list_view, name="user_list"),
    path("profile/<int:pk>/", views.ProfileDetailView.as_view(), name="profile"),
    path("profile/<int:pk>/edit/", views.ProfileUpdateView.as_view(), name="change_profile"),
    path("users/<int:pk>/edit/", views.UserUpdateView.as_view(), name="user_edit"),
    path("get_profiles/", views.get_profiles, name="get_profiles"),
    path("get_departments/", views.get_departments, name="get_departments"),
    path("upload_image/", views.set_profile_image, name="set_profile_image"),
    path("get_profile_image/", views.get_profile_image, name="get_profile_image"),
]
