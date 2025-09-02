function DeleteVar(event) {
    var btn = event.target.closest('.btn');
    var json = JSON.stringify({ 'id': btn.parentElement.parentElement.getAttribute('data-idVar') });
    var url;
    if (btn.parentElement.closest('.sql_variable_set')) {
        url = "/database/delete_sql_set/"
    }
    else if (btn.parentElement.closest('.sql_variable_get')) {
        url = "/database/delete_sql_get/"
    }
    $.ajax({
        type: "POST",
        url: url,
        data: json,
        contentType: "application/json; charset=utf-8",
        success: function (res, status, xhr) {
            switch (xhr.status) {
                case 200:
                    btn.parentElement.parentElement.parentElement.removeChild(btn.parentElement.parentElement)
                    break;
                case 304:
                    break;
            }
        }
    });
}

function ConnectionChanged() {
    var conn_id = document.querySelector('#id_connection').value;
    updateSqlVariables(conn_id);
    TestConnection();
}

function updateSqlVariables(conn_id) {
    for (let variable of document.querySelectorAll('.sql_variable')) {
        if (variable.getAttribute('data-idCon') != conn_id) {
            variable.hidden = true;
        }
        else {
            variable.hidden = false;
        }
    }
}

function MakeUpdatable(event) {
    var btn = event.target.closest('.btn');
    btn.parentElement.nextElementSibling.hidden = false;
    console.log(btn.parentElement)
    btn.parentElement.hidden = true;
    btn.parentElement.parentElement.querySelector('.sql_variable_name').removeAttribute('readonly');
    btn.parentElement.parentElement.querySelector('.sql_variable_sql').removeAttribute('readonly');
}

function CreateSQLVariableSet() {
    location.href = '/database/create_sql_variable_set/' + document.querySelector('#id_connection').value;
}

function CreateSQLVariableGet() {
    location.href = '/database/create_sql_variable_get/' + document.querySelector('#id_connection').value;
}

function GetTables() {
    $.ajax({
        type: "POST",
        url: "/database/update_table/",
        data: JSON.stringify({ 'con_id': document.querySelector('#id_connection').value }),
        contentType: "application/json; charset=utf-8",
        success: function (res, status, xhr) {
            switch (xhr.status) {
                case 200:
                    break;
                case 304:
                    break;
            }
        }
    });
}

function SyncUsers() {
    $.ajax({
        type: "POST",
        url: "/database/sync_user/",
        data: '',
        contentType: "application/json; charset=utf-8",
        success: function (res, status, xhr) {
            switch (xhr.status) {
                case 200:
                    break;
                case 304:
                    break;
            }
        }
    });
}

function AcceptChange(event) {
    var btn = event.target.closest('.btn');
    btn.parentElement.previousElementSibling.hidden = false;
    console.log(btn.parentElement)
    btn.parentElement.hidden = true;
    btn.parentElement.parentElement.querySelector('.sql_variable_name').setAttribute('readonly', 'true');
    btn.parentElement.parentElement.querySelector('.sql_variable_sql').setAttribute('readonly', 'true');

    var json = JSON.stringify({ 'id': btn.parentElement.parentElement.getAttribute('data-idVar'), 'name': btn.parentElement.parentElement.querySelector('.sql_variable_name').value, 'sql': btn.parentElement.parentElement.querySelector('.sql_variable_sql').value });
    var url;
    if (btn.parentElement.closest('.sql_variable_set')) {
        url = "/database/update_sql_set/"
    }
    else if (btn.parentElement.closest('.sql_variable_get')) {
        url = "/database/update_sql_get/"
    }
    $.ajax({
        type: "POST",
        url: url,
        data: json,
        contentType: "application/json; charset=utf-8",
        success: function (res, status, xhr) {
            switch (xhr.status) {
                case 200:
                    break;
                case 304:
                    break;
            }
        }
    });
}

function TestConnection() {
    var connectionValue = document.querySelector('#id_connection').value;
    var connectionIndicator = document.querySelector("#connection_indicator")
    console.log(connectionValue);
    $.ajax({
        type: "POST",
        url: "/database/test_connection/",
        data: JSON.stringify({ 'con_id': connectionValue }),
        contentType: "application/json; charset=utf-8",
        success: function (res, status, xhr) {
            switch (xhr.status) {
                case 200:
                    console.log(xhr);
                    console.log(xhr.getResponseHeader('connection'));
                    connectionIndicator.classList.remove('bad_connection');
                    connectionIndicator.classList.add('good_connection');
                    break;
                default:
                    connectionIndicator.style.color = 'red';
                    connectionIndicator.classList.remove('good_connection');
                    connectionIndicator.classList.add('bad_connection');
                    break;
            }
        }
    });
}

function GetVariables() {
    var json_stroke = []
    for (let variable of document.querySelector('#divSQLVariableGet').children) {
        var set_ids = []
        for (let varSetId of variable.getAttribute('data-idSet').split(';')) {
            try {
                set_ids.push({ 'id': varSetId, 'value': document.querySelector('.sql_variable_set[data-idVar="' + varSetId + '"] > .sql_variable_value').value });
            } catch (error) {
                continue;
            }
        }
        json_stroke.push({ 'get_id': variable.getAttribute('data-idVar'), 'set_ids': set_ids })
    }
    return json_stroke
}

function TestGetFromDB() {
    $.ajax({
        type: "POST",
        url: "/database/test_get/",
        data: JSON.stringify({ 'con_id': document.querySelector('#id_connection').value, 'variables': GetVariables() }),
        contentType: "application/json; charset=utf-8",
        success: function (res, status, xhr) {
            switch (xhr.status) {
                case 200:
                    var res = decodeURIComponent(escape(xhr.getResponseHeader('result')))
                    for (let r of res.split(';')) {
                        try {
                            var id = r.split(':')[0]
                            var val = r.split(':')[1]
                            document.querySelector('.sql_variable_get[data-idVar="' + id + '"] > .sql_variable_value').value = val
                        } catch (error) {
                            continue;
                        }

                    }
                    break;
                case 304:
                    break;
            }
        }
    });
}

window.onload = function () {
    console.log('Подключено')
    ConnectionChanged();
}
