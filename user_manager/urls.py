
from . import views
from django.urls import path

urlpatterns = [
    path('login/', views.login1, name='login'),
    path('profile/<str:pk>/', views.ShowProfilePageView.as_view(), name='profile'),
    path('logout/', views.logout_view, name='logout'),
    path('change_profile/<str:pk>/', views.CreateProfilePageView.as_view(), name='change_profile'),
    path('get_profiles/', views.GetProfiles),
    path('get_departments/', views.GetDepartments),
    path('upload_image/', views.SetProfileImage),
    path('get_profile_image/', views.GetProfileImage),

]