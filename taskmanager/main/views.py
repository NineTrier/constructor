from django.shortcuts import render, redirect
from django.views.generic import CreateView
from .models import Documents
from .forms import DocumentForm
import os
from django.conf import settings
from django.http import HttpResponse, Http404

from .Formatter.Formatter import Formatter


# Класс, который помогает создавать новый записи в базу данных Documents
# и открывает страницу с добавлением новых документов
class DocumentCreate(CreateView):
    model = Documents
    form_class = DocumentForm

    extra_context = {'documents': Documents.objects.all()}

    template_name = 'main/document_create.html'

    success_url = 'documents'


# Функция, которая позволяет скачать файл, загруженный на сервер
def download(request):
    file_path = os.path.join(settings.MEDIA_ROOT, request.GET.get('file'))
    if os.path.exists(file_path):
        with open(file_path, 'rb') as fh:
            response = HttpResponse(fh.read(), content_type="application/vnd.ms-word")
            response['Content-Disposition'] = 'inline; filename=' + os.path.basename(file_path)
            return response
    raise Http404


# Функция позволяет скачать изменённый при помощи летней программы файл
def change_doc(request):
    file_path = os.path.join(settings.MEDIA_ROOT, request.GET.get('file'))
    if os.path.exists(file_path):
        frm = Formatter(file_path, path_to_save=str(settings.MEDIA_ROOT)+'/documents/Редактированные')
        frm.Redact()
        if os.path.exists(frm.path):
            with open(frm.path, 'rb') as fh:
                response = HttpResponse(fh.read(), content_type="application/vnd.ms-word")
                response['Content-Disposition'] = 'inline; filename=' + os.path.basename(frm.path)
                return response
    raise Http404


# Главная страница веб-сервиса
def index(request):
    documents = Documents.objects.order_by("name")
    context = {
        'title': 'Главная страница сайта',
        'documents': documents,
    }
    return render(request, 'main/index.html', context)


# Страница about
def about(request):
    return render(request, 'main/about.html')
