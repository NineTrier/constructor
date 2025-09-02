from django.shortcuts import render
from .models import Object, Parameter, Object_ParentObject, ObjectLink_identificators
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

# import cx_Oracle

from sqlalchemy.engine import create_engine
from sqlalchemy import inspect
from sqlalchemy import text

import pandas as pd
from django.db.models import Q

        
def upload_csv_and_get_columns(request):
    if request.method == 'POST':
        csv_file = request.FILES['csv_file']
        df = pd.read_csv(csv_file, converters={i: str for i in range(100)})
        return HttpResponse(";".join(str(x) for x in df.columns.tolist()))
    
def get_object_parameters(request, pk):
    object = get_object_or_404(Object, pk=pk)
    if request.method == 'POST':
        parameters = sorted(Parameter.objects.filter(object=object), key=lambda x: x.id)
        result = []
        for par in parameters:
            result.append({'id': par.id, 'name': par.name, 'identificator': par.identificator})
        return HttpResponse(json.dumps({'data': result}))
       
def view_data(request):
    if request.method == 'POST':
        csv_file = request.FILES['csv_file']
        df = pd.read_csv(csv_file, converters={i: str for i in range(100)})
        if request.POST['drop_column'] != '-1':
            df = df.dropna(subset=[request.POST['drop_column']])
        return HttpResponse(df.to_html())
    
def object_manager(request):
    if request.method == 'POST':
        csv_file = request.FILES['csv_file']
        df = pd.read_csv(csv_file, converters={i: str for i in range(100)})
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
            for i, row in data_obj.sort_index(axis=0, ascending=False).iterrows():
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
        'parameters': sorted(Parameter.objects.filter(object=object), key=lambda x: x.id),
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
        with open(f'{settings.MEDIA_ROOT}/{object.data}', 'rb') as f:
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
        df = pd.read_csv(csv_file, converters={i: str for i in range(100)})

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
        df['id_to_connect'] = [f"{_}_{uuid.uuid4().hex}" for _ in range(df.shape[0])]
        # сохраняем файл
        os.makedirs(os.path.dirname(file_path), exist_ok=True)
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
    with open(f'{settings.MEDIA_ROOT}/{object.data}', 'rb') as f:
        # считываем файл
        data_obj = pickle.load(f)
    if request.method == 'POST':
        changed = request.POST['changed']
        if changed == 0:
            return HttpResponse(status=304)
        object.name = request.POST['name']
        object.save()
        # узнаем, какой столбец является идентификатором
        ident = request.POST.get('ident_column')
        col_ids = request.POST.getlist('col_ids[]')
        col_names = request.POST.getlist('col[]')
        col_types = request.POST.getlist('col_type[]')
        arr_delim = request.POST.getlist('arr_delim[]')
        date_format = request.POST.getlist('date_format[]')
        # создаем параметры Parameter для каждого столбца
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
                parameter.save()
                if ident is None:
                    parameter.identificator = True
                    ident = int(parameter.id)
                    parameter.save()
                data_obj[int(parameter.id)] = pd.NA
            else:
                parameter = Parameter.objects.get(id=int(col_id))
                parameter.identificator = col_id == ident   
                parameter.name = col_names[i]
                parameter.data_type = col_types[i]
                parameter.array_separator = arr_delim[i]
                parameter.date_format = date_format[i]
                parameter.save()
        if os.path.exists(object.data.path):
            os.remove(object.data.path)
        data_obj.to_pickle(object.data.path)
        # возвращаем ответ
        return HttpResponse(status=200)
    parameters_objects = Object_ParentObject.objects.filter(parent_object=object)
    
    # отображаем форму
    return render(request, 'database_manager/update_object.html', context={
        'object': object,
        'objects': Object.objects.all(),
        'parameters': sorted(Parameter.objects.filter(object=object), key=lambda x: x.id),
        'parameters_objects': [po.object for po in parameters_objects],
    })
    
def get_unique_filtered_strings(list_of_values):
    """
    Преобразует значения в строки, удаляет пробелы по краям,
    фильтрует пустые строки, 'None' и 'nan', и возвращает отсортированные уникальные результаты.
    """
    filtered_values = []
    for val in list_of_values:
        # Преобразуем в строку и удаляем пробелы по краям
        s_val = str(val).strip()
        # Проверяем, что строка не пустая и не является 'None' или 'nan'
        if s_val and s_val not in ['None', 'nan', '<NA>']:
            filtered_values.append(s_val)
    # Получаем уникальные значения и сортируем их
    return sorted(list(set(filtered_values)))
    

    
def get_parameter_data(data_obj, parameter):
    col_id = int(parameter.id)
    raw_values_list = []
    if col_id not in data_obj.columns:
        data_obj[col_id] = pd.NA
        return None
    column_series = data_obj[col_id].dropna()
    if parameter.data_type == 'ARRAY':
        for cell_value in column_series:
            cell_value_str = str(cell_value)
            raw_values_list.extend(cell_value_str.split(parameter.array_separator))
    else:
        raw_values_list = column_series.tolist()
        
    final_unique_values = get_unique_filtered_strings(raw_values_list)
    return final_unique_values

def get_parameters_data_all(object):
    with open(f'{settings.MEDIA_ROOT}/{object.data}', 'rb') as f:
        data_obj = pickle.load(f)
    parameters_data = [] 
    for parameter in Parameter.objects.filter(object=object):
        data = get_parameter_data(data_obj, parameter)
        if data is None:
            continue
        parameters_data.append((parameter, data))
    return parameters_data

def add_element_to_object(request, pk):
    object = get_object_or_404(Object, pk=pk)
    file_path = object.data
    with open(f'{settings.MEDIA_ROOT}/{object.data}', 'rb') as f:
        data_obj = pickle.load(f)

    if request.method == 'POST':
        new_row = {"id_to_connect": f"{data_obj.shape[0]}_{uuid.uuid4().hex}"}
        col_ids = map(int, request.POST.getlist('col_id[]'))
        for col_id in col_ids:
            col_values = request.POST.getlist(f'col_value_{col_id}[]')
            parameter = Parameter.objects.get(id=col_id)
            print(col_values)
            new_row[int(col_id)] = str(col_values[0]) if len(col_values) == 1 else f"{parameter.array_separator}".join(col_values)
            print(new_row)
        data_obj = pd.concat([data_obj, pd.DataFrame([new_row])], ignore_index=True)
        data_obj.to_pickle(file_path.path)
        return HttpResponse()
    parameters_objects = Object_ParentObject.objects.filter(parent_object=object)
    return render(request, 'database_manager/add_element_to_object.html', context={
        'object': object,
        'parameters_data': sorted(get_parameters_data_all(object), key=lambda x: x[0].id), #parameters_data,
        'parameters_objects': [(po.object, True if po.id == parameters_objects[0].id else False) for po in parameters_objects],
        'parameters_objects_params_data': [(obj, get_parameters_data_all(obj.object), True if obj.id == parameters_objects[0].id else False) for obj in parameters_objects],
    })
    
def find_in_params_data(params_data: list, value: str, param: Parameter) -> list:
    result = []
    for data in params_data:
        if param.data_type == 'ARRAY':
            if data[1] in set(list(filter(lambda x: str(x).strip(),value.split(param.array_separator)))):
                result.append(data[0])
        else:
            if value.strip() == data[1]:
                result.append(data[0])
    return result


def get_indexed_unique_filtered_values(values_list, array_separator=None):
    """
    Обрабатывает список значений: преобразует в строки, удаляет пробелы,
    фильтрует пустые строки, 'None' и 'nan'. Если array_separator предоставлен,
    сначала разбивает каждое значение по разделителю.
    Возвращает отсортированный список кортежей (индекс, уникальное_значение).
    """
    processed_elements = []
    for val in values_list:
        s_val = str(val).strip()
        # Фильтруем пустые строки, 'None' и 'nan' на ранней стадии
        if s_val and s_val not in ['None', 'nan', '<NA>']:
            if array_separator:
                # Если тип ARRAY, разбиваем строку по разделителю и добавляем элементы
                # Фильтруем пустые элементы после разбиения
                processed_elements.extend([el.strip() for el in s_val.split(array_separator) if el.strip()])
            else:
                # Для других типов, просто добавляем обработанную строку
                processed_elements.append(s_val)

    # Фильтруем еще раз на случай, если после разбиения появились пустые строки или 'None'/'nan'
    final_filtered = [el for el in processed_elements if el and el not in ['None', 'nan', '<NA>']]

    # Получаем уникальные значения и сортируем их
    unique_filtered = sorted(list(set(final_filtered)))

    # Добавляем индекс к каждому уникальному значению
    indexed_values = [(i, data) for i, data in enumerate(unique_filtered)]
    return indexed_values
    
def get_parameters_data_by_ident(object: Object, param_ident_id) -> list:
    with open(f'{settings.MEDIA_ROOT}/{object.data}', 'rb') as f:
        data_obj = pickle.load(f)
    if param_ident_id is None:
        row = None
    else:
        row = data_obj[data_obj['id_to_connect'] == param_ident_id]
    parameters_data = []
    for parameter in Parameter.objects.filter(object=object):
        if row is None or row.empty:
            current_data = ""
        else:
            current_data = str(row[int(parameter.id)].iloc[0]).strip()
        current_data = current_data if current_data not in ['None', 'nan', None, '<NA>'] else ""
        param_data = data_obj[int(parameter.id)].dropna().values.tolist()
        if parameter.data_type == 'ARRAY':
            arrays_data = f"{parameter.array_separator}".join(param_data)
            unique_param_data = set(list(filter(lambda x: str(x).strip(),arrays_data.split(f"{parameter.array_separator}"))))
            arrays_data = [value for value in unique_param_data if value not in ['None', 'nan', None, '<NA>'] and value]
            arrays_data = [(i, data) for i, data in enumerate(arrays_data)]
            parameters_data.append((parameter, arrays_data, find_in_params_data(arrays_data, current_data, parameter), current_data))
        else:
            param_data = [value for value in param_data if value not in ['None', 'nan', None, '<NA>'] and value]
            param_data = [(i, data) for i, data in enumerate(param_data)]
            parameters_data.append((parameter, param_data, find_in_params_data(param_data, current_data, parameter), current_data))
    return parameters_data


def update_element_to_object(request, pk):
    object = get_object_or_404(Object, pk=pk)
    file_path = object.data
    with open(f'{settings.MEDIA_ROOT}/{object.data}', 'rb') as f:
        data_obj = pickle.load(f)
    param_ident_id = request.GET.get('id')
    print(param_ident_id)
    if request.method == 'POST':
        col_ids = map(int, request.POST.getlist('col_id[]'))
        for col_id in col_ids:
            col_values = request.POST.getlist(f'col_value_{col_id}[]')
            parameter = Parameter.objects.get(id=col_id)
            data_obj.loc[data_obj['id_to_connect'] == param_ident_id, int(col_id)] = str(col_values[0]) if len(col_values) == 1 else f"{parameter.array_separator}".join(col_values)
        data_obj.to_pickle(file_path.path)
        return HttpResponse()
    parameters_data = get_parameters_data_by_ident(object, param_ident_id)
    parameters_objects = Object_ParentObject.objects.filter(parent_object=object)
    parameters_objects_params_data = []
    for po in parameters_objects:
        parameters_link = ObjectLink_identificators.objects.filter(object_link=po, parent_object_identificator=param_ident_id).first()
        linked_param_ident_id = parameters_link.object_identificator if parameters_link else None
        parameters_objects_params_data.append((po.object, get_parameters_data_by_ident(po.object, linked_param_ident_id), True if po.id == parameters_objects[0].id else False))
    print(parameters_objects_params_data)
    return render(request, 'database_manager/update_element_to_object.html', context={
        'object': object,
        'parameters_data': sorted(parameters_data, key=lambda x: x[0].id), #parameters_data,
        'param_ident_id': param_ident_id,
        'parameters_objects': [(po.object, True if po.id == parameters_objects[0].id else False) for po in parameters_objects],
        'parameters_objects_params_data': parameters_objects_params_data,
    })
    
def delete_element_to_object(request, pk):
    if request.method == 'POST':
        object = get_object_or_404(Object, pk=pk)
        file_path = object.data
        with open(f'{settings.MEDIA_ROOT}/{object.data}', 'rb') as f:
            data_obj = pickle.load(f)
        param_ident_id = request.GET.get('id')
        print(param_ident_id)
        index = data_obj[data_obj['id_to_connect'] == param_ident_id].index
        data_obj.drop(index, inplace=True)
        data_obj.to_pickle(file_path.path)
        return HttpResponse()

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
        with open(f'{settings.MEDIA_ROOT}/{object.data}', 'rb') as f:
            # считываем файл
            data_obj = pickle.load(f)
            f.close()
            
        print(request.POST)
            
        # читаем файл
        csv_file = request.FILES['csv_file']
        df = pd.read_csv(csv_file, converters={i: str for i in range(100)})

        drop_column = request.POST.get('drop_column', '-1')
        if drop_column != '-1':
            df.dropna(subset=[drop_column], inplace=True)
        
        new_df = {}
        for parameter in sorted(Parameter.objects.filter(object=object), key=lambda x: x.id):
            col_id = int(parameter.id)
            csv_name = request.POST.get(f'csv_column_{col_id}', '')
            if not csv_name:
                new_df[col_id] = pd.NA
                continue
            if csv_name == '-1':
                new_df[col_id] = pd.NA
                continue
            new_df[col_id] = df[csv_name].map(lambda x: str(x).strip()).tolist()
              
        new_df['id_to_connect'] = [f"{_}_{uuid.uuid4().hex}" for _ in range(df.shape[0])]
        
        new_df = pd.DataFrame(new_df)
        file_path = object.data
        
        # сохраняем файл
        if os.path.exists(file_path.path):
            os.remove(file_path.path)
        new_df.to_pickle(file_path.path)
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
    
    df_object = pd.read_pickle(f'{settings.MEDIA_ROOT}/{object.data}')
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

@require_http_methods(['POST'])
def add_objects_links(request, pk):
    object = get_object_or_404(Object, pk=pk)
    child_object_idents = request.POST.getlist('child_object_idents[]')
    if not child_object_idents:
        return HttpResponseForbidden("No child object identifiers provided.")
    for child_object_ident in child_object_idents:
        try:
            child_object_id = int(child_object_ident)
        except ValueError:
            continue
        if child_object_id == object.id:
            continue
        add_objects_link(object.id, child_object_id)
    return HttpResponse('')
    
def add_objects_link(object_id, object_child_id):
    object = Object.objects.get(id=int(object_id))
    if not object_child_id:
        return False
    if object_child_id == str(object.id):
        return False
    object_child = Object.objects.get(id=int(object_child_id))
    if not object_child:
        return False
    object_to_object = Object_ParentObject()
    object_to_object.object = object_child
    object_to_object.parent_object = object
    object_to_object.save()
    return True
