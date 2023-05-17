import json

from django.shortcuts import render, redirect
from django.views.generic import CreateView
from .models import Documents, Fonts, SavedElements, VariableBlock, DocType
from database_manager.models import VariableSQLGet, VariableSQLSet, VariableSQLGet_Variable, VariableSQLSet_Variable, VariableSQLSet_VariableSQLGet
from .forms import DocumentForm
from django.core.files.storage import FileSystemStorage
import os
from django.conf import settings
from django.http import HttpResponse, Http404, HttpResponseNotModified
from docx import Document
from .Document import Document

from django.views.decorators.csrf import csrf_exempt
from transliterate import translit
from transliterate.decorators import transliterate_function
from user_manager.models import Profile, user_directory_path


# Класс, который помогает создавать новый записи в базу данных Documents
# и открывает страницу с добавлением новых документов
class DocumentCreate(CreateView):
    model = Documents
    form_class = DocumentForm

    template_name = 'document/document_create.html'
    
    def get_context_data(self, **kwargs):
        context = super(DocumentCreate, self).get_context_data(**kwargs)
        if self.request.user.is_authenticated:
            context['profile'] = Profile.objects.filter(user=self.request.user)[0]
        context['documents'] = Documents.objects.all()
        return context
    
    def post(self, request, *args, **kwargs):
        print(request.POST)
        print(request.FILES)
        document = Documents()
        document.name = request.POST['name']
        document.description = request.POST['description']
        document.type = DocType.objects.get(id=request.POST['type'])
        document.owner = Profile.objects.filter(user=request.user)[0]
        file = request.FILES.get('file')
        fs = FileSystemStorage(location=f"{settings.MEDIA_ROOT}/documents/user_{request.user.id}/")
        fs.save(translit_russian(file.name), file)
        file_url = f"documents/user_{request.user.id}/{translit_russian(file.name)}"
        print(file_url)
        document.file = file_url
        document.save()
        print("###############", document.id)
        response = redirect(f"/document/view?id={document.id}&type=1")
        return response


def translit_russian(text):
    try:
        translited = translit(text, reversed=True)
        translited = str(translited).replace(' ', '_')
        return translited
    except:
        return text
    

def create_New_Document(request):
    if request.method == 'POST':
        print(request.POST)
        document = Documents()
        document.name = request.POST['name']
        document.description = request.POST['description']
        document.type = DocType.objects.get(id=request.POST['type'])
        document.owner = Profile.objects.filter(user=request.user)[0]
        document.file = f"documents/user_{request.user.id}/{translit_russian(document.name)}{translit_russian(document.owner)}.docx"
        file_path = os.path.join(settings.MEDIA_ROOT, str(document.file))
        file_path_blank = os.path.join(settings.MEDIA_ROOT, f"documents\\blank.docx")
        doc_file = Document(file_path_blank)
        doc_file.save(file_path)
        document.save()
        print(document.id)
        return redirect(f"/document/view?id={document.id}&type=1")
    form = DocumentForm()
    context = {
        'form': form,
    }
    if request.user.is_authenticated:
        context['profile'] = Profile.objects.filter(user=request.user)[0]
    return render(request, 'document/new_document.html', context)


# Функция, которая позволяет скачать файл, загруженный на сервер
def download(request):
    filepath = Documents.objects.filter(id=request.GET.get('id'))[0].file
    file_path = os.path.join(settings.MEDIA_ROOT, str(filepath))
    if os.path.exists(file_path):
        with open(file_path, 'rb') as fh:
            response = HttpResponse(fh.read(), content_type="application/vnd.ms-word")
            response['Content-Disposition'] = 'inline; filename=' + os.path.basename(file_path)
            return response
    raise Http404

@csrf_exempt
def DeleteSavedElement(request):
    response = redirect('/')
    request_data = request.body
    stroke = json.loads(request_data)
    if stroke['id'] == '-1':
        return response
    SavedElements.objects.filter(id=stroke['id']).delete()
    return response

@csrf_exempt
def DeleteVariable(request):
    response = HttpResponse()
    request_data = request.body
    stroke = json.loads(request_data)
    if stroke['id'] == '-1':
        return response
    VariableBlock.objects.filter(id=stroke['id']).delete()
    return response

@csrf_exempt
def SaveVariable(request):
    request_data = request.body
    stroke = json.loads(request_data)
    variable = VariableBlock.objects.filter(name=stroke['name'], doc_id=stroke['id_doc'])
    if len(variable) > 0 and stroke['id'] == '-1':
        response = HttpResponseNotModified()
        response['MessageOfError'] = 'Не удалось создать переменную. Переменная с таким именем существует.'.encode('utf-8')
        response['TypeOfError'] = 'Ошибка переменных'.encode('utf-8')
        return response
    if len(variable) == 1 and stroke['id'] != str(variable[0].id):
        response = HttpResponseNotModified()
        response['MessageOfError'] = 'Не удалось переименовать переменную. Переменная с таким именем существует.'.encode('utf-8')
        response['TypeOfError'] = 'Ошибка переменных'.encode('utf-8')
        return response
    response = HttpResponse()
    if stroke['id'] == '-1':
        variable = VariableBlock()
    else:
        variable = VariableBlock.objects.filter(id=stroke['id'])[0]
    variable.name = stroke['name']
    variable.meaning = stroke['value']
    variable.doc_id = stroke['id_doc']
    variable.save()
    response['id'] = variable.id
    return response

@csrf_exempt
def SaveSavedElement(request):
    response = HttpResponse()
    request_data = request.body
    stroke = json.loads(request_data)
    print(stroke)
    if stroke['id'] == '-1':
        saved_element = SavedElements()
    else:
        saved_element = SavedElements.objects.filter(id=stroke['id'])[0]
    saved_element.name = stroke['name']
    saved_element.json = stroke['json']
    saved_element.save()
    response['id'] = saved_element.id
    return response

@csrf_exempt
def DeleteDocument(request):
    response = redirect('/')
    request_data = request.body
    stroke = json.loads(request_data) 
    if stroke['id'] == '-1':
        return response
    Documents.objects.filter(id=stroke['id']).delete()
    return response

def ViewDocument(request):
    fileid = request.GET.get('id')
    print(request.GET.get('type'))
    Doc = Documents.objects.filter(id=fileid)[0]
    file_path = Doc.file
    print(file_path)
    file_path = os.path.join(settings.MEDIA_ROOT, str(file_path))
    if(request.GET.get('type') == '1'):
        document = Document(file_path)
        context = {
            'title': 'Просмотр документа',
            'id_doc': fileid,
            'document': [(child, child.id) for child in document.childs],
            'name_of_document': Doc.name,
            'sectPr': document.childs[-1],
            'fonts': Fonts.objects.order_by('id'),
            'saved_elements': json.dumps({f"{el.name}:{el.id}": json.dumps(el.json) for el in SavedElements.objects.all()}),
            'variable': json.dumps({var.id: f"{var.name}:{var.meaning}" for var in VariableBlock.objects.filter(doc=Doc.id)}),
            'document_json': json.dumps(Doc.json),
            'type': request.GET.get('type'),
            'sql_var_set': VariableSQLSet.objects.all(),
            'sql_var_get': VariableSQLGet.objects.all(),
            'sql_var_get_set': VariableSQLSet_VariableSQLGet.objects.all()
        }
    else:
        context = {
        'title': 'Просмотр документа',
        'id_doc': fileid,
        'name_of_document': Doc.name,
        'fonts': Fonts.objects.order_by('id'),
        'saved_elements': json.dumps({f"{el.name}:{el.id}": json.dumps(el.json) for el in SavedElements.objects.all()}),
        'variable': json.dumps({var.id: f"{var.name}:{var.meaning}" for var in VariableBlock.objects.filter(doc=Doc.id)}),
        'document_json': json.dumps(Doc.json),
        'type': request.GET.get('type'),
        'sql_var_set': VariableSQLSet.objects.all(),
        'sql_var_get': VariableSQLGet.objects.all(),
        'sql_var_get_set': VariableSQLSet_VariableSQLGet.objects.all()
    }
    
    if request.user.is_authenticated:
        context['profile'] = Profile.objects.filter(user=request.user)[0]
        
    return render(request, 'document/document_view_v2.html', context)


def SavedElementFromJSON(json_stroke):
    for list_elem in json_stroke:
        name, id_elem, element = list_elem['name'], list_elem['id'], list_elem['json']
        if id_elem == '-1':
            saved = SavedElements()
            saved.name = name
            saved.json = json.loads(element)
            saved.save()
        else:
            saved = SavedElements.objects.get(id=id_elem)
            saved.name = name
            saved.json = json.loads(element)
            saved.save()

@csrf_exempt
def UpdateDocument(request):
    fileid = request.GET.get('id')
    doc = Documents.objects.get(id=fileid)
    file_path = os.path.join(settings.MEDIA_ROOT, str(doc.file))
    document = Document()
    request_data = request.body
    stroke = json.loads(request_data)
    print(stroke)
    doc.json = stroke
    doc.name = stroke['doc_name']
    print(doc)
    if not doc.save():
        response = HttpResponseNotModified()
        response['MessageOfError'] = "Документ не сохранён. Попробуйте позже.".encode('utf-8')
        response['TypeOfError'] = "Документ не сохранён".encode('utf-8')
        return response
    document.from_json(stroke)
    document.save(file_path)
    response = HttpResponse()
    return response
