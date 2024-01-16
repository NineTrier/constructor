from django.shortcuts import render
from .models import Dialect, Connection, VariableSQLGet, VariableSQLSet, VariableSQLSet_VariableSQLGet, Connection_Organisation
from django.views.generic import CreateView
from .forms import ConnectionForm, SQLVariableFormGet, SQLVariableFormSet
from django.contrib.auth import views, models
from django.db.models import Q
from django.core.exceptions import PermissionDenied
from user_manager.models import Profile, Organisation
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse_lazy
from django.views.decorators.csrf import csrf_exempt
from django.http import HttpResponse, Http404, HttpResponseNotModified, HttpResponseForbidden
import json
import re
import cx_Oracle

from sqlalchemy.engine import create_engine
from sqlalchemy import inspect
from sqlalchemy import text


cx_Oracle.init_oracle_client(lib_dir=r"c:\oracle\instantclient_21_11")

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
        VariableSQLSet.objects.get(id=stroke['id']).delete()
        return response
    except Exception as exc:
        print(exc)
        response = HttpResponseNotModified()
        return response


def setting_database(request):
    profileUser = Profile.objects.filter(user=request.user)[0]
    context = {
        'profile': profileUser,
        'connections': [con.connection for con in Connection_Organisation.objects.filter(organisation=profileUser.organisation)],
        'sql_variables_get': VariableSQLGet.objects.all(),
        'sql_variables_set': VariableSQLSet.objects.all(),
        'set_get': VariableSQLSet_VariableSQLGet.objects.all(),
    }
    return render(request, 'database_manager/database_manager.html', context)


@csrf_exempt
def TestConnection(request):
    """Функция для обработки запроса по тестированию подключения к базе данных"""
    try:
        print(request.body)
        request_data = request.body
        stroke = json.loads(request_data)
        
        conn = Connection.objects.get(id=stroke['con_id'])
        if conn.dialect.id == 1:
            ENGINE_PATH_WIN_AUTH = conn.dialect.name + '+' + conn.dialect.driver + '://' + conn.username + ':' + conn.password +'@' + conn.host + ':' + conn.port + '/?service_name=' + conn.service
        else:
            ENGINE_PATH_WIN_AUTH = conn.dialect.name + '+' + conn.dialect.driver + '://' + conn.username + ':' + conn.password +'@' + conn.host + ':' + conn.port + '/' + conn.service
        engine = create_engine(ENGINE_PATH_WIN_AUTH, pool_size=50, pool_pre_ping=True)
        
        connect = engine.connect()
        # connect.execute(text("SET lc_time_names = 'ru_RU'"))
        try:
            response = HttpResponse()
            if conn.dialect.id == 1:
                sql = text("SELECT 1 FROM DUAL")
            else:
                sql = text("SELECT 1")
            result = connect.execute(sql)
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
        # profileUser = Profile.objects.filter(user=request.user)[0]
        conn = Connection.objects.get(id=stroke['con_id'])
        # print(Connection_Organisation.objects.filter(Q(connection=conn) & Q(organisation=profileUser.organisation)))
        # if len(Connection_Organisation.objects.filter(Q(connection=conn) & Q(organisation=profileUser.organisation))) == 0:
        #     return HttpResponseForbidden()
        print(stroke)
        if conn.dialect.id == 1:
            ENGINE_PATH_WIN_AUTH = conn.dialect.name + '+' + conn.dialect.driver + '://' + conn.username + ':' + conn.password +'@' + conn.host + ':' + conn.port + '/?service_name=' + conn.service
        else:
            ENGINE_PATH_WIN_AUTH = conn.dialect.name + '+' + conn.dialect.driver + '://' + conn.username + ':' + conn.password +'@' + conn.host + ':' + conn.port + '/' + conn.service
        engine = create_engine(ENGINE_PATH_WIN_AUTH, pool_size=50, pool_pre_ping=True)
        connect = engine.connect()
        if conn.id == 22:
            connect.execute(text("SET lc_time_names = 'ru_RU'"))
        res = {}
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
                res[varGet['get_id']] = row[0]
                #res += f"{varGet['get_id']}:{row[0]};"
                break
        #response['result'] = res.encode('utf-8')
        print(res)
        connect.close()
        return HttpResponse(json.dumps(res, default=str))
    except Exception as exc:
        print(exc)
        response = HttpResponseNotModified()
        return response

@csrf_exempt 
def GetFromDBByTable(request):
    request_data = request.body
    stroke = json.loads(request_data)
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
        if conn.id == 22:
            connect.execute(text("SET lc_time_names = 'ru_RU'"))
        tables = json.loads(conn.tables)
        colnames = []
        for table in stroke['tables']:
            for col in tables[table]['columns']:
                colnames.append(col['name'])
        res = []
        response = HttpResponse()
        if conn.dialect.id == 1:
            result = connect.execute(text(f"SELECT * FROM {stroke['tableSQL']} WHERE ROWNUM <= 10"))
        else:
            result = connect.execute(text(f"SELECT * FROM {stroke['tableSQL']} LIMIT 10"))
        for row in result:
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
        if conn.id == 22:
            connect.execute(text("SET lc_time_names = 'ru_RU'"))
        tables = json.loads(conn.tables)
        colnames = []
        for table in stroke['tables']:
            for col in tables[table]['columns']:
                colnames.append(col['name'])
        res = []
        response = HttpResponse()
        if conn.dialect.id == 1:
            sqlRequest = f"SELECT * FROM {stroke['tableSQL']} WHERE {stroke['where']} AND ROWNUM <= 10"
        else:
            sqlRequest = f"SELECT * FROM {stroke['tableSQL']} WHERE {stroke['where']} LIMIT 10"
        for key, value in stroke['sqlset'].items():
            sqlRequest = sqlRequest.replace('{: '+ key +' :}', f"{VariableSQLSet.objects.get(id=key).sql}='{value}'")
        result = connect.execute(text(sqlRequest))
        for row in result:
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
            for column in inspector.get_columns(table_name):
                columns.append({'name': f"{table_name}.{column['name']}", 'type': str(column['type'])})
            fks = []
            for fk in inspector.get_foreign_keys(table_name):
                fks.append({'constrained_column': f"{table_name}.{fk['constrained_columns'][0]}", 'referr_table': fk['referred_table'], 'referr_column': f"{fk['referred_table']}.{fk['referred_columns'][0]}"})
            tables[table_name] = {"columns": columns, "fks": fks}
        conn.tables = json.dumps(tables)
        conn.save()
    except Exception as exc:
        print(exc)


@csrf_exempt
def dataBaseUserSync(request):
    try:
        ENGINE_PATH_WIN_AUTH = 'mysql+mysqldb://admin:admin@10.104.224.123:3306/portal4aas'
        engine = create_engine(ENGINE_PATH_WIN_AUTH, pool_size=50, pool_pre_ping=True)
        connect = engine.connect()
        connect.execute(text("SET lc_time_names = 'ru_RU'"))
        result = connect.execute(text("SELECT * FROM usr INNER JOIN court ON usr.court_id = court.id"))
        for row in result:
            print(row)
            username = row[3]
            first_name = row[4]
            last_name = row[7]
            middle_name = row[6]
            organisation = Organisation.objects.filter(name=row[13])
            if organisation:
                organisation = organisation[0]
            else:
                organisation = Organisation()
                organisation.name = row[13]
                organisation.save()
            user = models.User.objects.filter(username=username)
            if user:
                user = user[0]
                user.first_name = first_name
                user.last_name = last_name
            else:
                user = models.User()
                user.username = username
                user.first_name = first_name
                user.last_name = last_name
            user.save()
            profile_of_user = Profile.objects.filter(user=user)
            if profile_of_user:
                profile_of_user = profile_of_user[0]
                profile_of_user.firstName = first_name
                profile_of_user.lastName = last_name
                profile_of_user.middleName = middle_name
                profile_of_user.organisation = organisation
            else:
                profile_of_user = Profile()
                profile_of_user.user = user
                profile_of_user.firstName = first_name
                profile_of_user.lastName = last_name
                profile_of_user.middleName = middle_name
                profile_of_user.organisation = organisation
                profile_of_user.canAddOrganisationDocument = 0
            profile_of_user.save()
        connect.close()
        return HttpResponse
    except Exception as exc:
        print(exc)