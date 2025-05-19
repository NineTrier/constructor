from django.shortcuts import render
from .models import Object, Parameter
from django.views.generic import CreateView
from django.contrib.auth import views, models
from django.db.models import Q
from django.core.exceptions import PermissionDenied
from user_manager.models import Profile, Organisation
from document.models import DocumentPattern_Objects
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse_lazy
from django.views.decorators.csrf import csrf_exempt
from django.http import HttpResponse, JsonResponse, StreamingHttpResponse, FileResponse, Http404, HttpResponseNotModified, HttpResponseForbidden
from django.conf import settings
from django.db.models.sql.query import Query
from django.db import connection
import pickle
import uuid
import json
import re
import os
from django.views.decorators.http import require_http_methods

import cx_Oracle

from sqlalchemy.engine import create_engine
from sqlalchemy import inspect
from sqlalchemy import text

import pandas as pd
from django.db.models import Q

        
def upload_csv_and_get_columns(request):
    if request.method == 'POST':
        csv_file = request.FILES['csv_file']
        df = pd.read_csv(csv_file)
        return HttpResponse(";".join(str(x) for x in df.columns.tolist()))
    
def get_object_parameters(request, pk):
    object = get_object_or_404(Object, pk=pk)
    if request.method == 'POST':
        parameters = Parameter.objects.filter(object=object).all()
        result = []
        for par in parameters:
            result.append({'id': par.id, 'name': par.name, 'identificator': par.identificator})
        return HttpResponse(json.dumps({'data': result}))
       
def view_data(request):
    if request.method == 'POST':
        csv_file = request.FILES['csv_file']
        df = pd.read_csv(csv_file)
        if request.POST['drop_column'] != '-1':
            df = df.dropna(subset=[request.POST['drop_column']])
        return HttpResponse(df.to_html())
    
def object_manager(request):
    if request.method == 'POST':
        csv_file = request.FILES['csv_file']
        df = pd.read_csv(csv_file)
        if request.POST['drop_column'] != '-1':
            df = df.dropna(subset=[request.POST['drop_column']])
        return HttpResponse(df.to_html())
    context = {
        'objects': Object.objects.all(),
    }
    return render(request, 'database_manager/object_manager.html', context)

def get_objects_to_connect(request):
    if request.method == 'POST':
        return HttpResponse(json.dumps({'object': [{'id': x.id, 'name': x.name} for x in Object.objects.all()]}))
      
@csrf_exempt
def create_new_object(request):
    new_object = Object()
    new_object.name = request.POST['name']
    file_id = uuid.uuid4().hex
    file_path = os.path.join(settings.MEDIA_ROOT, 'dataframes', f'{file_id}.pkl')
    new_object.data=os.path.join('dataframes', f'{file_id}.pkl')
    new_object.save()
    df = pd.DataFrame({"id_to_connect": []})
    df.to_pickle(file_path)
    return HttpResponse(json.dumps({'id': new_object.id}))
       
def get_object(request, pk):
    """
    Обработчик запроса на получение данных из объекта
    - находим объект по pk
    - открываем файл, загруженный в объект,
      преобразуем его в словарь
    - находим параметр-идентификатор,
      получаем его имя,
      берём из словаря значения этого параметра,
      преобразуем их в список
    """
    object = get_object_or_404(Object, pk=pk)
    try:
        # находим объект по pk

        # открываем файл, загруженный в объект,
        # преобразуем его в словарь
        with open(f'{settings.MEDIA_ROOT}/{object.data}', 'rb') as f:
            data_obj = pickle.load(f)

        # находим параметр-идентификатор,
        # получаем его имя,
        # берём из словаря значения этого параметра,
        # преобразуем их в список
        param_ident = Parameter.objects.filter(object=object, identificator=True).first()
        idents = []
        if param_ident != None:
            for i, row in data_obj.iterrows():
                idents.append({
                    'id': row['id_to_connect'],
                    'param_ident': row[param_ident.id],
                })
    except (Object.DoesNotExist, Parameter.DoesNotExist, KeyError, IndexError, FileNotFoundError, pickle.UnpicklingError) as e:
        # если объекта с таким pk не существует,
        # или параметра-идентификатора не существует,
        # или в файле нет параметра-идентификатора,
        # или в файле есть пустой параметр-идентификатор,
        # или файла не существует,
        # или файл повреждён,
        # то возвращаем ошибку 404
        return HttpResponse(status=404)

    context = {
        'object': object,
        'parameters': Parameter.objects.filter(object=object),
        'idents': idents,
        'documents': [doc.document for doc in DocumentPattern_Objects.objects.filter(object=object)],
    }
    if request.method == 'POST':
        return HttpResponse(json.dumps({
            'object': object.to_dict(),
            'idents': idents,
            'documents': [{doc.document.id: doc.document.name} for doc in DocumentPattern_Objects.objects.filter(object=object)], 
        }))
    return render(request, 'database_manager/get_object.html', context)

def post_data_from_object(request, pk):
    """
    Обработчик запроса на получение данных из объекта по идентификатору.
    
    - если метод POST, то загружает файл, читает его,
      находит параметр-идентификатор, ищет строку в файле,
      по этому параметру, преобразует ее в словарь,
      возвращает его в формате JSON
    """
    object = get_object_or_404(Object, pk=pk)
    if request.method == 'POST':
        # находим объект по pk
        
        # открываем файл, загруженный в объект
        with open(f'{settings.MEDIA_ROOT}\{object.data}', 'rb') as f:
            # считываем файл
            data_obj = pickle.load(f)
        
        id_to_connect = request.POST["param_ident_id"]
        
        # находим параметр-идентификатор
        param_ident = Parameter.objects.filter(object=object,identificator=True)[0]
        
        # находим идентификатор, указанный в запросе
        ident = str(request.POST[f"{param_ident.id}"]).strip()
         # находим строку в файле, по этому параметру
        data = data_obj.loc[data_obj["id_to_connect"] == id_to_connect]
        
        # если строка не найдена, возвращаем 404
        if data.empty:
            return HttpResponse(status=404)
        
        data_dict = data.to_dict(orient='records')
        
        for key, value in data_dict[0].items():
            try:
                param = Parameter.objects.get(id=int(key))
                if param.data_type == 'ARRAY':
                    data_dict[0][key] = {'data_type': param.data_type, 'value': value.split(param.array_separator)}
                elif param.data_type == 'DATE':
                    data_dict[0][key] = {'data_type': param.data_type, 'value': param.parse_date(value)}
                else:
                    data_dict[0][key] = {'data_type': param.data_type, 'value': value}
            except ValueError:
                continue
        # возвращаем словарь в формате JSON
        response = HttpResponse(json.dumps(data_dict))
        return response
       

def upload_csv(request):
    """
    Обработчик формы загрузки CSV-файла.
    
    - если метод POST, то загружает файл, читает его,
      обрабатывает, сохраняет в файл, создает объект Object,
      создает параметры Parameter для каждого столбца,
      указывает, какой столбец является идентификатором
    - если метод GET, то отображает форму загрузки CSV-файла
    """
    
    if request.method == 'POST':
        # читаем файл
        csv_file = request.FILES['csv_file']
        df = pd.read_csv(csv_file)

        # если выбран столбец для удаления, то удаляем
        drop_column = request.POST.get('drop_column', '-1')
        if drop_column != '-1':
            df.dropna(subset=[drop_column], inplace=True)

        # приводим все значения к строке и убираем лишние пробелы
        df = df.map(lambda x: str(x).strip())
        
        # создаем уникальное имя файла
        file_id = uuid.uuid4().hex
        file_path = os.path.join(settings.MEDIA_ROOT, 'dataframes', f'{file_id}.pkl')

        # создаем объект Object
        object = Object(name=request.POST['name'], data=os.path.join('dataframes', f'{file_id}.pkl'))
        object.save()

        # узнаем, какой столбец является идентификатором
        ident = request.POST.get('ident_column', df.columns[0])
        
        col_names = request.POST.getlist('col[]')
        col_types = request.POST.getlist('col_type[]')
        arr_delim = request.POST.getlist('arr_delim[]')
        date_format = request.POST.getlist('date_format[]')
        
        print(col_names)
        print(df.columns)
        
        # создаем параметры Parameter для каждого столбца
        parameters = [
            Parameter(
                object=object,
                name=col_names[i],
                data_type=col_types[i],
                array_separator=arr_delim[i],
                identificator=col == ident,
                date_format=date_format[i]
            )
            for i, col in enumerate(df.columns)
        ]
        # создаем параметры
        params = Parameter.objects.bulk_create(parameters)  
        df.columns = [param.id for param in params]
        df['id_to_connect'] = [uuid.uuid4().hex for _ in range(df.shape[0])]
        # сохраняем файл
        df.to_pickle(file_path)
        # возвращаем ответ
        return HttpResponse(f'/database/get_object/{object.id}')

    # отображаем форму
    return render(request, 'database_manager/upload_csv.html')

@csrf_exempt
def delete_param(request, pk):
    param = get_object_or_404(Parameter, pk=pk)
    param.delete()
    return HttpResponse(status=200)

def update_object(request, pk):
    object = get_object_or_404(Object, pk=pk)
    with open(f'{settings.MEDIA_ROOT}\{object.data}', 'rb') as f:
        # считываем файл
        data_obj = pickle.load(f)
    if request.method == 'POST':
        changed = request.POST['changed']
        if changed == 0:
            return HttpResponse(status=304)
        object.name = request.POST['name']
        # узнаем, какой столбец является идентификатором
        ident = request.POST.get('ident_column')
        col_ids = request.POST.getlist('col_ids[]')
        col_names = request.POST.getlist('col[]')
        col_types = request.POST.getlist('col_type[]')
        arr_delim = request.POST.getlist('arr_delim[]')
        date_format = request.POST.getlist('date_format[]')
        # создаем параметры Parameter для каждого столбца
        parameters = []
        for i, col_id in enumerate(col_ids):
            if col_id == '-1':
                parameter = Parameter(
                    object=object,
                    name=col_names[i],
                    data_type=col_types[i],
                    array_separator=arr_delim[i],
                    identificator=col_id == ident,
                    date_format=date_format[i]
                )
                parameters.append(parameter)
                data_obj[parameter.id] = []
            else:
                parameter = Parameter.objects.get(id=int(col_id))
                parameter.identificator = col_id == ident
                parameter.name = col_names[i]
                parameter.data_type = col_types[i]
                parameter.array_separator = arr_delim[i]
                parameter.date_format = date_format[i]
                parameters.append(parameter)
        Parameter.objects.bulk_update(parameters, [
            'name', 'data_type', 'array_separator', 'identificator', 'date_format'
        ])
        if os.path.exists(object.data.path):
            os.remove(object.data.path)
        data_obj.to_pickle(object.data.path)
        # возвращаем ответ
        return HttpResponse(status=200)
    # отображаем форму
    return render(request, 'database_manager/update_object.html', context={
        'object': object,
        'parameters': Parameter.objects.filter(object=object),
    })
    
def add_element_to_object(request, pk):
    object = get_object_or_404(Object, pk=pk)
    file_path = object.data
    with open(f'{settings.MEDIA_ROOT}\{object.data}', 'rb') as f:
        data_obj = pickle.load(f)

    if request.method == 'POST':
        new_row = {"id_to_connect": uuid.uuid4().hex}
        col_ids = map(int, request.POST.getlist('col_id[]'))
        col_values = request.POST.getlist('col_value[]')
        new_row.update(zip(col_ids, col_values))
        data_obj = data_obj.append(new_row, ignore_index=True)
        data_obj.to_pickle(file_path.path, compression='infer')
        return HttpResponse()

    parameters_data = [
        (parameter, [value for value in data_obj[parameter.id].unique() if value not in ['None', 'nan', None] and value])
        for parameter in Parameter.objects.filter(object=object)
    ]
    return render(request, 'database_manager/add_element_to_object.html', context={
        'object': object,
        'parameters_data': parameters_data,
    })

def update_csv(request, pk):
    """
    Обработчик формы загрузки CSV-файла.
    
    - если метод POST, то загружает файл, читает его,
      обрабатывает, сохраняет в файл, создает объект Object,
      создает параметры Parameter для каждого столбца,
      указывает, какой столбец является идентификатором
    - если метод GET, то отображает форму загрузки CSV-файла
    """
    object = get_object_or_404(Object, pk=pk)
    if request.method == 'POST':
        
        # открываем файл, загруженный в объект
        with open(f'{settings.MEDIA_ROOT}\{object.data}', 'rb') as f:
            # считываем файл
            data_obj = pickle.load(f)
            f.close()
            
        # читаем файл
        csv_file = request.FILES['csv_file']
        df = pd.read_csv(csv_file)
        
        if len(data_obj.columns.tolist()) != len(df.columns.tolist()):
            return HttpResponseNotModified("Количество столбцов разное. Для загрузки данных из этого CSV создайте новый объект.")
        
        # если выбран столбец для удаления, то удаляем
        drop_column = request.POST.get('drop_column', '-1')
        if drop_column != '-1':
            df.dropna(subset=[int(drop_column)], inplace=True)

        # приводим все значения к строке и убираем лишние пробелы
        df = df.map(lambda x: str(x).strip())
        df['id_to_connect'] = [uuid.uuid4().hex for _ in range(df.shape[0])]
        file_path = object.data
        
        df.columns = data_obj.columns.tolist()
        # сохраняем файл
        if os.path.exists(file_path.path):
            os.remove(file_path.path)
        df.to_pickle(file_path.path)
        # возвращаем ответ
        return HttpResponse(f'/database/get_object/{object.id}')

    # отображаем форму
    return render(request, 'database_manager/upload_csv.html')

def DeleteObject(request, pk):
    """Функция для обработки запроса по удалению переменной SQL-параметра"""
    try:
        object = Object.objects.filter(id=int(pk))[0]
        DocumentPattern_Objects.objects.filter(object=object).delete()
        Parameter.objects.filter(object=object).delete()
        object.delete()
        return HttpResponse()
    except Exception as exc:
        print(exc)
        response = HttpResponseNotModified()
        return response 

@require_http_methods(['POST'])
def generate_excel_file(request, pk):
    object = get_object_or_404(Object, pk=pk)
    ident = Parameter.objects.get(object=object, identificator=True)
    documents = DocumentPattern_Objects.objects.filter(object=object)
    doc_list = [f"{doc.document.name}**{doc.document.id}**" for doc in documents]
    
    df_object = pd.read_pickle(f'{settings.MEDIA_ROOT}\{object.data}')
    ident_list = [f"{row[ident.id]}**{row['id_to_connect']}" for index, row in df_object.iterrows()]
    dict_to_df = {f'{ident.id}**{ident.object.id}**': ident_list}
    df = pd.DataFrame({**dict_to_df, **{doc: ['-'] * len(ident_list) for doc in doc_list}})
    
    temp_dir = os.path.join(settings.MEDIA_ROOT, 'generated_files')
    if not os.path.exists(temp_dir):
        os.makedirs(temp_dir)
    
    file_path = os.path.join(temp_dir, 'file_changer.xlsx')
    df.to_excel(file_path, index=False)
    if os.path.exists(file_path):
        return FileResponse(open(file_path, 'rb'), content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    raise Http404
    
