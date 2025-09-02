from django.shortcuts import render
from django.urls import reverse, reverse_lazy

from .models import Profile, Organisation

import json
from django.views.generic.list import ListView
from django.views.generic.edit import CreateView
from django.shortcuts import get_object_or_404, redirect
from django.contrib.auth import views, models, authenticate, login, logout
from django.contrib.auth.forms import UserCreationForm, AuthenticationForm
from django.contrib.auth.models import User
from .forms import UserLoginForm, ProfileForm
from django.views.decorators.csrf import csrf_exempt
from django.http import HttpResponse, Http404, HttpResponseNotModified
from django.core.files.storage import FileSystemStorage
from django.contrib import messages
import csv
import codecs
from django.conf import settings
import os


def logout_view(request):
    logout(request)
    
    return redirect('/')


class Login(views.LoginView):
    authentication_form = UserLoginForm

    template_name = 'user_manager/login.html'

    def get_success_url(self):
        user = models.User.objects.filter(username=self.get_form_kwargs()['data']['username'])[0]
        return '/'
    
@csrf_exempt
def login1(request):
    try:
        print(request.META['REMOTE_ADDR'])
        if request.method == 'POST':
            print(request.POST)
            print(request.META['HTTP_HOST'])
            print(request.META['REMOTE_ADDR'])
            form = AuthenticationForm(request.POST)
            username = request.POST['username']
            password = request.POST['password']
            user = authenticate(username=username,password=password)
            print(user)
            profile_of_user = Profile.objects.filter(user=user)
            if not profile_of_user:
                profile_of_user = Profile()
                profile_of_user.user = user
                profile_of_user.firstName = "Новый пользователь"
                profile_of_user.lastName = ""
                profile_of_user.middleName = ""
                profile_of_user.organisation = Organisation.objects.get(id=1)
                profile_of_user.save()
            if user:
                if user.is_active:
                    login(request,user)
                    print('Пользователь залогинен')
                    return redirect('/')
            else:
                print('Неверный логин или пароль')
                messages.error(request,'username or password not correct')
                return redirect(reverse('login'))
        else:
            form = AuthenticationForm()
        return render(request,'user_manager/login.html',{'form':form})
    except Exception as exc:
        print(exc)
        return


class SignUpView(CreateView):
    form_class = UserCreationForm
    success_url = reverse_lazy('login')
    template_name = 'signup.html'

class Logout(views.LogoutView):

    def get_success_url(self):
        return '/'


class ShowProfilePageView(ListView):
    model = Profile
    template_name = 'user_manager/profile.html'
    

    def get_context_data(self, *args, **kwargs):
        context = super(ShowProfilePageView, self).get_context_data(*args, **kwargs)
        user = models.User.objects.get(id=self.kwargs['pk'])
        webpush_settings = getattr(settings, 'WEBPUSH_SETTINGS', {})
        vapid_key = webpush_settings.get('VAPID_PUBLIC_KEY')
        profile_of_user = Profile.objects.filter(user=user)
        context['profile'] = profile_of_user[0]
        context['organisation'] = Organisation.objects.all()
        context['vapid_key'] = vapid_key
        return context

class CreateProfilePageView(CreateView):
    model = Profile

    form_class = ProfileForm
    template_name = 'user_manager/change_profile.html'

    def get_context_data(self, *args, **kwargs):
        context = super(CreateProfilePageView, self).get_context_data(*args, **kwargs)
        user = models.User.objects.get(id=self.kwargs['pk'])
        profile_of_user = Profile.objects.filter(user=user)
        context['profile'] = profile_of_user[0]
        return context

    def post(self, request, *args, **kwargs):
        print(request.POST)
        user = models.User.objects.get(id=request.user.id)
        profile = Profile.objects.filter(user=user)[0]
        profile.firstName = request.POST['firstName']
        profile.lastName = request.POST['lastName']
        profile.middleName = request.POST['middleName']
        if 'profile_pic' in request.FILES:
            file = request.FILES['profile_pic']
            fs = FileSystemStorage()
            filename = fs.save(file.name, file)
            file_url = fs.url(filename)
            profile.profile_pic = file_url
        profile.organisation = Organisation.objects.get(id=int(request.POST['organisation']))
        profile.save()
        response = redirect(f'/accounts/profile/{user.id}')
        return response

    success_url = reverse_lazy('/')


@csrf_exempt
def SetProfileImage(request):
    try:
        profile = Profile.objects.get(id=request.POST.get('profile'))
        print(profile)
        file = request.FILES.get('profile_pic')
        print(file)
        fs = FileSystemStorage()
        filename = fs.save(file.name, file)
        print(filename)
        file_url = fs.url(filename)
        print(file_url)
        profile.profile_pic = file_url
        profile.save()
        response = HttpResponse()
        response['file'] = profile.profile_pic
        return response
    except Exception as exc:
        print(exc)
        response = HttpResponseNotModified()
        return response
    

@csrf_exempt
def GetProfileImage(request):
    try:
        request_data = request.body
        stroke = json.loads(request_data)
        print(stroke)
        user = models.User.objects.get(id=stroke['user'])
        profile = Profile.objects.filter(user=user)[0]
        if str(profile.profile_pic) == "":
            response = HttpResponseNotModified()
            return response
        response = HttpResponse()
        response['file'] = profile.profile_pic
        return response
    except Exception as exc:
        print(exc)
        response = HttpResponseNotModified()
        return response
    

@csrf_exempt
def GetProfiles(request):
    try:
        json_stroke = {}
        profiles = []
        for profile in Profile.objects.all():
            profiles.append({'id': profile.id, 'profile': profile.__str__()})
        json_stroke['profiles'] = profiles
        response = HttpResponse()
        response['profiles'] = json.dumps(json_stroke)
        return response
    except Exception as exc:
        print(exc)
        response = HttpResponseNotModified()
        return response


@csrf_exempt
def GetDepartments(request):
    try:
        json_stroke = {}
        departments = []
        for department in Organisation.objects.all():
            departments.append({'id': department.id, 'department': department.__str__()})
        json_stroke['departments'] = departments
        response = HttpResponse()
        response['departments'] = json.dumps(json_stroke)
        return response
    except Exception as exc:
        print(exc)
        response = HttpResponseNotModified()
        return response


@csrf_exempt
def UploadUsersFromActiveDirectory(request):
    try:
        request_data = request.body
        stroke = json.loads(request_data)
        print(stroke)
        fs = FileSystemStorage()
        csvReader = csv.reader(codecs.open(fs.location+'/'+stroke['path'].split('/')[-1], 'rU', 'utf-16'))
        for row in csvReader:
            print(row)
        response = HttpResponse()
        return response
    except Exception as exc:
        print(exc)
        response = HttpResponseNotModified()
        return response
