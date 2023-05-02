
from .views import ShowProfilePageView, Login, Logout, CreateProfilePageView, GetProfiles, GetDepartments, UploadUsersFromActiveDirectory, SetProfileImage, GetProfileImage
from django.urls import path

urlpatterns = [
    path('login/', Login.as_view(), name='login'),
    path('profile/<str:pk>/', ShowProfilePageView.as_view(), name='profile'),
    path('logout/', Logout.as_view(), name='logout'),
    path('change_profile/<str:pk>/', CreateProfilePageView.as_view(), name='change_profile'),
    path('get_profiles/', GetProfiles),
    path('get_departments/', GetDepartments),
    path('upload_image/', SetProfileImage),
    path('get_profile_image/', GetProfileImage),

]