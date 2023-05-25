from django.shortcuts import render
from .models import Connection, VariableSQLGet, VariableSQLSet, VariableSQLSet_VariableSQLGet
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
        connection.dialect = request.POST['dialect']
        connection.username = request.POST['username']
        connection.password = request.POST['password']
        connection.host = request.POST['host']
        connection.port = request.POST['port']
        connection.service = request.POST['service']
        connection.save()
        response = redirect(f'/database/setting_database/')
        return response

    success_url = reverse_lazy('/')

class CreateSQLVariableGet(CreateView):
    model = VariableSQLGet

    form_class = SQLVariableFormGet
    template_name = 'database_manager/create_sql_variable_get.html'

    def get_context_data(self, *args, **kwargs):
        context = super(CreateSQLVariableGet, self).get_context_data(*args, **kwargs)
        context['sql_variables_set'] = VariableSQLSet.objects.all()
        if self.request.user.is_authenticated:
            context['profile'] = Profile.objects.filter(user=self.request.user)[0]
        return context

    def post(self, request, *args, **kwargs):
        print(request.POST)
        sqlVariable = VariableSQLGet()
        sqlVariable.name = request.POST['name']
        sqlVariable.sql = request.POST['sql']
        sqlVariable.save()
        response = redirect(f'/database/setting_database/')
        return response


class CreateSQLVariableSet(CreateView):
    model = VariableSQLSet

    form_class = SQLVariableFormSet
    template_name = 'database_manager/create_sql_variable_set.html'

    def get_context_data(self, *args, **kwargs):
        context = super(CreateSQLVariableSet, self).get_context_data(*args, **kwargs)
        if self.request.user.is_authenticated:
            context['profile'] = Profile.objects.filter(user=self.request.user)[0]
        return context

    def post(self, request, *args, **kwargs):
        print(request.POST)
        sqlVariable = VariableSQLSet()
        sqlVariable.name = request.POST['name']
        sqlVariable.sql = request.POST['sql']
        sqlVariable.save()
        response = redirect(f'/database/setting_database/')
        return response

@csrf_exempt
def SaveSqlVariableGet(request):
    request_data = request.body
    stroke = json.loads(request_data)
    sqlVariableGet = VariableSQLGet()
    sqlVariableGet.name = stroke['name']
    sqlVariableGet.sql = stroke['sql']
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
    try:
        request_data = request.body
        stroke = json.loads(request_data)
        print(stroke)
        conn = Connection.objects.get(id=stroke['con_id'])
        ENGINE_PATH_WIN_AUTH = conn.dialect + '+' + 'cx_oracle' + '://' + conn.username + ':' + conn.password +'@' + conn.host + ':' + conn.port + '/?service_name=' + conn.service
        engine = create_engine(ENGINE_PATH_WIN_AUTH, pool_size=50, pool_pre_ping=True)
        
        connect = engine.connect()
        try:
            response = HttpResponse()
            sql = text("SELECT Name FROM participant INNER JOIN case on participant.case = case.objectid inner join subject on "
           "participant.subject = subject.objectid WHERE NUMAPPEAL = '04АП-1234/22'")
            result = connect.execute(sql)
            for i, row in enumerate(result):
                print(row)  
            response['connection'] = '1'
            
            print(response.has_header)
            connect.close()
            return response
        except Exception as exc:
            response = HttpResponseNotModified()
            print(exc)
            response['connection'] = '0'
            print(response.has_header)
            connect.close()
            return response
    except Exception as exc:
        print(exc)
        response = HttpResponseNotModified()
        response['connection'] = '0'
        return response
    
@csrf_exempt
def TestGetFromDB(request):
    try:
        request_data = request.body
        stroke = json.loads(request_data)
        print(stroke)
        conn = Connection.objects.get(id=stroke['con_id'])
        ENGINE_PATH_WIN_AUTH = conn.dialect + '+' + 'cx_oracle' + '://' + conn.username + ':' + conn.password +'@' + conn.host + ':' + conn.port + '/?service_name=' + conn.service
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