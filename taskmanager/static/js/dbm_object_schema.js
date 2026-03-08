(function () {
    function init() {
        if (!window.dbmApi) {
            return;
        }
        const form = document.getElementById('update_object_form');
        const tableBody = document.getElementById('link-meta-table-body');
        if (!form || !tableBody) {
            return;
        }
        const objectId = String(form.getAttribute('data-objectId') || '').trim();
        if (!objectId) {
            return;
        }

        const alertsHost = document.getElementById('link-meta-alerts');
        const modalEl = document.getElementById('linkMetaCreateModal');
        const createBtn = document.getElementById('link-meta-create-open');
        const createSubmitBtn = document.getElementById('link-meta-create-submit');
        const createDisplayInput = document.getElementById('link-meta-display-name');
        const createCodeInput = document.getElementById('link-meta-code');
        const createChildSelect = document.getElementById('link-meta-child-object');
        const createTypeSelect = document.getElementById('link-meta-link-type');
        const createOrderInput = document.getElementById('link-meta-order');

        const state = {
            metas: [],
            editingMetaId: null,
            createCodeTouched: false,
        };

        function showAlert(message, level) {
            if (!alertsHost) {
                return;
            }
            alertsHost.innerHTML = '';
            const el = document.createElement('div');
            el.className = 'alert alert-' + (level || 'danger') + ' py-2';
            el.setAttribute('role', 'alert');
            el.textContent = message;
            alertsHost.appendChild(el);
        }

        function clearAlert() {
            if (alertsHost) {
                alertsHost.innerHTML = '';
            }
        }

        function translitToCode(source) {
            const map = {
                а: 'a', б: 'b', в: 'v', г: 'g', д: 'd', е: 'e', ё: 'e', ж: 'zh', з: 'z', и: 'i',
                й: 'y', к: 'k', л: 'l', м: 'm', н: 'n', о: 'o', п: 'p', р: 'r', с: 's', т: 't',
                у: 'u', ф: 'f', х: 'h', ц: 'c', ч: 'ch', ш: 'sh', щ: 'sch', ъ: '', ы: 'y', ь: '',
                э: 'e', ю: 'yu', я: 'ya',
            };
            const input = String(source || '').toLowerCase();
            let raw = '';
            for (let i = 0; i < input.length; i += 1) {
                const char = input[i];
                raw += map.hasOwnProperty(char) ? map[char] : char;
            }
            raw = raw.replace(/[^a-z0-9]+/gi, '_').replace(/_+/g, '_').replace(/^_+|_+$/g, '');
            return (raw || 'LINK').toUpperCase();
        }

        function getChildOptions() {
            if (!createChildSelect) {
                return [];
            }
            return Array.from(createChildSelect.options)
                .filter(function (opt) { return String(opt.value || '').trim() !== ''; })
                .map(function (opt) {
                    return { value: String(opt.value), label: String(opt.textContent || '').trim() };
                });
        }

        function buildChildSelect(selectedValue, disabled) {
            const select = document.createElement('select');
            select.className = 'form-select form-select-sm';
            select.setAttribute('data-field', 'child_object_id');
            if (disabled) {
                select.disabled = true;
            }
            getChildOptions().forEach(function (opt) {
                const option = document.createElement('option');
                option.value = opt.value;
                option.textContent = opt.label;
                if (String(selectedValue) === String(opt.value)) {
                    option.selected = true;
                }
                select.appendChild(option);
            });
            return select;
        }

        function isDuplicateCode(code, currentId) {
            const value = String(code || '').trim().toUpperCase();
            if (!value) {
                return false;
            }
            return state.metas.some(function (meta) {
                if (currentId && Number(meta.id) === Number(currentId)) {
                    return false;
                }
                return String(meta.code || '').trim().toUpperCase() === value;
            });
        }

        function isDuplicateDisplay(displayName, currentId) {
            const value = String(displayName || '').trim().toLowerCase();
            if (!value) {
                return false;
            }
            return state.metas.some(function (meta) {
                if (currentId && Number(meta.id) === Number(currentId)) {
                    return false;
                }
                return String(meta.display_name || '').trim().toLowerCase() === value;
            });
        }

        function setRowEditMode(row, isEditing) {
            const editable = row.querySelectorAll('input[data-field], select[data-field]');
            editable.forEach(function (field) {
                field.disabled = !isEditing;
            });
            const editBtn = row.querySelector('[data-action=\"edit\"]');
            const saveBtn = row.querySelector('[data-action=\"save\"]');
            const cancelBtn = row.querySelector('[data-action=\"cancel\"]');
            if (editBtn) {
                editBtn.classList.toggle('d-none', isEditing);
            }
            if (saveBtn) {
                saveBtn.classList.toggle('d-none', !isEditing);
            }
            if (cancelBtn) {
                cancelBtn.classList.toggle('d-none', !isEditing);
            }
        }

        function renderRows() {
            tableBody.innerHTML = '';
            if (!state.metas.length) {
                const tr = document.createElement('tr');
                tr.innerHTML = '<td colspan=\"7\" class=\"text-muted\">Связи-роли пока не созданы.</td>';
                tableBody.appendChild(tr);
                return;
            }
            state.metas.forEach(function (meta) {
                const tr = document.createElement('tr');
                tr.setAttribute('data-meta-id', String(meta.id));

                const orderTd = document.createElement('td');
                const orderInput = document.createElement('input');
                orderInput.type = 'number';
                orderInput.className = 'form-control form-control-sm';
                orderInput.value = String(meta.order == null ? 0 : meta.order);
                orderInput.setAttribute('data-field', 'order');
                orderInput.disabled = true;
                orderTd.appendChild(orderInput);

                const displayTd = document.createElement('td');
                const displayInput = document.createElement('input');
                displayInput.type = 'text';
                displayInput.className = 'form-control form-control-sm';
                displayInput.value = String(meta.display_name || '');
                displayInput.setAttribute('data-field', 'display_name');
                displayInput.disabled = true;
                displayTd.appendChild(displayInput);

                const codeTd = document.createElement('td');
                const codeInput = document.createElement('input');
                codeInput.type = 'text';
                codeInput.className = 'form-control form-control-sm';
                codeInput.value = String(meta.code || '');
                codeInput.setAttribute('data-field', 'code');
                codeInput.disabled = true;
                codeTd.appendChild(codeInput);

                const childTd = document.createElement('td');
                childTd.appendChild(buildChildSelect(meta.child_object_id, true));

                const typeTd = document.createElement('td');
                const typeSelect = document.createElement('select');
                typeSelect.className = 'form-select form-select-sm';
                typeSelect.setAttribute('data-field', 'link_type');
                typeSelect.disabled = true;
                ['single', 'multiple'].forEach(function (optionValue) {
                    const option = document.createElement('option');
                    option.value = optionValue;
                    option.textContent = optionValue;
                    if (optionValue === String(meta.link_type || 'single')) {
                        option.selected = true;
                    }
                    typeSelect.appendChild(option);
                });
                typeTd.appendChild(typeSelect);

                const linkParamTd = document.createElement('td');
                const linkParam = meta.link_parameter || null;
                if (linkParam && linkParam.id) {
                    const title = linkParam.name + ' (id=' + linkParam.id + ')';
                    linkParamTd.textContent = title;
                    linkParamTd.title = title;
                    if (linkParam.is_managed) {
                        const badge = document.createElement('span');
                        badge.className = 'badge bg-light text-secondary ms-2';
                        badge.textContent = 'managed';
                        linkParamTd.appendChild(badge);
                    }
                } else {
                    linkParamTd.innerHTML = '<span class=\"text-muted\">Не привязан</span>';
                }

                const actionsTd = document.createElement('td');
                actionsTd.innerHTML = ''
                    + '<button type=\"button\" class=\"btn btn-sm btn-outline-secondary me-1\" data-action=\"edit\">Изменить</button>'
                    + '<button type=\"button\" class=\"btn btn-sm btn-primary me-1 d-none\" data-action=\"save\">Сохранить</button>'
                    + '<button type=\"button\" class=\"btn btn-sm btn-outline-secondary me-1 d-none\" data-action=\"cancel\">Отмена</button>'
                    + '<button type=\"button\" class=\"btn btn-sm btn-outline-danger\" data-action=\"delete\">Удалить</button>';

                tr.appendChild(orderTd);
                tr.appendChild(displayTd);
                tr.appendChild(codeTd);
                tr.appendChild(childTd);
                tr.appendChild(typeTd);
                tr.appendChild(linkParamTd);
                tr.appendChild(actionsTd);
                tableBody.appendChild(tr);
            });
        }

        function resetCreateForm() {
            if (createDisplayInput) {
                createDisplayInput.value = '';
            }
            if (createCodeInput) {
                createCodeInput.value = '';
            }
            if (createChildSelect) {
                createChildSelect.value = '';
            }
            if (createTypeSelect) {
                createTypeSelect.value = 'single';
            }
            if (createOrderInput) {
                createOrderInput.value = '0';
            }
            state.createCodeTouched = false;
        }

        function loadRows() {
            tableBody.innerHTML = '<tr><td colspan=\"7\" class=\"text-muted\">Загрузка...</td></tr>';
            return window.dbmApi.listLinksMeta(objectId)
                .then(function (payload) {
                    state.metas = Array.isArray(payload.links_meta) ? payload.links_meta.slice() : [];
                    state.metas.sort(function (left, right) {
                        if (Number(left.order || 0) !== Number(right.order || 0)) {
                            return Number(left.order || 0) - Number(right.order || 0);
                        }
                        return Number(left.id) - Number(right.id);
                    });
                    renderRows();
                })
                .catch(function (error) {
                    tableBody.innerHTML = '<tr><td colspan=\"7\" class=\"text-danger\">Не удалось загрузить связи-мета.</td></tr>';
                    showAlert(error.message || 'Не удалось загрузить связи-мета.', 'danger');
                });
        }

        function readRowPayload(row) {
            const payload = {
                display_name: String((row.querySelector('[data-field=\"display_name\"]') || {}).value || '').trim(),
                code: String((row.querySelector('[data-field=\"code\"]') || {}).value || '').trim(),
                child_object_id: String((row.querySelector('[data-field=\"child_object_id\"]') || {}).value || '').trim(),
                link_type: String((row.querySelector('[data-field=\"link_type\"]') || {}).value || 'single').trim(),
                order: String((row.querySelector('[data-field=\"order\"]') || {}).value || '0').trim(),
            };
            payload.order = payload.order === '' ? 0 : Number(payload.order);
            return payload;
        }

        tableBody.addEventListener('click', function (event) {
            const actionEl = event.target.closest('[data-action]');
            if (!actionEl) {
                return;
            }
            const row = event.target.closest('tr[data-meta-id]');
            if (!row) {
                return;
            }
            const metaId = Number(row.getAttribute('data-meta-id'));
            const action = actionEl.getAttribute('data-action');
            if (action === 'edit') {
                clearAlert();
                state.editingMetaId = metaId;
                setRowEditMode(row, true);
                return;
            }
            if (action === 'cancel') {
                clearAlert();
                state.editingMetaId = null;
                renderRows();
                return;
            }
            if (action === 'save') {
                const payload = readRowPayload(row);
                if (!payload.display_name) {
                    showAlert('Введите display_name.', 'danger');
                    return;
                }
                if (!payload.code) {
                    showAlert('Введите code.', 'danger');
                    return;
                }
                if (!payload.child_object_id) {
                    showAlert('Выберите дочерний объект.', 'danger');
                    return;
                }
                if (isDuplicateDisplay(payload.display_name, metaId)) {
                    showAlert('display_name уже используется для текущего объекта.', 'danger');
                    return;
                }
                if (isDuplicateCode(payload.code, metaId)) {
                    showAlert('code уже используется для текущего объекта.', 'danger');
                    return;
                }
                window.dbmApi.updateLinkMeta(objectId, metaId, payload)
                    .then(function () {
                        clearAlert();
                        state.editingMetaId = null;
                        return loadRows();
                    })
                    .catch(function (error) {
                        showAlert(error.message || 'Не удалось сохранить связь-роль.', 'danger');
                    });
                return;
            }
            if (action === 'delete') {
                if (!window.confirm('Удалить связь-роль?')) {
                    return;
                }
                window.dbmApi.deleteLinkMeta(objectId, metaId)
                    .then(function () {
                        clearAlert();
                        return loadRows();
                    })
                    .catch(function (error) {
                        showAlert(error.message || 'Не удалось удалить связь-роль.', 'danger');
                    });
            }
        });

        if (createDisplayInput) {
            createDisplayInput.addEventListener('input', function () {
                if (!state.createCodeTouched && createCodeInput) {
                    createCodeInput.value = translitToCode(createDisplayInput.value || '');
                }
            });
        }
        if (createCodeInput) {
            createCodeInput.addEventListener('input', function () {
                state.createCodeTouched = true;
            });
        }

        if (createBtn && modalEl && window.bootstrap && window.bootstrap.Modal) {
            createBtn.addEventListener('click', function () {
                clearAlert();
                resetCreateForm();
                window.bootstrap.Modal.getOrCreateInstance(modalEl).show();
            });
        }

        if (createSubmitBtn) {
            createSubmitBtn.addEventListener('click', function () {
                const payload = {
                    display_name: String((createDisplayInput && createDisplayInput.value) || '').trim(),
                    code: String((createCodeInput && createCodeInput.value) || '').trim(),
                    child_object_id: String((createChildSelect && createChildSelect.value) || '').trim(),
                    link_type: String((createTypeSelect && createTypeSelect.value) || 'single').trim(),
                    order: Number(String((createOrderInput && createOrderInput.value) || '0').trim() || 0),
                };
                if (!payload.display_name) {
                    showAlert('Введите название роли.', 'danger');
                    return;
                }
                if (!payload.code) {
                    payload.code = translitToCode(payload.display_name);
                }
                if (!payload.child_object_id) {
                    showAlert('Выберите дочерний объект.', 'danger');
                    return;
                }
                if (isDuplicateDisplay(payload.display_name, null)) {
                    showAlert('display_name уже используется для текущего объекта.', 'danger');
                    return;
                }
                if (isDuplicateCode(payload.code, null)) {
                    showAlert('code уже используется для текущего объекта.', 'danger');
                    return;
                }
                window.dbmApi.createLinkMeta(objectId, payload)
                    .then(function () {
                        clearAlert();
                        if (modalEl && window.bootstrap && window.bootstrap.Modal) {
                            window.bootstrap.Modal.getOrCreateInstance(modalEl).hide();
                        }
                        resetCreateForm();
                        return loadRows();
                    })
                    .catch(function (error) {
                        showAlert(error.message || 'Не удалось создать связь-роль.', 'danger');
                    });
            });
        }

        window.dbmObjectSchema = {
            reloadLinkMeta: loadRows,
        };
        loadRows();
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
