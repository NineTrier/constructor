(function () {
    function showAlert(message, type) {
        const host = document.getElementById('dbm-object-alerts');
        if (!host) {
            return;
        }
        host.innerHTML = '';
        const level = type || 'danger';
        const el = document.createElement('div');
        el.className = 'alert alert-' + level;
        el.setAttribute('role', 'alert');
        el.textContent = message;
        host.appendChild(el);
    }

    function setLoading(isLoading) {
        const loader = document.getElementById('dbm-records-loading');
        if (!loader) {
            return;
        }
        loader.classList.toggle('d-none', !isLoading);
    }

    function getObjectId() {
        const meta = document.querySelector('meta[name="object_id"]');
        return meta ? String(meta.getAttribute('content') || '').trim() : '';
    }

    function getCsrfToken() {
        const meta = document.querySelector('meta[name="csrf_token"]');
        if (meta) {
            return meta.getAttribute('content') || '';
        }
        const input = document.querySelector('input[name="csrfmiddlewaretoken"]');
        return input ? (input.value || '') : '';
    }

    function getDocumentsFromPage() {
        const script = document.getElementById('object-documents-data');
        if (!script) {
            return [];
        }
        try {
            const payload = JSON.parse(script.textContent || '[]');
            if (!Array.isArray(payload)) {
                return [];
            }
            return payload
                .map(function (item) {
                    if (!item || typeof item !== 'object') {
                        return null;
                    }
                    const pairs = Object.entries(item);
                    if (!pairs.length) {
                        return null;
                    }
                    return {
                        id: String(pairs[0][0]),
                        name: String(pairs[0][1]),
                    };
                })
                .filter(Boolean);
        } catch (_error) {
            return [];
        }
    }

    function findIdentParamId(schema) {
        const params = schema && schema.parameters ? schema.parameters : {};
        const keys = Object.keys(params);
        for (let i = 0; i < keys.length; i += 1) {
            const key = keys[i];
            if (params[key] && params[key].identificator) {
                return key;
            }
        }
        return null;
    }

    function renderRecords(payload) {
        const container = document.querySelector('.identsContainer');
        if (!container) {
            return;
        }
        const normalised = window.dbmDto.normaliseListPayload(payload);
        const schema = normalised.schema || { parameters: {} };
        const identParamId = findIdentParamId(schema);
        container.innerHTML = '';
        (normalised.records || []).forEach(function (record) {
            const card = document.createElement('div');
            card.className = 'ident col';
            const uid = String(record.record_uid || '');
            card.addEventListener('click', function (evt) {
                clickToIdent(evt, uid);
            });
            let identValue = '';
            if (identParamId && record.fields && record.fields[identParamId]) {
                identValue = record.fields[identParamId].value;
            }
            if (!identValue && record._legacy_ident) {
                identValue = record._legacy_ident;
            }
            if (!identValue) {
                identValue = uid;
            }
            const span = document.createElement('span');
            span.textContent = String(identValue);
            card.appendChild(span);
            container.appendChild(card);
        });
    }

    function fillSelectFromEntries(select, entries, selectFirst) {
        if (!select) {
            return;
        }
        if (window.jQuery && window.jQuery.fn.select2 && window.jQuery(select).data('select2')) {
            window.jQuery(select).select2('destroy');
        }
        select.innerHTML = '';
        entries.forEach(function (entry, index) {
            const option = document.createElement('option');
            option.value = String(entry.value);
            option.textContent = String(entry.label);
            option.selected = !!selectFirst && index === 0;
            select.appendChild(option);
        });
    }

    function showCreateDocumentsModal(payload, documents) {
        const normalised = window.dbmDto.normaliseListPayload(payload);
        const schema = normalised.schema || { parameters: {} };
        const identParamId = findIdentParamId(schema);
        const idents = (normalised.records || []).map(function (record) {
            let label = '';
            if (identParamId && record.fields && record.fields[identParamId]) {
                label = record.fields[identParamId].value;
            }
            return {
                value: record.record_uid,
                label: label || record.record_uid,
            };
        });
        const selectIdents = document.querySelector('select[name="object_idents[]"]');
        const selectDocuments = document.querySelector('select[name="object_documents[]"]');
        fillSelectFromEntries(selectIdents, idents, true);
        fillSelectFromEntries(
            selectDocuments,
            (documents || []).map(function (doc) {
                return { value: doc.id, label: doc.name };
            }),
            false
        );
        if (window.jQuery && window.jQuery.fn.select2) {
            window.jQuery(selectIdents).select2({
                width: '100%',
                dropdownParent: window.jQuery('#createDocumentsModal'),
                multiple: true,
            });
            window.jQuery(selectDocuments).select2({
                width: '100%',
                dropdownParent: window.jQuery('#createDocumentsModal'),
                multiple: true,
            });
        }
        if (window.bootstrap && window.bootstrap.Modal) {
            window.bootstrap.Modal.getOrCreateInstance(document.getElementById('createDocumentsModal')).show();
        }
    }

    function createOption(value, text, selected) {
        const option = document.createElement('option');
        option.value = String(value);
        option.textContent = String(text);
        option.selected = !!selected;
        return option;
    }

    function fillColumnsSelects(columnsRaw) {
        const columns = String(columnsRaw || '')
            .split(';')
            .map(function (item) { return String(item || '').trim(); })
            .filter(Boolean);
        const dropSelect = document.querySelector('#form_update_csv_object select[name="drop_column"]');
        if (dropSelect) {
            dropSelect.innerHTML = '';
            dropSelect.appendChild(createOption('-1', 'Выберите столбец, по которому сбросить строки таблицы', true));
            columns.forEach(function (column) {
                dropSelect.appendChild(createOption(column, column, false));
            });
        }
        const paramSelects = document.querySelectorAll('#form_update_csv_object select.obj_parameter_select_col_csv');
        paramSelects.forEach(function (select, index) {
            select.innerHTML = '';
            select.appendChild(createOption('-1', 'Не заполнять', true));
            columns.forEach(function (column, columnIndex) {
                select.appendChild(createOption(column, column, columnIndex === index));
            });
        });
    }

    function performObjectDelete() {
        const objectId = getObjectId();
        if (!objectId) {
            showAlert('Не удалось определить идентификатор объекта для удаления.');
            return;
        }
        const deleteConfirmButton = document.getElementById('delete-object-confirm-button');
        const deleteObjectModal = document.getElementById('deleteObjectModal');
        if (deleteConfirmButton) {
            deleteConfirmButton.disabled = true;
            deleteConfirmButton.textContent = 'Удаление...';
        }
        fetch('/database/delete_object/' + objectId + '/', {
            method: 'POST',
            headers: {
                'X-CSRFToken': getCsrfToken(),
            },
            body: new FormData(),
        })
            .then(function (response) {
                if (!response.ok) {
                    throw new Error('Не удалось удалить объект.');
                }
                if (deleteObjectModal && window.bootstrap && window.bootstrap.Modal) {
                    const modalInstance = window.bootstrap.Modal.getInstance(deleteObjectModal);
                    if (modalInstance) {
                        modalInstance.hide();
                    }
                }
                window.location.href = '/database/object_manager';
            })
            .catch(function (error) {
                console.error(error);
                showAlert(error.message || 'Не удалось удалить объект.');
                if (deleteConfirmButton) {
                    deleteConfirmButton.disabled = false;
                    deleteConfirmButton.textContent = 'Удалить';
                }
            });
    }

    function initDeleteModal() {
        const deleteModalElement = document.getElementById('deleteObjectModal');
        const deleteInput = document.getElementById('delete-object-confirm-input');
        const deleteConfirmButton = document.getElementById('delete-object-confirm-button');
        const objectNameMeta = document.querySelector('meta[name="object_name"]');
        const objectName =
            (deleteInput ? deleteInput.dataset.objectName : null) ||
            (objectNameMeta ? objectNameMeta.getAttribute('content') : '') ||
            '';

        function resetDeleteModalState() {
            if (deleteInput) {
                deleteInput.value = '';
            }
            if (deleteConfirmButton) {
                deleteConfirmButton.disabled = true;
                deleteConfirmButton.textContent = 'Удалить';
            }
        }

        if (deleteModalElement) {
            deleteModalElement.addEventListener('hidden.bs.modal', resetDeleteModalState);
            deleteModalElement.addEventListener('shown.bs.modal', function () {
                if (deleteInput) {
                    deleteInput.focus();
                }
            });
        }
        if (deleteInput && deleteConfirmButton) {
            deleteInput.addEventListener('input', function () {
                deleteConfirmButton.disabled = deleteInput.value.trim() !== objectName.trim();
            });
        }
        if (deleteConfirmButton) {
            deleteConfirmButton.addEventListener('click', function () {
                if (!deleteConfirmButton.disabled) {
                    performObjectDelete();
                }
            });
        }
    }

    function loadRecords() {
        const objectId = getObjectId();
        if (!objectId || !window.dbmApi || !window.dbmDto) {
            return;
        }
        setLoading(true);
        window.dbmApi
            .listRecords(objectId, {
                limit: 500,
                offset: 0,
                order: 'identificator',
                include_schema: 1,
            })
            .then(function (payload) {
                renderRecords(payload);
            })
            .catch(function (error) {
                console.error(error);
                showAlert(error.message || 'Не удалось загрузить список записей.');
            })
            .finally(function () {
                setLoading(false);
            });
    }

    function updateObjectElement() {
        const objectId = getObjectId();
        if (!objectId || !window.currentIdentId) {
            return;
        }
        window.location.href = '/database/update_element_to_object/' + objectId + '/?id=' + encodeURIComponent(window.currentIdentId);
    }

    function clickToIdent(_evt, identId) {
        window.currentIdentId = identId;
        updateObjectElement();
    }

    function updateObject() {
        const objectId = getObjectId();
        if (!objectId) {
            return;
        }
        window.location.href = '/database/update_object/' + objectId + '/';
    }

    function addElement() {
        const objectId = getObjectId();
        if (!objectId) {
            return;
        }
        window.location.href = '/database/add_element_to_object/' + objectId + '/';
    }

    function updateCSV() {
        const modalElement = document.getElementById('objectUpdateFileModal');
        if (!modalElement || !window.bootstrap || !window.bootstrap.Modal) {
            return;
        }
        window.bootstrap.Modal.getOrCreateInstance(modalElement).show();
    }

    function deleteObject(event) {
        if (event && event.preventDefault) {
            event.preventDefault();
        }
        const deleteModalElement = document.getElementById('deleteObjectModal');
        if (deleteModalElement && window.bootstrap && window.bootstrap.Modal) {
            window.bootstrap.Modal.getOrCreateInstance(deleteModalElement).show();
            return;
        }
        if (window.confirm('Удалить объект? Это действие нельзя отменить.')) {
            performObjectDelete();
        }
    }

    function fileChangerExcelDownload(event) {
        if (event && event.preventDefault) {
            event.preventDefault();
        }
        const objectId = getObjectId();
        if (!objectId) {
            return;
        }
        fetch('/database/file_changer/' + objectId + '/', {
            method: 'POST',
            headers: {
                'X-CSRFToken': getCsrfToken(),
            },
            body: new FormData(),
        })
            .then(function (response) {
                if (!response.ok) {
                    throw new Error('Не удалось получить Excel-файл.');
                }
                return response.blob();
            })
            .then(function (blob) {
                const url = URL.createObjectURL(blob);
                const link = document.createElement('a');
                link.href = url;
                link.download = 'file_changer.xlsx';
                link.click();
                URL.revokeObjectURL(url);
            })
            .catch(function (error) {
                console.error(error);
                showAlert(error.message || 'Не удалось скачать файл.');
            });
    }

    function openModalCreateDocuments(event) {
        if (event && event.preventDefault) {
            event.preventDefault();
        }
        const objectId = getObjectId();
        if (!objectId) {
            return;
        }
        const docs = getDocumentsFromPage();
        window.dbmApi
            .listRecords(objectId, {
                limit: 500,
                offset: 0,
                order: 'identificator',
                include_schema: 1,
            })
            .then(function (payload) {
                showCreateDocumentsModal(payload, docs);
            })
            .catch(function (error) {
                console.error(error);
                showAlert(error.message || 'Не удалось открыть форму создания документов.');
            });
    }

    function initUpdateCsvForm() {
        const form = document.getElementById('form_update_csv_object');
        if (!form) {
            return;
        }
        const fileInput = form.querySelector('input[type="file"][name="csv_file"]');
        const needDropCheckbox = form.querySelector('input[name="need_drop"]');
        const dropColumnBlock = document.getElementById('drop_column_block');
        if (fileInput) {
            fileInput.addEventListener('change', function () {
                fetch('/database/upload_csv_to_get_columns/', {
                    method: 'POST',
                    headers: { 'X-CSRFToken': getCsrfToken() },
                    body: new FormData(form),
                })
                    .then(function (response) {
                        if (!response.ok) {
                            throw new Error('Не удалось прочитать CSV.');
                        }
                        return response.text();
                    })
                    .then(function (columnsRaw) {
                        fillColumnsSelects(columnsRaw);
                        if (dropColumnBlock) {
                            dropColumnBlock.classList.remove('invisible');
                        }
                    })
                    .catch(function (error) {
                        console.error(error);
                        showAlert(error.message || 'Не удалось обработать CSV.');
                    });
            });
        }

        if (needDropCheckbox && dropColumnBlock) {
            needDropCheckbox.addEventListener('change', function () {
                const dropSelect = dropColumnBlock.querySelector('select[name="drop_column"]');
                if (!dropSelect) {
                    return;
                }
                dropSelect.classList.toggle('invisible', !needDropCheckbox.checked);
            });
        }

        form.addEventListener('submit', function (event) {
            event.preventDefault();
            const objectId = getObjectId();
            fetch('/database/update_csv/' + objectId + '/', {
                method: 'POST',
                headers: { 'X-CSRFToken': getCsrfToken() },
                body: new FormData(form),
            })
                .then(function (response) {
                    if (!response.ok) {
                        throw new Error('Не удалось обновить данные из CSV.');
                    }
                    return response.text();
                })
                .then(function () {
                    window.location.reload();
                })
                .catch(function (error) {
                    console.error(error);
                    showAlert(error.message || 'Не удалось обновить данные из CSV.');
                });
        });
    }

    function initCreateDocumentsForm() {
        const form = document.getElementById('form_create_documents');
        if (!form) {
            return;
        }
        form.addEventListener('submit', function (event) {
            event.preventDefault();
            fetch('/document/createDocumentsMultiple', {
                method: 'GET',
                headers: { 'X-CSRFToken': getCsrfToken() },
                body: new FormData(form),
            }).catch(function (error) {
                console.error(error);
            });
        });
    }

    function initObjectPage() {
        if (!window.dbmApi || !window.dbmDto) {
            return;
        }
        const objectId = getObjectId();
        if (!objectId) {
            return;
        }
        initDeleteModal();
        initUpdateCsvForm();
        initCreateDocumentsForm();
        loadRecords();

        window.openModalCreateDocuments = openModalCreateDocuments;
        window.delete_object = deleteObject;
        window.fileChangerExcelDownload = fileChangerExcelDownload;
        window.updateCSV = updateCSV;
        window.updateObjectElement = updateObjectElement;
        window.updateObject = updateObject;
        window.addElement = addElement;
        window.click_to_ident = clickToIdent;
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', initObjectPage);
    } else {
        initObjectPage();
    }
})();
