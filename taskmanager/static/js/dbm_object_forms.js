(function () {
    function readFlag(name, fallbackValue) {
        if (window[name] !== undefined) {
            return !!window[name];
        }
        const meta = document.querySelector('meta[name="' + name.toLowerCase() + '"]');
        if (!meta) {
            return fallbackValue;
        }
        const value = String(meta.getAttribute('content') || '').toLowerCase();
        return value === '1' || value === 'true' || value === 'yes';
    }

    function showAlert(message, type) {
        const host = document.getElementById('dbm-form-alerts');
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

    function setSavingState(form, saving) {
        const submitButtons = form.querySelectorAll('button[type="submit"], button[form="' + form.id + '"]');
        submitButtons.forEach(function (button) {
            if (!button.dataset.originalText) {
                button.dataset.originalText = button.textContent;
            }
            button.disabled = !!saving;
            button.textContent = saving ? 'Saving...' : button.dataset.originalText;
        });
    }

    function shouldUseApiMutations() {
        const uiV1Only = readFlag('DBM_UI_V1_ONLY', false);
        const useApiMutations = readFlag('DBM_UI_USE_API_FOR_MUTATIONS', true);
        const legacyFallbackEnabled = readFlag('DBM_UI_LEGACY_FALLBACK', false);
        if (uiV1Only) {
            return true;
        }
        if (useApiMutations) {
            return true;
        }
        return !legacyFallbackEnabled;
    }

    function getRecordUidFromPage() {
        const meta = document.querySelector('meta[name="record_uid"]');
        if (meta && meta.getAttribute('content')) {
            return meta.getAttribute('content');
        }
        const query = new URLSearchParams(window.location.search);
        return query.get('id') || '';
    }

    function getSelectedValues(select) {
        if (!select) {
            return [];
        }
        if (select.multiple) {
            return Array.from(select.selectedOptions)
                .map(function (opt) { return String(opt.value || '').trim(); })
                .filter(Boolean);
        }
        const value = String(select.value || '').trim();
        return value ? [value] : [];
    }

    function collectLinkSelections(form) {
        const selections = {};
        form.querySelectorAll('.child-link-select[data-link-id]').forEach(function (select) {
            const linkId = String(select.getAttribute('data-link-id') || '').trim();
            if (!linkId) {
                return;
            }
            selections[linkId] = getSelectedValues(select);
        });
        return selections;
    }

    function mapExistingLinks(payload) {
        const result = {};
        const links = payload && Array.isArray(payload.links) ? payload.links : [];
        links.forEach(function (item) {
            const key = String(item.link_meta_id);
            result[key] = new Set((item.child_record_uids || []).map(function (uid) { return String(uid); }));
        });
        return result;
    }

    async function syncLinks(objectId, recordUid, desiredSelections) {
        if (!recordUid) {
            return;
        }
        const existingPayload = await window.dbmApi.getLinks(objectId, recordUid);
        const existingByMeta = mapExistingLinks(existingPayload);
        const desiredMetaIds = Object.keys(desiredSelections);
        for (let i = 0; i < desiredMetaIds.length; i += 1) {
            const metaId = desiredMetaIds[i];
            const desired = new Set((desiredSelections[metaId] || []).map(function (uid) { return String(uid); }));
            const existing = existingByMeta[metaId] || new Set();
            const toCreate = [];
            const toDelete = [];
            desired.forEach(function (uid) {
                if (!existing.has(uid)) {
                    toCreate.push(uid);
                }
            });
            existing.forEach(function (uid) {
                if (!desired.has(uid)) {
                    toDelete.push(uid);
                }
            });
            for (let c = 0; c < toCreate.length; c += 1) {
                await window.dbmApi.createLink(objectId, recordUid, toCreate[c], metaId);
            }
            for (let d = 0; d < toDelete.length; d += 1) {
                await window.dbmApi.deleteLink(objectId, recordUid, toDelete[d], metaId);
            }
        }
    }

    function fetchChildData(childObjId, selectedValue) {
        document
            .querySelectorAll('.child-param-field[data-child-object-id="' + childObjId + '"]')
            .forEach(function (field) {
                field.value = '';
            });
        if (!childObjId || !selectedValue || !window.dbmApi || !window.dbmDto) {
            return;
        }
        window.dbmApi
            .getRecord(childObjId, String(selectedValue))
            .then(function (payload) {
                const data = window.dbmDto.normaliseRecordPayload(payload);
                Object.entries(data.fields || {}).forEach(function (entry) {
                    const key = entry[0];
                    const valueObj = entry[1] || {};
                    let value = valueObj.value;
                    if (Array.isArray(value)) {
                        value = value.join(', ');
                    }
                    const field = document.querySelector(
                        '.child-param-field[data-child-object-id="' + childObjId + '"][data-param-id="' + key + '"]'
                    );
                    if (field) {
                        field.value = value == null ? '' : String(value);
                    }
                });
            })
            .catch(function (error) {
                console.error('Error fetching child data:', error);
            });
    }

    function syncPreviewSelectFromChildSelection(select, selectedValues) {
        const childObjId = String(select.getAttribute('data-child-object-id') || '');
        if (!childObjId) {
            return;
        }
        const previewSelect = document.querySelector('.child-preview-select[data-child-object-id="' + childObjId + '"]');
        if (!previewSelect) {
            if (selectedValues.length > 0) {
                fetchChildData(childObjId, selectedValues[0]);
            } else {
                fetchChildData(childObjId, '');
            }
            return;
        }

        previewSelect.innerHTML = '';
        const placeholderOption = document.createElement('option');
        placeholderOption.value = '';
        placeholderOption.textContent = '-- Не выбрано --';
        previewSelect.appendChild(placeholderOption);
        selectedValues.forEach(function (value) {
            const option = select.querySelector('option[value="' + value.replace(/"/g, '\\"') + '"]');
            const optionElement = document.createElement('option');
            optionElement.value = value;
            optionElement.textContent = option ? option.textContent : value;
            previewSelect.appendChild(optionElement);
        });
        if (selectedValues.length > 0) {
            previewSelect.value = selectedValues[0];
            fetchChildData(childObjId, selectedValues[0]);
        } else {
            fetchChildData(childObjId, '');
        }
        if (window.jQuery && window.jQuery.fn.select2 && window.jQuery(previewSelect).data('select2')) {
            window.jQuery(previewSelect).trigger('change.select2');
        }
    }

    function getSelectValue(select) {
        if (!select) {
            return '';
        }
        if (window.jQuery) {
            return window.jQuery(select).val();
        }
        if (select.multiple) {
            return Array.from(select.options)
                .filter(function (opt) { return opt.selected; })
                .map(function (opt) { return opt.value; });
        }
        return select.value;
    }

    function setSelectValue(select, value) {
        if (!select) {
            return;
        }
        if (window.jQuery) {
            window.jQuery(select).val(value).trigger('change');
            return;
        }
        if (select.multiple && Array.isArray(value)) {
            Array.from(select.options).forEach(function (option) {
                option.selected = value.indexOf(option.value) !== -1;
            });
        } else {
            select.value = Array.isArray(value) ? (value[0] || '') : value;
        }
        select.dispatchEvent(new Event('change'));
    }

    function setupChildPreviewHandlers() {
        document.addEventListener('change', function (evt) {
            const target = evt.target;
            if (!(target instanceof Element)) {
                return;
            }
            if (target.classList.contains('child-link-select')) {
                const childObjId = String(target.getAttribute('data-child-object-id') || '');
                const selectedValues = getSelectedValues(target);
                if (target.multiple) {
                    syncPreviewSelectFromChildSelection(target, selectedValues);
                } else {
                    fetchChildData(childObjId, selectedValues.length ? selectedValues[0] : '');
                }
                const linkedParamSelect = document.querySelector(
                    'select[name^="col_value_"][data-linked-object-id="' + childObjId + '"]'
                );
                if (linkedParamSelect && !target.dataset.syncing) {
                    target.dataset.syncing = '1';
                    setSelectValue(linkedParamSelect, target.multiple ? selectedValues : (selectedValues[0] || ''));
                    target.dataset.syncing = '';
                }
                return;
            }

            if (target.classList.contains('child-preview-select')) {
                const childObjId = String(target.getAttribute('data-child-object-id') || '');
                fetchChildData(childObjId, target.value || '');
                return;
            }

            if (target.matches('select[name^="col_value_"]')) {
                const childObjId = String(target.getAttribute('data-linked-object-id') || '');
                if (!childObjId || target.dataset.syncing) {
                    return;
                }
                const childSelect = document.querySelector('.child-link-select[data-child-object-id="' + childObjId + '"]');
                if (!childSelect) {
                    return;
                }
                target.dataset.syncing = '1';
                const value = getSelectValue(target);
                setSelectValue(childSelect, value);
                target.dataset.syncing = '';
            }
        });

        document.querySelectorAll('.child-link-select').forEach(function (select) {
            const selectedValues = getSelectedValues(select);
            if (select.multiple) {
                syncPreviewSelectFromChildSelection(select, selectedValues);
            } else {
                const childObjId = String(select.getAttribute('data-child-object-id') || '');
                fetchChildData(childObjId, selectedValues.length ? selectedValues[0] : '');
            }
        });
    }

    function setupSelect2AndValidation(form) {
        if (!window.jQuery || !window.jQuery.fn.select2) {
            return;
        }
        window.jQuery('select.form-select').each(function () {
            const placeholder = window.jQuery(this).find('option[value=""]').text() || '';
            const options = {
                width: '100%',
                placeholder: placeholder,
                allowClear: true,
                language: 'ru',
            };
            if (window.jQuery(this).hasClass('select2-tags')) {
                options.tags = true;
            }
            window.jQuery(this).select2(options);
        });

        form.addEventListener('submit', function (evt) {
            const identField = document.querySelector('.identificator-field');
            if (!identField) {
                return;
            }
            let value;
            if (identField.tagName.toLowerCase() === 'select') {
                value = getSelectValue(identField);
            } else {
                value = identField.value;
            }
            const isEmpty =
                !value ||
                (Array.isArray(value) && value.length === 0) ||
                (typeof value === 'string' && value.trim() === '');
            if (isEmpty) {
                evt.preventDefault();
                showAlert('Пожалуйста, заполните идентификатор (обязательное поле).', 'danger');
            }
        });
    }

    function setupLegacyDeleteButton(csrfToken) {
        const deleteButton = document.getElementById('delete-element-button');
        if (!deleteButton || !deleteButton.dataset.deleteUrl) {
            return;
        }
        const deleteUrl = deleteButton.dataset.deleteUrl;
        const redirectUrl = deleteButton.dataset.redirectUrl || '';
        deleteButton.addEventListener('click', function () {
            if (deleteButton.disabled) {
                return;
            }
            if (!window.confirm('Удалить выбранную запись? Действие необратимо.')) {
                return;
            }
            deleteButton.disabled = true;
            deleteButton.classList.add('disabled');
            fetch(deleteUrl, {
                method: 'POST',
                headers: { 'X-CSRFToken': csrfToken },
            })
                .then(function (response) {
                    if (!response.ok) {
                        throw new Error('Не удалось удалить запись.');
                    }
                    if (redirectUrl) {
                        window.location.href = redirectUrl;
                    }
                })
                .catch(function (error) {
                    console.error(error);
                    deleteButton.disabled = false;
                    deleteButton.classList.remove('disabled');
                    showAlert('Не удалось удалить запись. Попробуйте позже.', 'danger');
                });
        });
    }

    function initForm() {
        if (!window.dbmApi || !window.dbmDto) {
            return;
        }
        const form = document.getElementById('form_add_data_to_object') || document.getElementById('form_update_data_to_object');
        if (!form) {
            return;
        }
        const csrfInput = form.querySelector('[name="csrfmiddlewaretoken"]');
        const csrfToken = csrfInput ? csrfInput.value : '';
        setupSelect2AndValidation(form);
        setupChildPreviewHandlers();

        const useApiMutations = shouldUseApiMutations();
        if (!useApiMutations) {
            setupLegacyDeleteButton(csrfToken);
            return;
        }

        const objectIdMeta = document.querySelector('meta[name="object_id"]');
        const objectId = objectIdMeta ? String(objectIdMeta.getAttribute('content') || '').trim() : '';
        if (!objectId) {
            return;
        }
        const isUpdate = form.id === 'form_update_data_to_object';
        let schemaCache = null;

        async function ensureSchema() {
            if (schemaCache) {
                return schemaCache;
            }
            const payload = await window.dbmApi.listRecords(objectId, {
                limit: 1,
                offset: 0,
                order: 'updated_at',
                include_schema: 1,
            });
            schemaCache = payload.schema || { object_id: Number(objectId), parameters: {} };
            return schemaCache;
        }

        form.addEventListener(
            'submit',
            async function (evt) {
                evt.preventDefault();
                evt.stopPropagation();
                setSavingState(form, true);
                try {
                    const schema = await ensureSchema();
                    const fields = window.dbmDto.fieldsFromForm(form, schema);
                    const linkSelections = collectLinkSelections(form);
                    let savedRecordUid = '';
                    if (isUpdate) {
                        const recordUid = getRecordUidFromPage();
                        if (!recordUid) {
                            throw new Error('Не указан идентификатор записи для обновления.');
                        }
                        const updatePayload = await window.dbmApi.updateRecord(objectId, recordUid, fields);
                        const normalised = window.dbmDto.normaliseRecordPayload(updatePayload);
                        savedRecordUid = normalised.record_uid || recordUid;
                    } else {
                        const createPayload = await window.dbmApi.createRecord(objectId, fields);
                        const normalised = window.dbmDto.normaliseRecordPayload(createPayload);
                        savedRecordUid = normalised.record_uid;
                    }
                    if (savedRecordUid) {
                        await syncLinks(objectId, savedRecordUid, linkSelections);
                    }
                    showAlert('Сохранение выполнено.', 'success');
                    window.setTimeout(function () {
                        window.location.href = '/database/get_object/' + objectId + '/';
                    }, 350);
                } catch (error) {
                    console.error(error);
                    showAlert(error.message || 'Не удалось сохранить запись.', 'danger');
                } finally {
                    setSavingState(form, false);
                }
            },
            true
        );

        const deleteButton = document.getElementById('delete-element-button');
        if (deleteButton && isUpdate) {
            deleteButton.addEventListener(
                'click',
                async function (evt) {
                    evt.preventDefault();
                    evt.stopPropagation();
                    evt.stopImmediatePropagation();
                    const recordUid = getRecordUidFromPage();
                    if (!recordUid) {
                        showAlert('Не указан идентификатор записи для удаления.', 'danger');
                        return;
                    }
                    if (!window.confirm('Удалить выбранную запись? Действие необратимо.')) {
                        return;
                    }
                    deleteButton.disabled = true;
                    try {
                        await window.dbmApi.deleteRecord(objectId, recordUid);
                        window.location.href = '/database/get_object/' + objectId + '/';
                    } catch (error) {
                        console.error(error);
                        deleteButton.disabled = false;
                        showAlert(error.message || 'Не удалось удалить запись.', 'danger');
                    }
                },
                true
            );
        }
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', initForm);
    } else {
        initForm();
    }
})();
