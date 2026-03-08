(function () {
    let childPickerInstance = null;
    const linkedOptionsCache = new Map();
    const childParamHydrationSeq = new Map();
    const childParamHydrationLastUid = new Map();
    const childRecordValueCache = new Map();
    const linkInteractionState = new Map();
    let linkHydrationInProgress = false;
    let liveLinkSyncTimer = null;
    let paramSelect2HandlersBound = false;

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

    function isDevGuardEnabled() {
        return readFlag('DOCUMENT_LINK_TREE_DEBUG', false);
    }

    function isFormsDebugEnabled() {
        return readFlag('DBM_OBJECT_FORMS_DEBUG', false);
    }

    function debugLogFieldSnapshot(form, stage) {
        if (!isFormsDebugEnabled() || !form) {
            return;
        }
        try {
            const summary = {
                stage: String(stage || ''),
                recordUid: String(getRecordUidFromPage() || ''),
                simpleInputs: [],
                childParamFields: [],
                linkParams: [],
                childLinks: [],
            };
            Array.from(form.querySelectorAll('input[name^="col_value_"], textarea[name^="col_value_"]'))
                .slice(0, 12)
                .forEach(function (el) {
                    summary.simpleInputs.push({
                        name: el.name,
                        value: String(el.value || ''),
                        disabled: !!el.disabled,
                        readonly: !!el.readOnly,
                    });
                });
            Array.from(form.querySelectorAll('.child-param-field'))
                .slice(0, 20)
                .forEach(function (el) {
                    summary.childParamFields.push({
                        childObjectId: String(el.getAttribute('data-child-object-id') || ''),
                        linkMetaId: String(el.getAttribute('data-link-meta-id') || ''),
                        paramId: String(el.getAttribute('data-param-id') || ''),
                        value: String(el.value || ''),
                        disabled: !!el.disabled,
                    });
                });
            Array.from(form.querySelectorAll('select[name^="col_value_"][data-linked-object-id], select[name^="col_value_"][data-link-meta-id]'))
                .forEach(function (el) {
                    summary.linkParams.push({
                        name: el.name,
                        linkMetaId: String(getLinkMetaIdFromNode(el) || ''),
                        linkedObjectId: String(el.getAttribute('data-linked-object-id') || ''),
                        selected: getSelectedValues(el),
                        dataSelectedValues: String(el.getAttribute('data-selected-values') || ''),
                    });
                });
            Array.from(form.querySelectorAll('.child-link-select'))
                .forEach(function (el) {
                    summary.childLinks.push({
                        name: el.name,
                        linkMetaId: String(getLinkMetaIdFromNode(el) || ''),
                        childObjectId: String(el.getAttribute('data-child-object-id') || ''),
                        selected: getSelectedValues(el),
                        dataSelectedValues: String(el.getAttribute('data-selected-values') || ''),
                    });
                });
            console.log('[dbm_object_forms] snapshot', summary);
        } catch (error) {
            console.warn('[dbm_object_forms] snapshot failed', error);
        }
    }

    function debugLogLinkSelections(form, stage, selections) {
        if (!isFormsDebugEnabled() || !form) {
            return;
        }
        const payload = {
            stage: String(stage || ''),
            selections: selections || {},
            childLinkValues: [],
            paramLinkValues: [],
        };
        form.querySelectorAll('.child-link-select').forEach(function (select) {
            payload.childLinkValues.push({
                name: select.name,
                linkMetaId: getLinkMetaIdFromNode(select),
                selected: getSelectedValues(select),
            });
        });
        form.querySelectorAll('select[name^="col_value_"][data-link-meta-id], select[name^="col_value_"][data-linked-object-id]').forEach(function (select) {
            payload.paramLinkValues.push({
                name: select.name,
                linkMetaId: getLinkMetaIdFromNode(select),
                selected: getSelectedValues(select),
            });
        });
        console.log('[dbm_object_forms] link selections', payload);
    }

    function debugLogSubmitSnapshot(form, selections) {
        if (!isFormsDebugEnabled() || !form) {
            return;
        }
        const snapshot = [];
        form.querySelectorAll('.child-link-select').forEach(function (childSelect) {
            const linkMetaId = getLinkMetaIdFromNode(childSelect);
            if (!linkMetaId) {
                return;
            }
            const paramSelect = getLinkedParamSelectForChildSelect(childSelect);
            const childValues = normaliseSelectionForCompare(childSelect);
            const paramValues = paramSelect ? normaliseSelectionForCompare(paramSelect) : [];
            const payloadValues = Array.isArray(selections && selections[linkMetaId]) ? selections[linkMetaId] : [];
            snapshot.push({
                linkMetaId: linkMetaId,
                child_uid: childValues.length ? childValues[0] : '',
                param_uid: paramValues.length ? paramValues[0] : '',
                final_payload_uid: payloadValues.length ? String(payloadValues[0] || '') : '',
            });
        });
        console.log('[dbm_object_forms] submit snapshot', snapshot);
    }

    function emitLinksChanged(detail) {
        const payload = detail && typeof detail === 'object' ? detail : {};
        try {
            if (window.docUiEvents && typeof window.docUiEvents.dispatchEvent === 'function') {
                window.docUiEvents.dispatchEvent(new CustomEvent('dbm:links-changed', { detail: payload }));
            }
        } catch (_error) {
            // no-op
        }
        try {
            document.dispatchEvent(new CustomEvent('dbm:links-changed', { detail: payload }));
        } catch (_error) {
            // no-op
        }
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

    function normaliseSelectedValueList(rawValue) {
        if (Array.isArray(rawValue)) {
            return rawValue
                .map(function (item) { return String(item || '').trim(); })
                .filter(Boolean);
        }
        const scalar = String(rawValue || '').trim();
        return scalar ? [scalar] : [];
    }

    function getSelectedValues(select) {
        if (!select) {
            return [];
        }
        if (window.jQuery) {
            const jq = window.jQuery(select);
            if (jq && jq.data('select2')) {
                return normaliseSelectedValueList(jq.val());
            }
        }
        if (select.multiple) {
            return Array.from(select.selectedOptions)
                .map(function (opt) { return String(opt.value || '').trim(); })
                .filter(Boolean);
        }
        const value = String(select.value || '').trim();
        return value ? [value] : [];
    }

    function getFallbackSelectedValues(select) {
        if (!select) {
            return [];
        }
        const selectedValues = getSelectedValues(select);
        if (selectedValues.length) {
            return selectedValues;
        }
        const raw = String(select.getAttribute('data-selected-values') || '').trim();
        if (!raw) {
            return [];
        }
        return raw
            .split('|')
            .map(function (item) { return String(item || '').trim(); })
            .filter(Boolean);
    }

    function getLinkMetaIdFromNode(node) {
        if (!node) {
            return '';
        }
        return String(
            node.getAttribute('data-link-meta-id')
            || node.getAttribute('data-link-id')
            || ''
        ).trim();
    }

    function extractParamIdFromFieldName(fieldName) {
        const raw = String(fieldName || '');
        const match = raw.match(/^col_value_(\d+)\[\]$/);
        return match ? String(match[1]) : '';
    }

    function rememberLinkInteraction(linkMetaId, source) {
        const key = String(linkMetaId || '').trim();
        if (!key || !source) {
            return;
        }
        linkInteractionState.set(key, {
            source: String(source),
            at: Date.now(),
        });
    }

    function getPreferredPairSource(linkMetaId) {
        const key = String(linkMetaId || '').trim();
        if (!key) {
            return '';
        }
        const state = linkInteractionState.get(key);
        return state && state.source ? String(state.source) : '';
    }

    function getSelectOptionLabel(select, value) {
        if (!select) {
            return '';
        }
        const valueStr = String(value || '');
        const option = Array.from(select.options).find(function (item) {
            return String(item.value || '') === valueStr;
        });
        return option ? String(option.textContent || '').trim() : '';
    }

    function findUniqueBySelector(selector) {
        const nodes = Array.from(document.querySelectorAll(selector));
        return nodes.length === 1 ? nodes[0] : null;
    }

    function collectLinkSelections(form) {
        const selections = {};
        form.querySelectorAll('.child-link-select').forEach(function (select) {
            const linkId = getLinkMetaIdFromNode(select);
            if (!linkId) {
                return;
            }
            const mirrorParam = getLinkedParamSelectForChildSelect(select);
            if (mirrorParam) {
                enforcePairInvariant('submit', select, mirrorParam);
            }
            let selectedValues = normaliseSelectionForCompare(select);
            if (!selectedValues.length) {
                const mirrorValues = mirrorParam ? normaliseSelectionForCompare(mirrorParam) : [];
                if (mirrorValues.length) {
                    selectedValues = select.multiple ? mirrorValues : [mirrorValues[0]];
                    if (isFormsDebugEnabled()) {
                        console.warn('[dbm_object_forms] collectLinkSelections fallback from param select', {
                            linkMetaId: linkId,
                            childSelectName: select.name,
                            paramSelectName: mirrorParam ? mirrorParam.name : '',
                            values: selectedValues,
                        });
                    }
                }
            }
            selections[linkId] = selectedValues;
        });
        emitLinksChanged({
            source: 'collectLinkSelections',
            metaCount: Object.keys(selections).length,
        });
        debugLogLinkSelections(form, 'collectLinkSelections', selections);
        debugLogSubmitSnapshot(form, selections);
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
        emitLinksChanged({
            source: 'syncLinks',
            objectId: String(objectId || ''),
            recordUid: String(recordUid || ''),
            metaCount: desiredMetaIds.length,
        });
    }

    function getChildParamFields(childObjId, linkMetaId) {
        const baseSelector = '.child-param-field[data-child-object-id="' + childObjId + '"]';
        const scopedSelector = linkMetaId ? baseSelector + '[data-link-meta-id="' + String(linkMetaId).trim() + '"]' : baseSelector;
        const scoped = Array.from(document.querySelectorAll(scopedSelector));
        if (scoped.length || linkMetaId) {
            return scoped;
        }
        return Array.from(document.querySelectorAll(baseSelector));
    }

    function clearChildParamFields(childObjId, linkMetaId) {
        if (isFormsDebugEnabled()) {
            console.log('[dbm_object_forms] clearChildParamFields', {
                childObjectId: String(childObjId || ''),
                linkMetaId: String(linkMetaId || ''),
            });
        }
        getChildParamFields(childObjId, linkMetaId).forEach(function (field) {
            field.value = '';
        });
    }

    function applyChildParamFields(childObjId, fields, linkMetaId) {
        const scopedFields = getChildParamFields(childObjId, linkMetaId);
        clearChildParamFields(childObjId, linkMetaId);
        Object.entries(fields || {}).forEach(function (entry) {
            const key = entry[0];
            const valueObj = entry[1] || {};
            let value = valueObj.value;
            if (Array.isArray(value)) {
                value = value.join(', ');
            }
            const field = scopedFields.find(function (item) {
                return String(item.getAttribute('data-param-id') || '') === String(key);
            });
            if (field) {
                field.value = value == null ? '' : String(value);
            }
        });
    }

    async function hydrateChildParamFields(childObjectId, selectedUid, linkMetaId) {
        const childObjId = String(childObjectId || '').trim();
        const recordUid = String(selectedUid || '').trim();
        if (isFormsDebugEnabled()) {
            console.log('[dbm_object_forms] hydrateChildParamFields:start', {
                childObjectId: childObjId,
                linkMetaId: String(linkMetaId || ''),
                recordUid: recordUid,
            });
        }
        if (!childObjId) {
            return;
        }
        const scopeKey = childObjId + ':' + String(linkMetaId || '').trim();
        const fields = getChildParamFields(childObjId, linkMetaId);
        if (!fields.length) {
            return;
        }
        if (!recordUid) {
            childParamHydrationLastUid.set(scopeKey, '');
            clearChildParamFields(childObjId, linkMetaId);
            emitLinksChanged({
                source: 'hydrateChildParamFields',
                event: 'dbm:child-data-hydrated',
                childObjectId: childObjId,
                linkMetaId: String(linkMetaId || ''),
                recordUid: '',
                status: 'cleared',
            });
            try {
                if (window.docUiEvents && typeof window.docUiEvents.dispatchEvent === 'function') {
                    window.docUiEvents.dispatchEvent(new CustomEvent('dbm:child-data-hydrated', {
                        detail: {
                            childObjectId: childObjId,
                            linkMetaId: String(linkMetaId || ''),
                            recordUid: '',
                            status: 'cleared',
                        },
                    }));
                }
            } catch (_error) {
                // no-op
            }
            return;
        }
        if (childParamHydrationLastUid.get(scopeKey) === recordUid) {
            return;
        }
        const nextRequestId = (childParamHydrationSeq.get(scopeKey) || 0) + 1;
        childParamHydrationSeq.set(scopeKey, nextRequestId);
        const cacheKey = childObjId + ':' + recordUid;
        try {
            let normalised = childRecordValueCache.get(cacheKey);
            if (!normalised) {
                const payload = await window.dbmApi.getRecord(childObjId, recordUid);
                normalised = window.dbmDto.normaliseRecordPayload(payload);
                childRecordValueCache.set(cacheKey, normalised);
            }
            if (childParamHydrationSeq.get(scopeKey) !== nextRequestId) {
                return;
            }
            applyChildParamFields(childObjId, normalised.fields || {}, linkMetaId);
            childParamHydrationLastUid.set(scopeKey, recordUid);
            if (isFormsDebugEnabled()) {
                console.log('[dbm_object_forms] hydrateChildParamFields:ok', {
                    childObjectId: childObjId,
                    linkMetaId: String(linkMetaId || ''),
                    recordUid: recordUid,
                    fieldCount: Object.keys(normalised.fields || {}).length,
                });
            }
            try {
                if (window.docUiEvents && typeof window.docUiEvents.dispatchEvent === 'function') {
                    window.docUiEvents.dispatchEvent(new CustomEvent('dbm:child-data-hydrated', {
                        detail: {
                            childObjectId: childObjId,
                            linkMetaId: String(linkMetaId || ''),
                            recordUid: recordUid,
                            status: 'ok',
                        },
                    }));
                }
            } catch (_error) {
                // no-op
            }
        } catch (error) {
            if (childParamHydrationSeq.get(scopeKey) !== nextRequestId) {
                return;
            }
            clearChildParamFields(childObjId, linkMetaId);
            console.error('Error hydrating child fields:', childObjId, recordUid, error);
            if (isFormsDebugEnabled()) {
                console.warn('[dbm_object_forms] hydrateChildParamFields:error', {
                    childObjectId: childObjId,
                    linkMetaId: String(linkMetaId || ''),
                    recordUid: recordUid,
                    error: error && error.message ? error.message : String(error),
                });
            }
            try {
                if (window.docUiEvents && typeof window.docUiEvents.dispatchEvent === 'function') {
                    window.docUiEvents.dispatchEvent(new CustomEvent('dbm:child-data-hydrated', {
                        detail: {
                            childObjectId: childObjId,
                            linkMetaId: String(linkMetaId || ''),
                            recordUid: recordUid,
                            status: 'error',
                        },
                    }));
                }
            } catch (_error) {
                // no-op
            }
        }
    }

    function syncPreviewSelectFromChildSelection(select, selectedValues) {
        const childObjId = String(select.getAttribute('data-child-object-id') || '');
        const linkMetaId = getLinkMetaIdFromNode(select);
        if (!childObjId) {
            return;
        }
        const previewSelect = document.querySelector(
            '.child-preview-select[data-child-object-id="' + childObjId + '"][data-link-meta-id="' + String(linkMetaId || '') + '"]'
        ) || document.querySelector('.child-preview-select[data-child-object-id="' + childObjId + '"]');
        if (!previewSelect) {
            if (selectedValues.length > 0) {
                hydrateChildParamFields(childObjId, selectedValues[0], linkMetaId);
            } else {
                hydrateChildParamFields(childObjId, '', linkMetaId);
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
            hydrateChildParamFields(childObjId, selectedValues[0], linkMetaId);
        } else {
            hydrateChildParamFields(childObjId, '', linkMetaId);
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

    function ensureOptionExists(select, value, label) {
        if (!select) {
            return;
        }
        const valueStr = String(value || '');
        let option = Array.from(select.options).find(function (item) {
            return String(item.value) === valueStr;
        });
        if (!option) {
            option = document.createElement('option');
            option.value = valueStr;
            option.textContent = String(label || valueStr);
            select.appendChild(option);
        } else if (label) {
            option.textContent = String(label);
        }
    }

    function getLinkedParamSelectForChildSelect(childSelect) {
        if (!childSelect) {
            return null;
        }
        const scope = childSelect.closest('form') || document;
        const linkedParamId = String(childSelect.getAttribute('data-linked-param-id') || '').trim();
        if (linkedParamId) {
            const byParamId = scope.querySelector('select[name="col_value_' + linkedParamId + '[]"]')
                || document.querySelector('select[name="col_value_' + linkedParamId + '[]"]');
            if (byParamId) {
                return byParamId;
            }
        }
        const linkMetaId = getLinkMetaIdFromNode(childSelect);
        if (linkMetaId) {
            const byMeta = scope.querySelector('select[name^="col_value_"][data-link-meta-id="' + linkMetaId + '"]')
                || document.querySelector('select[name^="col_value_"][data-link-meta-id="' + linkMetaId + '"]');
            if (byMeta) {
                return byMeta;
            }
        }
        const childObjId = String(childSelect.getAttribute('data-child-object-id') || '').trim();
        if (!childObjId) {
            return null;
        }
        const scopedNodes = Array.from(scope.querySelectorAll('select[name^="col_value_"][data-linked-object-id="' + childObjId + '"]'));
        if (scopedNodes.length === 1) {
            return scopedNodes[0];
        }
        return findUniqueBySelector('select[name^="col_value_"][data-linked-object-id="' + childObjId + '"]');
    }

    function getChildSelectForParamSelect(paramSelect) {
        if (!paramSelect) {
            return null;
        }
        const scope = paramSelect.closest('form') || document;
        const linkedParamId = extractParamIdFromFieldName(paramSelect.name);
        if (linkedParamId) {
            const byLinkedParamId = scope.querySelector('.child-link-select[data-linked-param-id="' + linkedParamId + '"]')
                || document.querySelector('.child-link-select[data-linked-param-id="' + linkedParamId + '"]');
            if (byLinkedParamId) {
                return byLinkedParamId;
            }
        }
        const linkMetaId = getLinkMetaIdFromNode(paramSelect);
        if (linkMetaId) {
            const byMeta = scope.querySelector('.child-link-select[data-link-id="' + linkMetaId + '"], .child-link-select[data-link-meta-id="' + linkMetaId + '"]')
                || document.querySelector('.child-link-select[data-link-id="' + linkMetaId + '"], .child-link-select[data-link-meta-id="' + linkMetaId + '"]');
            if (byMeta) {
                return byMeta;
            }
        }
        const childObjId = String(paramSelect.getAttribute('data-linked-object-id') || '').trim();
        if (!childObjId) {
            return null;
        }
        const scopedNodes = Array.from(scope.querySelectorAll('.child-link-select[data-child-object-id="' + childObjId + '"]'));
        if (scopedNodes.length === 1) {
            return scopedNodes[0];
        }
        return findUniqueBySelector('.child-link-select[data-child-object-id="' + childObjId + '"]');
    }

    function scheduleLiveLinkSync(form, source) {
        if (!form || linkHydrationInProgress || !shouldUseApiMutations() || form.id !== 'form_update_data_to_object') {
            return;
        }
        const objectIdMeta = document.querySelector('meta[name="object_id"]');
        const objectId = objectIdMeta ? String(objectIdMeta.getAttribute('content') || '').trim() : '';
        const recordUid = getRecordUidFromPage();
        if (!objectId || !recordUid) {
            return;
        }
        if (liveLinkSyncTimer) {
            window.clearTimeout(liveLinkSyncTimer);
        }
        liveLinkSyncTimer = window.setTimeout(function () {
            const desired = collectLinkSelections(form);
            if (isFormsDebugEnabled()) {
                debugLogLinkSelections(form, 'scheduleLiveLinkSync:' + source, desired);
            }
            syncLinks(objectId, recordUid, desired).catch(function (error) {
                console.error('Failed to sync links after control change (' + source + ')', error);
            });
        }, 180);
    }

    function normaliseSelectionForCompare(select, values) {
        const sourceValues = Array.isArray(values) ? values : getSelectedValues(select);
        const cleaned = sourceValues
            .map(function (item) { return String(item || '').trim(); })
            .filter(Boolean);
        if (select && select.multiple) {
            return Array.from(new Set(cleaned)).sort();
        }
        return cleaned.length ? [cleaned[0]] : [];
    }

    function selectionsEqual(left, right) {
        if (left.length !== right.length) {
            return false;
        }
        for (let i = 0; i < left.length; i += 1) {
            if (left[i] !== right[i]) {
                return false;
            }
        }
        return true;
    }

    function warnPairDrift(source, childSelect, paramSelect) {
        if (!isDevGuardEnabled() || !childSelect || !paramSelect) {
            return;
        }
        const childValues = normaliseSelectionForCompare(childSelect);
        const paramValues = normaliseSelectionForCompare(paramSelect);
        if (!selectionsEqual(childValues, paramValues)) {
            console.warn(
                '[dbm_object_forms] linked controls drift detected',
                {
                    source: source,
                    linkMetaId: getLinkMetaIdFromNode(childSelect) || getLinkMetaIdFromNode(paramSelect),
                    childSelectName: childSelect.name,
                    paramSelectName: paramSelect.name,
                    childValues: childValues,
                    paramValues: paramValues,
                }
            );
        }
    }

    function enforcePairInvariant(source, childSelect, paramSelect) {
        if (!childSelect || !paramSelect) {
            return;
        }
        const currentChildValues = normaliseSelectionForCompare(childSelect);
        const currentParamValues = normaliseSelectionForCompare(paramSelect);
        let desiredValues = source === 'param'
            ? currentParamValues
            : currentChildValues;
        if (source === 'submit') {
            const linkMetaId = getLinkMetaIdFromNode(childSelect) || getLinkMetaIdFromNode(paramSelect);
            const preferredSource = getPreferredPairSource(linkMetaId);
            if (preferredSource === 'param') {
                desiredValues = currentParamValues;
            } else if (preferredSource === 'child') {
                desiredValues = currentChildValues;
            } else if (!currentChildValues.length && currentParamValues.length) {
                desiredValues = currentParamValues;
            } else if (!currentParamValues.length && currentChildValues.length) {
                desiredValues = currentChildValues;
            }
        }
        desiredValues.forEach(function (value) {
            ensureOptionExists(childSelect, value, getSelectOptionLabel(paramSelect, value) || value);
            ensureOptionExists(paramSelect, value, getSelectOptionLabel(childSelect, value) || value);
        });
        const appliedChildValues = normaliseSelectionForCompare(childSelect);
        const appliedParamValues = normaliseSelectionForCompare(paramSelect);
        if (selectionsEqual(appliedChildValues, desiredValues) && selectionsEqual(appliedParamValues, desiredValues)) {
            return desiredValues;
        }
        childSelect.dataset.syncing = '1';
        paramSelect.dataset.syncing = '1';
        try {
            setSelectValue(childSelect, childSelect.multiple ? desiredValues : (desiredValues[0] || ''));
            setSelectValue(paramSelect, paramSelect.multiple ? desiredValues : (desiredValues[0] || ''));
        } finally {
            childSelect.dataset.syncing = '';
            paramSelect.dataset.syncing = '';
        }
        warnPairDrift(source + '->enforce', childSelect, paramSelect);
        return desiredValues;
    }

    async function fetchLinkedRecordOptions(childObjectId) {
        const key = String(childObjectId || '').trim();
        if (!key) {
            return [];
        }
        if (linkedOptionsCache.has(key)) {
            return linkedOptionsCache.get(key);
        }
        const promise = window.dbmApi.listRecords(key, {
            limit: 200,
            offset: 0,
            order: 'identificator',
            include_schema: 0,
        }).then(function (payload) {
            const rows = payload && Array.isArray(payload.records) ? payload.records : [];
            return rows
                .map(function (row) {
                    const uid = String((row && row.record_uid) || '').trim();
                    if (!uid) {
                        return null;
                    }
                    const label = String((row && (row.identificator || row.record_uid)) || uid);
                    return { value: uid, label: label };
                })
                .filter(Boolean);
        }).catch(function (error) {
            linkedOptionsCache.delete(key);
            throw error;
        });
        linkedOptionsCache.set(key, promise);
        return promise;
    }

    async function ensureMissingSelectedOptionLabels(select, childObjectId, selectedValues) {
        const uniqueValues = Array.from(new Set((selectedValues || []).map(function (item) { return String(item || '').trim(); }).filter(Boolean)));
        for (let i = 0; i < uniqueValues.length; i += 1) {
            const value = uniqueValues[i];
            const currentLabel = getSelectOptionLabel(select, value);
            if (currentLabel) {
                continue;
            }
            try {
                const payload = await window.dbmApi.getRecord(childObjectId, value);
                const normalised = window.dbmDto.normaliseRecordPayload(payload);
                const label = String(normalised.identificator || normalised.record_uid || value);
                ensureOptionExists(select, value, label);
            } catch (_error) {
                ensureOptionExists(select, value, value);
            }
        }
    }

    async function hydrateLinkedSelectOptions(select, preferredValues) {
        if (!select) {
            return;
        }
        const childObjectId = String(
            select.getAttribute('data-linked-object-id')
            || select.getAttribute('data-child-object-id')
            || ''
        ).trim();
        if (!childObjectId) {
            return;
        }
        const selectedValues = (preferredValues && preferredValues.length)
            ? preferredValues
            : getFallbackSelectedValues(select);
        try {
            const options = await fetchLinkedRecordOptions(childObjectId);
            options.forEach(function (item) {
                ensureOptionExists(select, item.value, item.label);
            });
            await ensureMissingSelectedOptionLabels(select, childObjectId, selectedValues);
            if (select.multiple) {
                setSelectValue(select, selectedValues);
            } else {
                setSelectValue(select, selectedValues[0] || '');
            }
            const childUid = selectedValues.length ? selectedValues[0] : '';
            hydrateChildParamFields(childObjectId, childUid, getLinkMetaIdFromNode(select));
            emitLinksChanged({
                source: 'hydrateLinkedSelectOptions',
                childObjectId: childObjectId,
            });
        } catch (error) {
            console.error('Failed to load linked options for object', childObjectId, error);
        }
    }

    function syncFromChildSelect(childSelect, options) {
        if (!childSelect) {
            return;
        }
        const syncOptions = options && typeof options === 'object' ? options : {};
        const selectedValues = getSelectedValues(childSelect);
        const childObjId = String(childSelect.getAttribute('data-child-object-id') || '');
        const linkMetaId = getLinkMetaIdFromNode(childSelect);
        if (!syncOptions.preserveInteraction) {
            rememberLinkInteraction(linkMetaId, 'child');
        }
        if (childSelect.multiple) {
            syncPreviewSelectFromChildSelection(childSelect, selectedValues);
        } else {
            hydrateChildParamFields(childObjId, selectedValues.length ? selectedValues[0] : '', linkMetaId);
        }
        const eventPayload = {
            source: 'syncFromChildSelect',
            linkMetaId: linkMetaId,
            childObjectId: String(childSelect.getAttribute('data-child-object-id') || ''),
            selectedValues: selectedValues,
        };
        const linkedParamSelect = getLinkedParamSelectForChildSelect(childSelect);
        if (!linkedParamSelect || childSelect.dataset.syncing === '1') {
            if (linkedParamSelect) {
                warnPairDrift('child->param(skip-syncing)', childSelect, linkedParamSelect);
            }
            emitLinksChanged(eventPayload);
            scheduleLiveLinkSync(childSelect.form, 'syncFromChildSelect(skip)');
            return;
        }
        selectedValues.forEach(function (value) {
            ensureOptionExists(linkedParamSelect, value, getSelectOptionLabel(childSelect, value) || value);
        });
        childSelect.dataset.syncing = '1';
        linkedParamSelect.dataset.syncing = '1';
        setSelectValue(linkedParamSelect, childSelect.multiple ? selectedValues : (selectedValues[0] || ''));
        linkedParamSelect.dataset.syncing = '';
        childSelect.dataset.syncing = '';
        enforcePairInvariant('child', childSelect, linkedParamSelect);
        warnPairDrift('child->param', childSelect, linkedParamSelect);
        emitLinksChanged(eventPayload);
        scheduleLiveLinkSync(childSelect.form, 'syncFromChildSelect');
    }

    function syncFromParamSelect(paramSelect) {
        if (!paramSelect || paramSelect.dataset.syncing === '1') {
            return;
        }
        const childSelect = getChildSelectForParamSelect(paramSelect);
        const linkMetaId = getLinkMetaIdFromNode(paramSelect);
        rememberLinkInteraction(linkMetaId, 'param');
        if (isFormsDebugEnabled()) {
            console.log('[dbm_object_forms] syncFromParamSelect:input', {
                paramSelectName: paramSelect ? paramSelect.name : '',
                linkMetaId: linkMetaId,
                linkedObjectId: String(paramSelect.getAttribute('data-linked-object-id') || ''),
                selectedRaw: getSelectValue(paramSelect),
                selectedValues: getSelectedValues(paramSelect),
                childSelectFound: !!childSelect,
                childSelectName: childSelect ? childSelect.name : '',
            });
        }
        if (!childSelect) {
            return;
        }
        const value = getSelectValue(paramSelect);
        const values = Array.isArray(value)
            ? value.map(function (item) { return String(item || '').trim(); }).filter(Boolean)
            : (String(value || '').trim() ? [String(value || '').trim()] : []);
        values.forEach(function (item) {
            ensureOptionExists(childSelect, item, getSelectOptionLabel(paramSelect, item) || item);
        });
        paramSelect.dataset.syncing = '1';
        childSelect.dataset.syncing = '1';
        setSelectValue(childSelect, childSelect.multiple ? values : (values[0] || ''));
        childSelect.dataset.syncing = '';
        paramSelect.dataset.syncing = '';
        enforcePairInvariant('param', childSelect, paramSelect);
        syncFromChildSelect(childSelect, { preserveInteraction: true });
        warnPairDrift('param->child', childSelect, paramSelect);
        emitLinksChanged({
            source: 'syncFromParamSelect',
            linkMetaId: linkMetaId,
            childObjectId: String(paramSelect.getAttribute('data-linked-object-id') || ''),
            selectedValues: values,
        });
        if (isFormsDebugEnabled()) {
            console.log('[dbm_object_forms] syncFromParamSelect:applied', {
                paramSelectName: paramSelect.name,
                childSelectName: childSelect.name,
                linkMetaId: getLinkMetaIdFromNode(paramSelect),
                selectedValues: values,
                childSelectedAfter: getSelectedValues(childSelect),
            });
        }
    }

    async function hydrateLinkControlOptions(form) {
        const childSelects = Array.from(form.querySelectorAll('.child-link-select[data-child-object-id]'));
        const paramSelects = Array.from(form.querySelectorAll('select[name^="col_value_"][data-linked-object-id], select[name^="col_value_"][data-link-meta-id]'));
        const tasks = [];
        linkHydrationInProgress = true;
        try {
            childSelects.forEach(function (select) {
                tasks.push(hydrateLinkedSelectOptions(select));
            });
            paramSelects.forEach(function (select) {
                tasks.push(hydrateLinkedSelectOptions(select));
            });
            await Promise.all(tasks);
            childSelects.forEach(function (childSelect) {
                const linkedParamSelect = getLinkedParamSelectForChildSelect(childSelect);
                if (!linkedParamSelect) {
                    syncFromChildSelect(childSelect);
                    return;
                }
                const childValues = normaliseSelectionForCompare(childSelect);
                const paramValues = normaliseSelectionForCompare(linkedParamSelect);
                if (!childValues.length && paramValues.length) {
                    syncFromParamSelect(linkedParamSelect);
                    return;
                }
                syncFromChildSelect(childSelect);
            });
        } finally {
            linkHydrationInProgress = false;
        }
    }

    function openChildRecordPicker(button) {
        if (!window.dbmRecordPicker || !window.bootstrap || !window.bootstrap.Modal) {
            return;
        }
        const childObjectId = String(button.getAttribute('data-child-object-id') || '').trim();
        const linkName = String(button.getAttribute('data-child-link-name') || '').trim();
        if (!childObjectId || !linkName) {
            return;
        }
        const select = document.querySelector('select[name="' + linkName + '"]');
        if (!select) {
            return;
        }
        const modalEl = document.getElementById('childRecordPickerModal');
        const modalBody = document.getElementById('child-record-picker-container');
        const titleEl = document.getElementById('childRecordPickerModalLabel');
        if (!modalEl || !modalBody) {
            return;
        }
        if (titleEl) {
            const parentLabel = button.parentElement ? button.parentElement.querySelector('label.form-label') : null;
            titleEl.textContent = parentLabel ? ('Выбор записи: ' + parentLabel.textContent) : 'Выбор связанной записи';
        }
        if (childPickerInstance && typeof childPickerInstance.destroy === 'function') {
            childPickerInstance.destroy();
            childPickerInstance = null;
        }
        childPickerInstance = window.dbmRecordPicker.createRecordPicker({
            objectId: childObjectId,
            containerEl: modalBody,
            mode: select.multiple ? 'multiple' : 'single',
            order: 'identificator',
            pageSize: 50,
            persistKey: 'dbm:child-record-picker:' + childObjectId,
            onSelect: function (recordUid, record) {
                const label = (record && (record.identificator || record.record_uid)) || recordUid;
                ensureOptionExists(select, recordUid, label);
                const linkedParamSelect = getLinkedParamSelectForChildSelect(select);
                if (linkedParamSelect) {
                    ensureOptionExists(linkedParamSelect, recordUid, label);
                }
                if (select.multiple) {
                    const values = getSelectedValues(select);
                    if (values.indexOf(recordUid) === -1) {
                        values.push(recordUid);
                    }
                    setSelectValue(select, values);
                } else {
                    setSelectValue(select, recordUid);
                    window.bootstrap.Modal.getOrCreateInstance(modalEl).hide();
                }
            },
            onEscape: function () {
                window.bootstrap.Modal.getOrCreateInstance(modalEl).hide();
            },
        });
        window.bootstrap.Modal.getOrCreateInstance(modalEl).show();
    }

    function setupChildRecordPickerButtons() {
        const buttons = document.querySelectorAll('.child-record-picker-open[data-child-object-id][data-child-link-name]');
        buttons.forEach(function (button) {
            button.addEventListener('click', function () {
                openChildRecordPicker(button);
            });
        });
        const modalEl = document.getElementById('childRecordPickerModal');
        if (modalEl) {
            modalEl.addEventListener('hidden.bs.modal', function () {
                if (childPickerInstance && typeof childPickerInstance.destroy === 'function') {
                    childPickerInstance.destroy();
                    childPickerInstance = null;
                }
            });
        }
    }

    function setupChildPreviewHandlers() {
        document.addEventListener('change', function (evt) {
            const target = evt.target;
            if (!(target instanceof Element)) {
                return;
            }
            if (target.classList.contains('child-link-select')) {
                if (target.dataset.syncing === '1') {
                    return;
                }
                syncFromChildSelect(target);
                return;
            }

            if (target.classList.contains('child-preview-select')) {
                const childObjId = String(target.getAttribute('data-child-object-id') || '');
                hydrateChildParamFields(childObjId, target.value || '', getLinkMetaIdFromNode(target));
                return;
            }

            if (target.matches('select[name^="col_value_"]')) {
                const childObjId = String(target.getAttribute('data-linked-object-id') || '');
                const linkMetaId = getLinkMetaIdFromNode(target);
                if ((!childObjId && !linkMetaId) || target.dataset.syncing) {
                    return;
                }
                syncFromParamSelect(target);
            }
        });
        if (window.jQuery && window.jQuery.fn.select2 && !paramSelect2HandlersBound) {
            paramSelect2HandlersBound = true;
            window.jQuery(document).on(
                'select2:select.dbmObjectForms select2:clear.dbmObjectForms',
                'select[name^="col_value_"][data-link-meta-id], select[name^="col_value_"][data-linked-object-id]',
                function () {
                    const target = this;
                    if (!target || target.dataset.syncing === '1') {
                        return;
                    }
                    syncFromParamSelect(target);
                }
            );
        }
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
        debugLogFieldSnapshot(form, 'init:start-before-select2');
        const csrfInput = form.querySelector('[name="csrfmiddlewaretoken"]');
        const csrfToken = csrfInput ? csrfInput.value : '';
        setupSelect2AndValidation(form);
        debugLogFieldSnapshot(form, 'init:after-select2');
        setupChildPreviewHandlers();
        setupChildRecordPickerButtons();
        hydrateLinkControlOptions(form).catch(function (error) {
            console.error('Failed to initialise linked select options', error);
        }).finally(function () {
            debugLogFieldSnapshot(form, 'init:after-hydration');
        });

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
                    debugLogLinkSelections(form, 'submit-before-save', linkSelections);
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
