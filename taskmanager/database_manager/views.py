from django.shortcuts import render
from .models import Dialect, Connection, VariableSQLGet, VariableSQLSet, VariableSQLSet_VariableSQLGet
from django.views.generic import CreateView
from .forms import ConnectionForm, SQLVariableFormGet, SQLVariableFormSet
from django.contrib.auth import views, models
from user_manager.models import Profile
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse_lazy
from django.views.decorators.csrf import csrf_exempt
from django.http import HttpResponse, Http404, HttpResponseNotModified
import json
import re

from sqlalchemy.engine import create_engine
from sqlalchemy import inspect
from sqlalchemy import text

# Класс для создания подключения к базе данных
class CreateConnection(CreateView):
    model = Connection

    form_class = ConnectionForm
    template_name = 'database_manager/create_connection.html'

    def get_context_data(self, *args, **kwargs):
        context = super(CreateConnection, self).get_context_data(*args, **kwargs)
        if self.request.user.is_authenticated:
            context['profile'] = Profile.objects.filter(user=self.request.user)[0]
        return context

    def post(self, request, *args, **kwargs):
        print(request.POST)
        connection = Connection()
        connection.name = request.POST['name']
        connection.dialect = Dialect.objects.get(id=request.POST['dialect'])
        connection.username = request.POST['username']
        connection.password = request.POST['password']
        connection.host = request.POST['host']
        connection.port = request.POST['port']
        connection.service = request.POST['service']
        connection.save()
        get_all_table(conn_id=connection.id)
        response = redirect(f'/database/setting_database/')
        return response

    success_url = reverse_lazy('/')

# Класс для создания переменной SQL-запроса
class CreateSQLVariableGet(CreateView):
    model = VariableSQLGet

    form_class = SQLVariableFormGet
    template_name = 'database_manager/create_sql_variable_get.html'

    def get_context_data(self, *args, **kwargs):
        context = super(CreateSQLVariableGet, self).get_context_data(*args, **kwargs)
        context['sql_variables_set'] = VariableSQLSet.objects.all()
        context['conn'] = Connection.objects.get(id=self.kwargs['pk'])
        if self.request.user.is_authenticated:
            context['profile'] = Profile.objects.filter(user=self.request.user)[0]
        return context

    def post(self, request, *args, **kwargs):
        print(request.POST)
        sqlVariable = VariableSQLGet()
        sqlVariable.name = request.POST['name']
        sqlVariable.sql = request.POST['sql']
        sqlVariable.connection = Connection.objects.get(id=request.POST['connection'])
        sqlVariable.save()
        response = redirect(f'/database/setting_database/')
        return response

# Класс для создания переменной SQL-параметра
class CreateSQLVariableSet(CreateView):
    model = VariableSQLSet

    form_class = SQLVariableFormSet
    template_name = 'database_manager/create_sql_variable_set.html'

    def get_context_data(self, *args, **kwargs):
        context = super(CreateSQLVariableSet, self).get_context_data(*args, **kwargs)
        context['conn'] = Connection.objects.get(id=self.kwargs['pk'])
        if self.request.user.is_authenticated:
            context['profile'] = Profile.objects.filter(user=self.request.user)[0]
        return context

    def post(self, request, *args, **kwargs):
        print(request.POST)
        sqlVariable = VariableSQLSet()
        sqlVariable.name = request.POST['name']
        sqlVariable.sql = request.POST['sql']
        sqlVariable.connection = Connection.objects.get(id=request.POST['connection'])
        sqlVariable.save()
        response = redirect(f'/database/setting_database/')
        return response

@csrf_exempt
def SaveSqlVariableGet(request):
    """Функция для обработки запроса по сохранению переменной SQL-Запроса"""
    request_data = request.body
    stroke = json.loads(request_data)
    sqlVariableGet = VariableSQLGet()
    sqlVariableGet.name = stroke['name']
    sqlVariableGet.sql = stroke['sql']
    sqlVariableGet.connection = Connection.objects.get(id=stroke['connection'])
    sqlVariableGet.save()
    for regexp in re.findall(r'{: \d{1,5} :}', stroke['sql']):
        print(VariableSQLSet.objects.get(id=re.search(r'\d{1,5}', regexp)[0]))
        varSet_varGet = VariableSQLSet_VariableSQLGet()
        varSet_varGet.variableGet = sqlVariableGet
        varSet_varGet.variableSet = VariableSQLSet.objects.get(id=re.search(r'\d{1,5}', regexp)[0])
        varSet_varGet.save()

    return HttpResponse()

@csrf_exempt
def UpdateSQLVariableSet(request):
    """Функция для обработки запроса по сохранению переменной SQL-параметра"""
    try:
        response = HttpResponse()
        request_data = request.body
        stroke = json.loads(request_data)
        print(stroke)
        var = VariableSQLSet.objects.get(id=stroke['id'])
        var.name = stroke['name']
        var.sql = stroke['sql']
        var.save()
        return response
    except Exception as exc:
        print(exc)
        response = HttpResponseNotModified()
        return response


@csrf_exempt
def UpdateSQLVariableGet(request):
    """Функция для обработки запроса по обновлению переменной SQL-Запроса"""
    try:
        response = HttpResponse()
        request_data = request.body
        stroke = json.loads(request_data)
        print(stroke)
        var = VariableSQLGet.objects.get(id=stroke['id'])
        var.name = stroke['name']
        var.sql = stroke['sql']
        var.save()
        for get_set in VariableSQLSet_VariableSQLGet.objects.filter(variableGet=var):
            get_set.delete()
        for regexp in re.findall(r'{: \d{1,5} :}', stroke['sql']):
            print(VariableSQLSet.objects.get(id=re.search(r'\d{1,5}', regexp)[0]))
            varSet_varGet = VariableSQLSet_VariableSQLGet()
            varSet_varGet.variableGet = var
            varSet_varGet.variableSet = VariableSQLSet.objects.get(id=re.search(r'\d{1,5}', regexp)[0])
            varSet_varGet.save()
        return response
    except Exception as exc:
        print(exc)
        response = HttpResponseNotModified()
        return response

@csrf_exempt
def DeleteSQLVariableGet(request):
    """Функция для обработки запроса по удалению переменной SQL-Запроса"""
    try:
        response = HttpResponse()
        request_data = request.body
        stroke = json.loads(request_data)
        print(stroke)
        VariableSQLGet.objects.get(id=stroke['id']).delete()
        return response
    except Exception as exc:
        print(exc)
        response = HttpResponseNotModified()
        return response
    
@csrf_exempt
def DeleteSQLVariableSet(request):
    """Функция для обработки запроса по удалению переменной SQL-параметра"""
    try:
        response = HttpResponse()
        request_data = request.body
        stroke = json.loads(request_data)
        print(stroke)
        VariableSQLSet.objects.get(id=stroke['id']).delete()
        return response
    except Exception as exc:
        print(exc)
        response = HttpResponseNotModified()
        return response


def setting_database(request):
    context = {
        'profile': Profile.objects.filter(user=request.user)[0],
        'connections': Connection.objects.all(),
        'sql_variables_get': VariableSQLGet.objects.all(),
        'sql_variables_set': VariableSQLSet.objects.all(),
        'set_get': VariableSQLSet_VariableSQLGet.objects.all(),
    }
    return render(request, 'database_manager/database_manager.html', context)


@csrf_exempt
def TestConnection(request):
    """Функция для обработки запроса по тестированию подключения к базе данных"""
    try:
        request_data = request.body
        stroke = json.loads(request_data)
        print(stroke)
        conn = Connection.objects.get(id=stroke['con_id'])
        if conn.dialect.id == 1:
            ENGINE_PATH_WIN_AUTH = conn.dialect.name + '+' + conn.dialect.driver + '://' + conn.username + ':' + conn.password +'@' + conn.host + ':' + conn.port + '/?service_name=' + conn.service
        else:
            ENGINE_PATH_WIN_AUTH = conn.dialect.name + '+' + conn.dialect.driver + '://' + conn.username + ':' + conn.password +'@' + conn.host + ':' + conn.port + '/' + conn.service
        engine = create_engine(ENGINE_PATH_WIN_AUTH, pool_size=50, pool_pre_ping=True)
        
        connect = engine.connect()
        try:
            response = HttpResponse()
            if conn.dialect.id == 1:
                sql = text("SELECT 1 FROM DUAL")
            else:
                sql = text("SELECT 1")
            result = connect.execute(sql)
            for i, row in enumerate(result):
                print(row)  
            connect.close()
            response['connection'] = json.dumps({'connection':'1'})
            return response
        except Exception as exc:
            response = HttpResponseNotModified()
            print(exc)
            connect.close()
            response['connection'] = json.dumps({'connection':'0'})
            return response
    except Exception as exc:
        print(exc)
        response = HttpResponseNotModified()
        response['connection'] = json.dumps({'connection':'0'})
        return response
    
@csrf_exempt
def TestGetFromDB(request):
    """Функция для получения данных из базы данных"""
    try:
        request_data = request.body
        stroke = json.loads(request_data)
        print(stroke)
        conn = Connection.objects.get(id=stroke['con_id'])
        if conn.dialect.id == 1:
            ENGINE_PATH_WIN_AUTH = conn.dialect.name + '+' + conn.dialect.driver + '://' + conn.username + ':' + conn.password +'@' + conn.host + ':' + conn.port + '/?service_name=' + conn.service
        else:
            ENGINE_PATH_WIN_AUTH = conn.dialect.name + '+' + conn.dialect.driver + '://' + conn.username + ':' + conn.password +'@' + conn.host + ':' + conn.port + '/' + conn.service
        engine = create_engine(ENGINE_PATH_WIN_AUTH, pool_size=50, pool_pre_ping=True)
        connect = engine.connect()
        res = ""
        response = HttpResponse()
        for varGet in stroke['variables']:
            sqlGetVar = VariableSQLGet.objects.get(id=varGet['get_id'])    
            sql = sqlGetVar.sql
            for varSet in varGet['set_ids']:
                sql = sql.replace('{: '+ varSet['id'] +' :}', f"{VariableSQLSet.objects.get(id=varSet['id']).sql}='{varSet['value']}'")
            print(sql)
            sql = text(sql)
            result = connect.execute(sql)
            for row in result:
                print(row[0])
                res += f"{varGet['get_id']}:{row[0]};"
                break
        response['result'] = res.encode('utf-8')
        connect.close()
        return response
    except Exception as exc:
        print(exc)
        response = HttpResponseNotModified()
        return response

@csrf_exempt 
def GetFromDBByTable(request):
    request_data = request.body
    stroke = json.loads(request_data)
    print(stroke)
    if('where' in stroke):
        return GetFromDBByTableWithWhere(stroke)
    else:
        return GetFromDBByTableWithoutWhere(stroke)


def GetFromDBByTableWithoutWhere(stroke):
    """Функция для получения данных из базы данных"""
    try:
        conn = Connection.objects.get(id=stroke['conn_id'])
        if conn.dialect.id == 1:
            ENGINE_PATH_WIN_AUTH = conn.dialect.name + '+' + conn.dialect.driver + '://' + conn.username + ':' + conn.password +'@' + conn.host + ':' + conn.port + '/?service_name=' + conn.service
        else:
            ENGINE_PATH_WIN_AUTH = conn.dialect.name + '+' + conn.dialect.driver + '://' + conn.username + ':' + conn.password +'@' + conn.host + ':' + conn.port + '/' + conn.service
        engine = create_engine(ENGINE_PATH_WIN_AUTH, pool_size=50, pool_pre_ping=True)
        connect = engine.connect()
        tables = json.loads(conn.tables)
        colnames = []
        for table in stroke['tables']:
            for col in tables[table]['columns']:
                colnames.append(col['name'])

        print(colnames)
        res = []
        response = HttpResponse()
        if conn.dialect.id == 1:
            result = connect.execute(text(f"SELECT * FROM {stroke['tableSQL']} WHERE ROWNUM <= 10"))
        else:
            result = connect.execute(text(f"SELECT * FROM {stroke['tableSQL']} LIMIT 10"))
        for row in result:
            print(row)
            oneRow = {}
            for i, col in enumerate(row):
                oneRow[colnames[i]] = str(col)
            res.append(oneRow)
        response['result'] = json.dumps({'rows': res})
        connect.close()
        return response
    except Exception as exc:
        print(exc)
        response = HttpResponseNotModified()
        return response

def GetFromDBByTableWithWhere(stroke):
    """Функция для получения данных из базы данных"""
    try:
        conn = Connection.objects.get(id=stroke['conn_id'])
        if conn.dialect.id == 1:
            ENGINE_PATH_WIN_AUTH = conn.dialect.name + '+' + conn.dialect.driver + '://' + conn.username + ':' + conn.password +'@' + conn.host + ':' + conn.port + '/?service_name=' + conn.service
        else:
            ENGINE_PATH_WIN_AUTH = conn.dialect.name + '+' + conn.dialect.driver + '://' + conn.username + ':' + conn.password +'@' + conn.host + ':' + conn.port + '/' + conn.service
        engine = create_engine(ENGINE_PATH_WIN_AUTH, pool_size=50, pool_pre_ping=True)
        connect = engine.connect()
        tables = json.loads(conn.tables)
        colnames = []
        for table in stroke['tables']:
            for col in tables[table]['columns']:
                colnames.append(col['name'])

        print(colnames)
        res = []
        response = HttpResponse()
        if conn.dialect.id == 1:
            sqlRequest = f"SELECT * FROM {stroke['tableSQL']} WHERE {stroke['where']} AND ROWNUM <= 10"
        else:
            sqlRequest = f"SELECT * FROM {stroke['tableSQL']} WHERE {stroke['where']} LIMIT 10"
        for key, value in stroke['sqlset'].items():
            sqlRequest = sqlRequest.replace('{: '+ key +' :}', f"{VariableSQLSet.objects.get(id=key).sql}='{value}'")
        print(sqlRequest)
        result = connect.execute(text(sqlRequest))
        for row in result:
            print(row)
            oneRow = {}
            for i, col in enumerate(row):
                oneRow[colnames[i]] = str(col)
            res.append(oneRow)
        response['result'] = json.dumps({'rows': res})
        connect.close()
        return response
    except Exception as exc:
        print(exc)
        response = HttpResponseNotModified()
        return response

@csrf_exempt
def update_table(request):
    try:
        request_data = request.body
        stroke = json.loads(request_data)
        print(stroke)
        get_all_table(stroke['con_id'])
        response = HttpResponse()
        return response
    except Exception as exc:
        print(exc)
        response = HttpResponseNotModified()
        return response


def get_all_table(conn_id):
    try:
        conn = Connection.objects.get(id=conn_id)
        if conn.dialect.id == 1:
            ENGINE_PATH_WIN_AUTH = conn.dialect.name + '+' + conn.dialect.driver + '://' + conn.username + ':' + conn.password +'@' + conn.host + ':' + conn.port + '/?service_name=' + conn.service
        else:
            ENGINE_PATH_WIN_AUTH = conn.dialect.name + '+' + conn.dialect.driver + '://' + conn.username + ':' + conn.password +'@' + conn.host + ':' + conn.port + '/' + conn.service
        engine = create_engine(ENGINE_PATH_WIN_AUTH, pool_size=50, pool_pre_ping=True)
        inspector = inspect(engine)
        tables = {}
        for table_name in inspector.get_table_names():
            columns = []
            print(table_name)
            for column in inspector.get_columns(table_name):
                columns.append({'name': f"{table_name}.{column['name']}", 'type': str(column['type'])})
            fks = []
            for fk in inspector.get_foreign_keys(table_name):
                fks.append({'constrained_column': f"{table_name}.{fk['constrained_columns'][0]}", 'referr_table': fk['referred_table'], 'referr_column': f"{fk['referred_table']}.{fk['referred_columns'][0]}"})
            tables[table_name] = {"columns": columns, "fks": fks}
        print(tables)
        conn.tables = json.dumps(tables)
        conn.save()
    except Exception as exc:
        print(exc)