from django.shortcuts import render
from document.models import Documents
from user_manager.models import Profile

# Главная страница веб-сервиса
def index(request):
    documents = Documents.objects.order_by('-id')
    context = {
        'title': 'Главная страница сайта',
        'documents': documents,
    }
    if request.user.is_authenticated:
        context['profile'] = Profile.objects.filter(user=request.user)[0]
    return render(request, 'main/index.html', context)


# Страница about
def about(request):
    return render(request, 'main/about.html')
