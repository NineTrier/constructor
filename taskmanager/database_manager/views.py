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
from django.http import HttpResponse, JsonResponse, Http404, HttpResponseNotModified, HttpResponseForbidden
from django.conf import settings
from django.db.models.sql.query import Query
from django.db import connection
import pickle
import uuid
import json
import re
import os
import cx_Oracle

from sqlalchemy.engine import create_engine
from sqlalchemy import inspect
from sqlalchemy import text

import pandas as pd


# cx_Oracle.init_oracle_client(lib_dir= r"C:\instantclient_23_6")

# Класс для создания подключения к базе данных
# class CreateConnection(CreateView):
#     model = Connection

#     form_class = ConnectionForm
#     template_name = 'database_manager/create_connection.html'

#     def get_context_data(self, *args, **kwargs):
#         context = super(CreateConnection, self).get_context_data(*args, **kwargs)
#         if self.request.user.is_authenticated:
#             context['profile'] = Profile.objects.filter(user=self.request.user)[0]
#         return context

#     def post(self, request, *args, **kwargs):
#         connection = Connection()
#         connection.name = request.POST['name']
#         connection.dialect = Dialect.objects.get(id=request.POST['dialect'])
#         connection.username = request.POST['username']
#         connection.password = request.POST['password']
#         connection.host = request.POST['host']
#         connection.port = request.POST['port']
#         connection.service = request.POST['service']
#         connection.save()
#         get_all_table(conn_id=connection.id)
#         response = redirect(f'/database/setting_database/')
#         return response

#     success_url = reverse_lazy('/')

# # Класс для создания переменной SQL-запроса
# class CreateSQLVariableGet(CreateView):
#     model = VariableSQLGet

#     form_class = SQLVariableFormGet
#     template_name = 'database_manager/create_sql_variable_get.html'

#     def get_context_data(self, *args, **kwargs):
#         context = super(CreateSQLVariableGet, self).get_context_data(*args, **kwargs)
#         context['sql_variables_set'] = VariableSQLSet.objects.all()
#         context['conn'] = Connection.objects.get(id=self.kwargs['pk'])
#         if self.request.user.is_authenticated:
#             context['profile'] = Profile.objects.filter(user=self.request.user)[0]
#         return context

#     def post(self, request, *args, **kwargs):
#         sqlVariable = VariableSQLGet()
#         sqlVariable.name = request.POST['name']
#         sqlVariable.sql = request.POST['sql']
#         sqlVariable.connection = Connection.objects.get(id=request.POST['connection'])
#         sqlVariable.save()
#         response = redirect(f'/database/setting_database/')
#         return response

# # Класс для создания переменной SQL-параметра
# class CreateSQLVariableSet(CreateView):
#     model = VariableSQLSet

#     form_class = SQLVariableFormSet
#     template_name = 'database_manager/create_sql_variable_set.html'

#     def get_context_data(self, *args, **kwargs):
#         context = super(CreateSQLVariableSet, self).get_context_data(*args, **kwargs)
#         context['conn'] = Connection.objects.get(id=self.kwargs['pk'])
#         if self.request.user.is_authenticated:
#             context['profile'] = Profile.objects.filter(user=self.request.user)[0]
#         return context

#     def post(self, request, *args, **kwargs):
#         sqlVariable = VariableSQLSet()
#         sqlVariable.name = request.POST['name']
#         sqlVariable.sql = request.POST['sql']
#         sqlVariable.connection = Connection.objects.get(id=request.POST['connection'])
#         sqlVariable.save()
#         response = redirect(f'/database/setting_database/')
#         return response

# @csrf_exempt
# def SaveSqlVariableGet(request):
#     """Функция для обработки запроса по сохранению переменной SQL-Запроса"""
#     request_data = request.body
#     stroke = json.loads(request_data)
#     sqlVariableGet = VariableSQLGet()
#     sqlVariableGet.name = stroke['name']
#     sqlVariableGet.sql = stroke['sql']
#     sqlVariableGet.connection = Connection.objects.get(id=stroke['connection'])
#     sqlVariableGet.save()
#     for regexp in re.findall(r'{: \d{1,5} :}', stroke['sql']):
#         varSet_varGet = VariableSQLSet_VariableSQLGet()
#         varSet_varGet.variableGet = sqlVariableGet
#         varSet_varGet.variableSet = VariableSQLSet.objects.get(id=re.search(r'\d{1,5}', regexp)[0])
#         varSet_varGet.save()

#     return HttpResponse()

# @csrf_exempt
# def UpdateSQLVariableSet(request):
#     """Функция для обработки запроса по сохранению переменной SQL-параметра"""
#     try:
#         response = HttpResponse()
#         request_data = request.body
#         stroke = json.loads(request_data)
#         var = VariableSQLSet.objects.get(id=stroke['id'])
#         var.name = stroke['name']
#         var.sql = stroke['sql']
#         var.save()
#         return response
#     except Exception as exc:
#         print(exc)
#         response = HttpResponseNotModified()
#         return response


# @csrf_exempt
# def UpdateSQLVariableGet(request):
#     """Функция для обработки запроса по обновлению переменной SQL-Запроса"""
#     try:
#         response = HttpResponse()
#         request_data = request.body
#         stroke = json.loads(request_data)
#         var = VariableSQLGet.objects.get(id=stroke['id'])
#         var.name = stroke['name']
#         var.sql = stroke['sql']
#         var.save()
#         for get_set in VariableSQLSet_VariableSQLGet.objects.filter(variableGet=var):
#             get_set.delete()
#         for regexp in re.findall(r'{: \d{1,5} :}', stroke['sql']):
#             print(VariableSQLSet.objects.get(id=re.search(r'\d{1,5}', regexp)[0]))
#             varSet_varGet = VariableSQLSet_VariableSQLGet()
#             varSet_varGet.variableGet = var
#             varSet_varGet.variableSet = VariableSQLSet.objects.get(id=re.search(r'\d{1,5}', regexp)[0])
#             varSet_varGet.save()
#         return response
#     except Exception as exc:
#         print(exc)
#         response = HttpResponseNotModified()
#         return response

# @csrf_exempt
# def DeleteSQLVariableGet(request):
#     """Функция для обработки запроса по удалению переменной SQL-Запроса"""
#     try:
#         response = HttpResponse()
#         request_data = request.body
#         stroke = json.loads(request_data)
#         VariableSQLGet.objects.get(id=stroke['id']).delete()
#         return response
#     except Exception as exc:
#         print(exc)
#         response = HttpResponseNotModified()
#         return response
    
# @csrf_exempt
# def DeleteSQLVariableSet(request):
#     """Функция для обработки запроса по удалению переменной SQL-параметра"""
#     try:
#         response = HttpResponse()
#         request_data = request.body
#         stroke = json.loads(request_data)
#         VariableSQLSet.objects.get(id=stroke['id']).delete()
#         return response
#     except Exception as exc:
#         print(exc)
#         response = HttpResponseNotModified()
#         return response


# def setting_database(request):
#     profileUser = Profile.objects.filter(user=request.user)[0]
#     context = {
#         'profile': profileUser,
#         'connections': [con.connection for con in Connection_Organisation.objects.filter(organisation=profileUser.organisation)],
#         'sql_variables_get': VariableSQLGet.objects.all(),
#         'sql_variables_set': VariableSQLSet.objects.all(),
#         'set_get': VariableSQLSet_VariableSQLGet.objects.all(),
#     }
#     return render(request, 'database_manager/database_manager.html', context)


# @csrf_exempt
# def TestConnection(request):
#     """Функция для обработки запроса по тестированию подключения к базе данных"""
#     try:
#         print(request.body)
#         request_data = request.body
#         stroke = json.loads(request_data)
        
#         conn = Connection.objects.get(id=stroke['con_id'])
#         # if conn.dialect.id == 1:
#         #     ENGINE_PATH_WIN_AUTH = conn.dialect.name + '+' + conn.dialect.driver + '://' + conn.username + ':' + conn.password +'@' + conn.host + ':' + conn.port + '/?service_name=' + conn.service
#         # else:
#         #     ENGINE_PATH_WIN_AUTH = conn.dialect.name + '+' + conn.dialect.driver + '://' + conn.username + ':' + conn.password +'@' + conn.host + ':' + conn.port + '/' + conn.service
#         ENGINE_PATH_WIN_AUTH = conn.dialect.name + '://' + conn.username + ':' + conn.password +'@' + conn.host + ':' + conn.port + '/' + conn.service
#         print(ENGINE_PATH_WIN_AUTH)
#         engine = create_engine(ENGINE_PATH_WIN_AUTH, pool_size=50, pool_pre_ping=True)
#         connect = engine.connect()
#         # connect.execute(text("SET lc_time_names = 'ru_RU'"))
#         try:
#             response = HttpResponse()
#             sql = text("SELECT 1")
#             result = connect.execute(sql)
#             connect.close()
#             response['connection'] = json.dumps({'connection':'1'})
#             return response
#         except Exception as exc:
#             response = HttpResponseNotModified()
#             print(exc)
#             connect.close()
#             response['connection'] = json.dumps({'connection':'0'})
#             return response
#     except Exception as exc:
#         print(exc)
#         response = HttpResponseNotModified()
#         response['connection'] = json.dumps({'connection':'0'})
#         return response
    
# @csrf_exempt
# def TestGetFromDB(request):
#     """Функция для получения данных из базы данных"""
#     try:
#         request_data = request.body
#         stroke = json.loads(request_data)
#         # profileUser = Profile.objects.filter(user=request.user)[0]
#         conn = Connection.objects.get(id=stroke['con_id'])
#         # print(Connection_Organisation.objects.filter(Q(connection=conn) & Q(organisation=profileUser.organisation)))
#         # if len(Connection_Organisation.objects.filter(Q(connection=conn) & Q(organisation=profileUser.organisation))) == 0:
#         #     return HttpResponseForbidden()
#         print(stroke)
#         ENGINE_PATH_WIN_AUTH = conn.dialect.name + '://' + conn.username + ':' + conn.password +'@' + conn.host + ':' + conn.port + '/' + conn.service
#         engine = create_engine(ENGINE_PATH_WIN_AUTH, pool_size=50, pool_pre_ping=True)
#         connect = engine.connect()
#         if conn.id == 22:
#             connect.execute(text("SET lc_time_names = 'ru_RU'"))
#         res = {}
#         for varGet in stroke['variables']:
#             sqlGetVar = VariableSQLGet.objects.get(id=varGet['get_id'])    
#             sql = sqlGetVar.sql
#             for varSet in varGet['set_ids']:
#                 sql = sql.replace('{: '+ varSet['id'] +' :}', f"{VariableSQLSet.objects.get(id=varSet['id']).sql}='{varSet['value']}'")
#             print(sql)
#             sql = text(sql)
#             result = connect.execute(sql)
#             for row in result:
#                 print(row[0])
#                 res[varGet['get_id']] = row[0]
#                 #res += f"{varGet['get_id']}:{row[0]};"
#                 break
#         #response['result'] = res.encode('utf-8')
#         print(res)
#         connect.close()
#         return HttpResponse(json.dumps(res, default=str))
#     except Exception as exc:
#         print(exc)
#         response = HttpResponseNotModified()
#         return response

# @csrf_exempt 
# def GetFromDBByTable(request):
#     request_data = request.body
#     stroke = json.loads(request_data)
#     if('where' in stroke):
#         return GetFromDBByTableWithWhere(stroke)
#     else:
#         return GetFromDBByTableWithoutWhere(stroke)


# def GetFromDBByTableWithoutWhere(stroke):
#     """Функция для получения данных из базы данных"""
#     try:
#         conn = Connection.objects.get(id=stroke['conn_id'])

#         ENGINE_PATH_WIN_AUTH = conn.dialect.name + '://' + conn.username + ':' + conn.password +'@' + conn.host + ':' + conn.port + '/' + conn.service
#         engine = create_engine(ENGINE_PATH_WIN_AUTH, pool_size=50, pool_pre_ping=True)
#         connect = engine.connect()
#         if conn.id == 22:
#             connect.execute(text("SET lc_time_names = 'ru_RU'"))
#         tables = json.loads(conn.tables)
#         colnames = []
#         for table in stroke['tables']:
#             for col in tables[table]['columns']:
#                 colnames.append(col['name'])
#         res = []
#         response = HttpResponse()
#         result = connect.execute(text(f"SELECT * FROM {stroke['tableSQL']} LIMIT 10"))
#         for row in result:
#             oneRow = {}
#             for i, col in enumerate(row):
#                 oneRow[colnames[i]] = str(col)
#             res.append(oneRow)
#         response['result'] = json.dumps({'rows': res})
#         connect.close()
#         return response
#     except Exception as exc:
#         print(exc)
#         response = HttpResponseNotModified()
#         return response

# def GetFromDBByTableWithWhere(stroke):
#     """Функция для получения данных из базы данных"""
#     try:
#         conn = Connection.objects.get(id=stroke['conn_id'])

#         ENGINE_PATH_WIN_AUTH = conn.dialect.name + '://' + conn.username + ':' + conn.password +'@' + conn.host + ':' + conn.port + '/' + conn.service
#         engine = create_engine(ENGINE_PATH_WIN_AUTH, pool_size=50, pool_pre_ping=True)
#         connect = engine.connect()
#         if conn.id == 22:
#             connect.execute(text("SET lc_time_names = 'ru_RU'"))
#         tables = json.loads(conn.tables)
#         colnames = []
#         for table in stroke['tables']:
#             for col in tables[table]['columns']:
#                 colnames.append(col['name'])
#         res = []
#         response = HttpResponse()
#         sqlRequest = f"SELECT * FROM {stroke['tableSQL']} WHERE {stroke['where']} LIMIT 10"
#         for key, value in stroke['sqlset'].items():
#             sqlRequest = sqlRequest.replace('{: '+ key +' :}', f"{VariableSQLSet.objects.get(id=key).sql}='{value}'")
#         result = connect.execute(text(sqlRequest))
#         for row in result:
#             oneRow = {}
#             for i, col in enumerate(row):
#                 oneRow[colnames[i]] = str(col)
#             res.append(oneRow)
#         response['result'] = json.dumps({'rows': res})
#         connect.close()
#         return response
#     except Exception as exc:
#         print(exc)
#         response = HttpResponseNotModified()
#         return response

# @csrf_exempt
# def update_table(request):
#     try:
#         request_data = request.body
#         stroke = json.loads(request_data)
#         get_all_table(stroke['con_id'])
#         response = HttpResponse()
#         return response
#     except Exception as exc:
#         print(exc)
#         response = HttpResponseNotModified()
#         return response


# def get_all_table(conn_id):
#     try:
#         conn = Connection.objects.get(id=conn_id)
#         ENGINE_PATH_WIN_AUTH = conn.dialect.name + '://' + conn.username + ':' + conn.password +'@' + conn.host + ':' + conn.port + '/' + conn.service
#         engine = create_engine(ENGINE_PATH_WIN_AUTH, pool_size=50, pool_pre_ping=True)
#         inspector = inspect(engine)
#         tables = {}
#         for table_name in inspector.get_table_names():
#             columns = []
#             for column in inspector.get_columns(table_name):
#                 columns.append({'name': f"{table_name}.{column['name']}", 'type': str(column['type'])})
#             fks = []
#             for fk in inspector.get_foreign_keys(table_name):
#                 fks.append({'constrained_column': f"{table_name}.{fk['constrained_columns'][0]}", 'referr_table': fk['referred_table'], 'referr_column': f"{fk['referred_table']}.{fk['referred_columns'][0]}"})
#             tables[table_name] = {"columns": columns, "fks": fks}
#         conn.tables = json.dumps(tables)
#         conn.save()
#     except Exception as exc:
#         print(exc)


# @csrf_exempt
# def dataBaseUserSync(request):
#     try:
#         ENGINE_PATH_WIN_AUTH = 'mysql+mysqldb://admin:admin@10.104.224.123:3306/portal4aas'
#         engine = create_engine(ENGINE_PATH_WIN_AUTH, pool_size=50, pool_pre_ping=True)
#         connect = engine.connect()
#         connect.execute(text("SET lc_time_names = 'ru_RU'"))
#         result = connect.execute(text("SELECT * FROM usr INNER JOIN court ON usr.court_id = court.id"))
#         for row in result:
#             print(row)
#             username = row[3]
#             first_name = row[4]
#             last_name = row[7]
#             middle_name = row[6]
#             organisation = Organisation.objects.filter(name=row[13])
#             if organisation:
#                 organisation = organisation[0]
#             else:
#                 organisation = Organisation()
#                 organisation.name = row[13]
#                 organisation.save()
#             user = models.User.objects.filter(username=username)
#             if user:
#                 user = user[0]
#                 user.first_name = first_name
#                 user.last_name = last_name
#             else:
#                 user = models.User()
#                 user.username = username
#                 user.first_name = first_name
#                 user.last_name = last_name
#             user.save()
#             profile_of_user = Profile.objects.filter(user=user)
#             if profile_of_user:
#                 profile_of_user = profile_of_user[0]
#                 profile_of_user.firstName = first_name
#                 profile_of_user.lastName = last_name
#                 profile_of_user.middleName = middle_name
#                 profile_of_user.organisation = organisation
#             else:
#                 profile_of_user = Profile()
#                 profile_of_user.user = user
#                 profile_of_user.firstName = first_name
#                 profile_of_user.lastName = last_name
#                 profile_of_user.middleName = middle_name
#                 profile_of_user.organisation = organisation
#                 profile_of_user.canAddOrganisationDocument = 0
#             profile_of_user.save()
#         connect.close()
#         return HttpResponse
#     except Exception as exc:
#         print(exc)
        
def upload_csv_and_get_columns(request):
    if request.method == 'POST':
        csv_file = request.FILES['csv_file']
        df = pd.read_csv(csv_file)
        return HttpResponse(";".join(str(x) for x in df.columns.tolist()))
    
def get_object_parameters(request, pk):
    if request.method == 'POST':
        object = Object.objects.filter(id=int(pk))[0]
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
    try:
        # находим объект по pk
        object = Object.objects.select_related().get(id=pk)

        # открываем файл, загруженный в объект,
        # преобразуем его в словарь
        with open(f'{settings.MEDIA_ROOT}/{object.data}', 'rb') as f:
            data_obj = pickle.load(f)

        # находим параметр-идентификатор,
        # получаем его имя,
        # берём из словаря значения этого параметра,
        # преобразуем их в список
        param_ident = Parameter.objects.filter(object=object, identificator=True).first()
        idents = data_obj[param_ident.name].values.tolist()
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
    }
    if request.method == 'POST':
        return HttpResponse(json.dumps({
            'object': object.to_dict(),
            'idents': idents,
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
    if request.method == 'POST':
        # находим объект по pk
        object = Object.objects.filter(id=int(pk))[0]
        
        # открываем файл, загруженный в объект
        with open(f'{settings.MEDIA_ROOT}\{object.data}', 'rb') as f:
            # считываем файл
            data_obj = pickle.load(f)
        
        # находим параметр-идентификатор
        param_ident = Parameter.objects.filter(object=object,identificator=True)[0]
        
        # находим идентификатор, указанный в запросе
        ident = str(request.POST[param_ident.name]).strip()
         # находим строку в файле, по этому параметру
        data = data_obj.loc[data_obj[param_ident.name] == ident]
        
        # если строка не найдена, возвращаем 404
        if data.empty:
            return HttpResponse(status=404)
        
        data_dict = data.to_dict(orient='records')
        print(data_dict)
        
        for key, value in data_dict[0].items():
            param = Parameter.objects.filter(object=object,name=key)[0]
            data_dict[0][key] = {'data_type': param.data_type, 'value': value}
            if param.data_type == 'ARRAY':
                data_dict[0][key] = {'data_type': param.data_type, 'value': value.split(param.array_separator)}
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
        
        print(col_names)
        # создаем параметры Parameter для каждого столбца
        parameters = [
            Parameter(
                object=object,
                name=col_names[i],
                data_type=col_types[i],
                array_separator=arr_delim[i],
                identificator=(col == ident)
            )
            for i, col in enumerate(df.columns)
        ]
        # создаем параметры
        Parameter.objects.bulk_create(parameters)
        df.columns = col_names
        # сохраняем файл
        df.to_pickle(file_path)
        # возвращаем ответ
        return HttpResponse(f'/database/get_object/{object.id}')

    # отображаем форму
    return render(request, 'database_manager/upload_csv.html')

def update_csv(request, pk):
    """
    Обработчик формы загрузки CSV-файла.
    
    - если метод POST, то загружает файл, читает его,
      обрабатывает, сохраняет в файл, создает объект Object,
      создает параметры Parameter для каждого столбца,
      указывает, какой столбец является идентификатором
    - если метод GET, то отображает форму загрузки CSV-файла
    """
    if request.method == 'POST':
        
        object = Object.objects.filter(id=int(pk))[0]
        
        # открываем файл, загруженный в объект
        with open(f'{settings.MEDIA_ROOT}\{object.data}', 'rb') as f:
            # считываем файл
            data_obj = pickle.load(f)
            f.close()
            
        # читаем файл
        csv_file = request.FILES['csv_file']
        df = pd.read_csv(csv_file)
        print(df)
        
        if len(data_obj.columns.tolist()) != len(df.columns.tolist()):
            return HttpResponseNotModified("Количество столбцов разное. Для загрузки данных из этого CSV создайте новый объект.")
        
        # если выбран столбец для удаления, то удаляем
        drop_column = request.POST.get('drop_column', '-1')
        if drop_column != '-1':
            df.dropna(subset=[drop_column], inplace=True)

        # приводим все значения к строке и убираем лишние пробелы
        df = df.map(lambda x: str(x).strip())

        file_path = object.data
        
        df.columns = data_obj.columns.tolist()
        print(df)
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