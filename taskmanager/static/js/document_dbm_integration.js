(function () {
    function showDbmError(message) {
        const text = message || 'Ошибка работы с объектами';
        const containerId = 'dbm-ui-alert-container';
        let container = document.getElementById(containerId);
        if (!container) {
            container = document.createElement('div');
            container.id = containerId;
            container.style.position = 'fixed';
            container.style.top = '12px';
            container.style.right = '12px';
            container.style.zIndex = '2000';
            container.style.minWidth = '300px';
            document.body.appendChild(container);
        }
        const alert = document.createElement('div');
        alert.className = 'alert alert-danger alert-dismissible fade show';
        alert.setAttribute('role', 'alert');
        alert.innerHTML = '<span>' + text + '</span><button type="button" class="btn-close" data-bs-dismiss="alert" aria-label="Close"></button>';
        container.appendChild(alert);
        setTimeout(function () {
            if (alert.parentNode) {
                alert.parentNode.removeChild(alert);
            }
        }, 5000);
    }

    function applyFieldsToObjectUI(fields) {
        Object.entries(fields || {}).forEach(function (entry) {
            const key = entry[0];
            const valueObj = window.dbmDto.normaliseField(entry[1]);
            const objectParameter = document.querySelector('.obj_parameter[data-idParam="' + key + '"]');
            if (!objectParameter) {
                return;
            }
            if (String(valueObj.type).toUpperCase() === 'ARRAY') {
                const container = objectParameter.querySelector('.array_data_container');
                if (!container) {
                    return;
                }
                const values = Array.isArray(valueObj.value) ? valueObj.value : [];
                container.innerHTML = '';
                values.forEach(function (item) {
                    container.insertAdjacentHTML('beforeend', '<div class="array_data"><span>' + item + '</span></div>');
                });
                return;
            }
            const input = objectParameter.querySelector('input[name="' + key + '"]');
            if (!input) {
                return;
            }
            input.value = valueObj.value == null ? '' : String(valueObj.value);
            input.dispatchEvent(new Event('change'));
        });
    }

    function mapV1ListToLegacyModalPayload(basePayload, v1Payload) {
        const result = {
            object: (basePayload && basePayload.object) ? basePayload.object : { id: v1Payload.object_id, name: '' },
            idents: [],
        };
        const schemaParams = (v1Payload && v1Payload.schema && v1Payload.schema.parameters) ? v1Payload.schema.parameters : {};
        let identParamId = null;
        Object.keys(schemaParams).forEach(function (paramId) {
            if (schemaParams[paramId] && schemaParams[paramId].identificator) {
                identParamId = paramId;
            }
        });
        const records = (v1Payload && Array.isArray(v1Payload.records)) ? v1Payload.records : [];
        records.forEach(function (record) {
            const field = identParamId && record.fields ? record.fields[identParamId] : null;
            result.idents.push({
                id: record.record_uid,
                param_ident: field && field.value !== undefined ? String(field.value) : '',
            });
        });
        return result;
    }

    function linkedParentElementChanged(evt) {
        const linkedParam = evt.target.closest('.linked_parameter');
        if (!linkedParam) {
            return;
        }
        const objId = linkedParam.getAttribute('data-linked_object_id');
        const recordUid = linkedParam.value;
        if (!recordUid) {
            return;
        }
        getLinkedObjectData(objId, recordUid);
    }

    function getLinkedObjectData(objId, recordUid) {
        window.dbmApi.getRecord(objId, recordUid).then(function (payload) {
            const data = window.dbmDto.normaliseRecordPayload(payload);
            applyFieldsToObjectUI(data.fields);
            $('#objectFindDataModal').modal('hide');
        }).catch(function (error) {
            console.error(error);
            showDbmError(error.message || 'Не удалось загрузить связанную запись');
        });
    }

    function ConnectNewObject() {
        window.dbmApi.getObjectsToConnect().then(function (data) {
            let select = $('#objectSelect');
            if (select.is('.select2-hidden-accessible')) {
                select.select2('destroy');
            }
            select.empty();
            data.object.forEach(function (obj) {
                const option = new Option(obj.name, obj.id, false, false);
                select.append(option).trigger('change');
            });
            select.select2({
                width: '100%',
                dropdownParent: $('#modal_objectConnectModal_body'),
                multiple: true,
            });
            $('#objectConnectModal').modal('show');
        }).catch(function (error) {
            console.error(error);
            showDbmError(error.message || 'Не удалось получить список объектов');
        });
    }

    function deleteObjectFromDocument(id) {
        if (!confirm('Вы точно хотите отвязать объект от документа?')) {
            return;
        }
        const docId = document.querySelector('meta[name="doc_id"]').getAttribute('content');
        window.dbmApi.deleteObjectFromDocument(docId, id).then(function () {
            alert('Объект отвязан. Сохраните документ и обновите страницу.');
        }).catch(function (error) {
            console.error(error);
            showDbmError(error.message || 'Не удалось удалить объект из документа');
        });
    }

    function addObjects() {
        const select = document.getElementById('objectSelect');
        const selectedObjects = Array.from(select.selectedOptions).map(function (option) {
            return option.value;
        });
        const docId = document.querySelector('meta[name="doc_id"]').getAttribute('content');
        window.dbmApi.connectObjectsToDocument(docId, selectedObjects).then(function () {
            $('#objectConnectModal').modal('hide');
            alert('Объекты подключены. Сохраните документ и обновите страницу.');
        }).catch(function (error) {
            console.error(error);
            showDbmError(error.message || 'Не удалось подключить объекты');
        });
    }

    function getDataFromObject(objId, form) {
        const formData = new FormData(form);
        const recordUid = formData.get('param_ident_id');
        if (!recordUid) {
            showDbmError('Не выбран идентификатор записи');
            return;
        }
        window.dbmApi.getRecord(objId, recordUid).then(function (payload) {
            const data = window.dbmDto.normaliseRecordPayload(payload);
            applyFieldsToObjectUI(data.fields);
            $('#objectFindDataModal').modal('hide');
        }).catch(function (error) {
            console.error(error);
            showDbmError(error.message || 'Не удалось получить данные объекта');
        });
    }

    function click_to_ident(evt, objectId, value, paramIdentId) {
        const objectElement = document.querySelector('#object_' + objectId);
        const input = objectElement.querySelector('input.identificator');
        const inputFast = document.querySelector('.obj_parameter_fast[data-idObj="' + objectId + '"]');
        const inputIdToConnect = objectElement.querySelector('input[name="param_ident_id"]');
        input.value = value;
        inputIdToConnect.value = paramIdentId;
        if (inputFast) {
            inputFast.querySelector('input').value = value;
        }
        input.dispatchEvent(new Event('change'));
        selectedIdent[objectId] = paramIdentId;
        getDataFromObject(objectId, objectElement.querySelector('form'));
    }

    function updateObjectElement(objectId) {
        const ident = selectedIdent[objectId];
        if (!ident) {
            return;
        }
        location.href = '/database/update_element_to_object/' + objectId + '/?id=' + ident;
    }

    function openFindDataModal(data, objId) {
        const container = document.querySelector('.objectFindDataModalContainer');
        const name = document.querySelector('#objectFindDataModalName');
        const datalist = document.getElementById('idents_list');
        name.innerHTML = data.object.name;
        container.innerHTML = '';
        datalist.innerHTML = '';
        for (let i = 0; i < data.idents.length; i++) {
            const ident = data.idents[i];
            container.insertAdjacentHTML(
                'beforeend',
                '<div class="ident col" onclick="click_to_ident(event, \'' + objId + '\', \'' + ident.param_ident + '\', \'' + ident.id + '\')"><span>' + ident.param_ident + '<span></div>'
            );
            datalist.insertAdjacentHTML('beforeend', '<option value="' + ident.param_ident + '"></option>');
        }
        $('#objectFindDataModal').modal('show');
    }

    function onFilterInputInput(evt) {
        const filterValue = evt.target.value.toLowerCase();
        document.querySelectorAll('.ident').forEach(function (element) {
            const textContent = element.textContent.toLowerCase();
            element.style.display = textContent.includes(filterValue) ? 'block' : 'none';
        });
    }

    function getObjectData(evt, objectId) {
        const caller = evt && evt.target ? evt.target : null;
        if (caller) {
            caller.disabled = true;
        }
        window.dbmApi.listRecords(objectId, {
            limit: 200,
            offset: 0,
            order: 'identificator',
            include_schema: 1,
        }).then(function (v1Payload) {
            const normalised = window.dbmDto.normaliseListPayload(v1Payload);
            if (!window.dbmApi.isLegacyFallbackEnabled || !window.dbmApi.isLegacyFallbackEnabled()) {
                return mapV1ListToLegacyModalPayload(null, normalised);
            }
            return window.dbmApi.getObject(objectId).then(function (legacyPayload) {
                return mapV1ListToLegacyModalPayload(legacyPayload, normalised);
            }).catch(function () {
                return mapV1ListToLegacyModalPayload(null, normalised);
            });
        }).then(function (modalData) {
            openFindDataModal(modalData, objectId);
        }).catch(function (error) {
            console.error(error);
            showDbmError(error.message || 'Не удалось получить список записей');
        }).finally(function () {
            if (caller) {
                caller.disabled = false;
            }
        });
    }

    window.documentDbmIntegration = {
        linkedParentElementChanged: linkedParentElementChanged,
        getLinkedObjectData: getLinkedObjectData,
        ConnectNewObject: ConnectNewObject,
        deleteObjectFromDocument: deleteObjectFromDocument,
        addObjects: addObjects,
        getDataFromObject: getDataFromObject,
        click_to_ident: click_to_ident,
        updateObjectElement: updateObjectElement,
        openFindDataModal: openFindDataModal,
        onFilterInputInput: onFilterInputInput,
        getObjectData: getObjectData,
    };

    window.linkedParentElementChanged = linkedParentElementChanged;
    window.getLinkedObjectData = getLinkedObjectData;
    window.ConnectNewObject = ConnectNewObject;
    window.deleteObjectFromDocument = deleteObjectFromDocument;
    window.addObjects = addObjects;
    window.getDataFromObject = getDataFromObject;
    window.click_to_ident = click_to_ident;
    window.updateObjectElement = updateObjectElement;
    window.openFindDataModal = openFindDataModal;
    window.onFilterInputInput = onFilterInputInput;
    window.getObjectData = getObjectData;
})();
