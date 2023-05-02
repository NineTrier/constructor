from django.shortcuts import render
from document.models import Documents

# Главная страница веб-сервиса
def index(request):
    documents = Documents.objects.order_by('-id')
    context = {
        'title': 'Главная страница сайта',
        'documents': documents,
    }
    return render(request, 'main/index.html', context)


# Страница about
def about(request):
    return render(request, 'main/about.html')
