import json

from django.shortcuts import render, redirect
from django.views.generic import CreateView
from .models import Documents, Fonts
from .forms import DocumentForm
import os
from django.conf import settings
from django.http import HttpResponse, Http404
from docx import Document
from .Document import Document

from .Formatter.Formatter import Formatter
from django.views.decorators.csrf import csrf_exempt


# Класс, который помогает создавать новый записи в базу данных Documents
# и открывает страницу с добавлением новых документов
class DocumentCreate(CreateView):
    model = Documents
    form_class = DocumentForm

    extra_context = {'documents': Documents.objects.all()}

    template_name = 'main/document_create.html'

    success_url = 'documents'


def uploadRed(request):
    filepath = request.GET.get('filename')
    filepath = os.path.normpath(filepath)
    print(filepath)
    name = "".join(filepath.split('\\')[-1].split('.')[:-1])
    print(name)
    file_path = os.path.join(settings.MEDIA_ROOT, str(filepath.split('\\')[-1]))
    doc = Documents(name=name, owner="Александр", description="Описание", file=file_path)
    doc.save()
    docToSave = Document(filepath)
    docToSave.save(file_path)
    file_path = os.path.join(settings.MEDIA_ROOT, str(filepath))
    if os.path.exists(file_path):
        frm = Formatter(file_path, path_to_save=str(settings.MEDIA_ROOT) + '/documents/Редактированные')
        frm.Redact()
        if os.path.exists(frm.path):
            with open(frm.path, 'rb') as fh:
                response = HttpResponse(fh.read(), content_type="application/vnd.ms-word")
                response['Content-Disposition'] = 'inline; filename=' + os.path.basename(frm.path)
                return response
    raise Http404


# Функция, которая позволяет скачать файл, загруженный на сервер
def download(request):
    filepath = Documents.objects.filter(name=request.GET.get('filename'))[0].file
    file_path = os.path.join(settings.MEDIA_ROOT, str(filepath))
    if os.path.exists(file_path):
        with open(file_path, 'rb') as fh:
            response = HttpResponse(fh.read(), content_type="application/vnd.ms-word")
            response['Content-Disposition'] = 'inline; filename=' + os.path.basename(file_path)
            return response
    raise Http404


# Функция позволяет скачать изменённый при помощи летней программы файл
def change_doc(request):
    filepath = Documents.objects.filter(name=request.GET.get('filename'))[0].file
    file_path = os.path.join(settings.MEDIA_ROOT, str(filepath))
    if os.path.exists(file_path):
        frm = Formatter(file_path, path_to_save=str(settings.MEDIA_ROOT)+'/documents/Редактированные')
        frm.Redact()
        if os.path.exists(frm.path):
            with open(frm.path, 'rb') as fh:
                response = HttpResponse(fh.read(), content_type="application/vnd.ms-word")
                response['Content-Disposition'] = 'inline; filename=' + os.path.basename(frm.path)
                return response
    raise Http404


def ViewDocument(request):
    fileid = request.GET.get('id')
    file_path = Documents.objects.filter(id=fileid)[0].file
    print(file_path)
    file_path = os.path.join(settings.MEDIA_ROOT, str(file_path))
    document = Document(file_path)
    context = {
        'title': 'Просмотр документа',
        'id_doc': fileid,
        'document': [(child, child.id) for child in document.childs],
        'sectPr': document.childs[-1],
        'fonts': Fonts.objects.order_by('id')
    }
    return render(request, 'main/document_view.html', context)


@csrf_exempt
def UpdateDocument(request):
    fileid = request.GET.get('id')
    doc = Documents.objects.filter(id=fileid)[0]
    file_path = os.path.join(settings.MEDIA_ROOT, str(doc.file))
    document = Document(file_path)
    request_data = request.body
    stroke = json.loads(request_data)
    document.from_json(stroke)
    document.save(file_path)
    response = redirect('/')
    return response


# Главная страница веб-сервиса
def index(request):
    documents = Documents.objects.order_by("id")
    context = {
        'title': 'Главная страница сайта',
        'documents': documents,
    }
    return render(request, 'main/index.html', context)


# Страница about
def about(request):
    return render(request, 'main/about.html')
