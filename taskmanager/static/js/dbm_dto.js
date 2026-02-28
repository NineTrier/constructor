(function () {
    function isV1Only() {
        if (window.DBM_UI_V1_ONLY !== undefined) {
            return !!window.DBM_UI_V1_ONLY;
        }
        const meta = document.querySelector('meta[name="dbm_ui_v1_only"]');
        if (!meta) {
            return false;
        }
        const value = String(meta.getAttribute('content') || '').toLowerCase();
        return value === '1' || value === 'true' || value === 'yes';
    }

    function isLegacyFallbackEnabled() {
        if (window.DBM_UI_LEGACY_FALLBACK !== undefined) {
            return !!window.DBM_UI_LEGACY_FALLBACK;
        }
        const meta = document.querySelector('meta[name="dbm_ui_legacy_fallback"]');
        if (!meta) {
            return false;
        }
        const value = String(meta.getAttribute('content') || '').toLowerCase();
        return value === '1' || value === 'true' || value === 'yes';
    }

    function normaliseField(rawField, fallbackType) {
        if (rawField && typeof rawField === 'object' && !Array.isArray(rawField)) {
            const fieldType = rawField.type || rawField.data_type || fallbackType || 'TXT';
            return {
                type: fieldType,
                value: normaliseValueByType(rawField.value !== undefined ? rawField.value : '', fieldType),
            };
        }
        const finalType = fallbackType || 'TXT';
        return {
            type: finalType,
            value: normaliseValueByType(rawField, finalType),
        };
    }

    function normaliseValueByType(value, type) {
        const upperType = String(type || 'TXT').toUpperCase();
        if (value == null) {
            return upperType === 'ARRAY' ? [] : '';
        }
        if (upperType === 'ARRAY') {
            if (Array.isArray(value)) {
                return value.map(function (item) { return String(item); }).filter(function (item) { return item.trim(); });
            }
            const text = String(value).trim();
            return text ? [text] : [];
        }
        if (upperType === 'INT') {
            if (value === '') {
                return '';
            }
            const parsed = Number(value);
            return Number.isNaN(parsed) ? String(value) : parsed;
        }
        return String(value);
    }

    function normaliseRecordPayload(payload) {
        if (payload && payload.api_version === 'v1' && payload.record && payload.record.fields) {
            const schema = payload.schema || { parameters: {} };
            const fields = {};
            Object.keys(payload.record.fields).forEach(function (key) {
                const schemaField = (schema.parameters && schema.parameters[key]) || {};
                fields[key] = normaliseField(payload.record.fields[key], schemaField.type || 'TXT');
            });
            return {
                api_version: 'v1',
                object_id: payload.object_id || (schema && schema.object_id) || null,
                record_uid: payload.record.record_uid || '',
                fields: fields,
                schema: schema,
            };
        }

        if (isV1Only() || !isLegacyFallbackEnabled()) {
            throw new Error('Legacy payload is disabled by UI flags');
        }
        console.warn('legacy fallback: normaliseRecordPayload');

        let legacyRecord = null;
        if (Array.isArray(payload) && payload.length > 0) {
            legacyRecord = payload[0];
        } else if (payload && payload.legacy_records && payload.legacy_records.length > 0) {
            legacyRecord = payload.legacy_records[0];
        }
        if (!legacyRecord || typeof legacyRecord !== 'object') {
            return {
                api_version: 'legacy',
                record_uid: '',
                fields: {},
                schema: { parameters: {} },
            };
        }
        const fields = {};
        Object.keys(legacyRecord).forEach(function (key) {
            if (key === 'id_to_connect') {
                return;
            }
            fields[key] = normaliseField(legacyRecord[key], 'TXT');
        });
        const schemaParams = {};
        Object.keys(fields).forEach(function (key) {
            schemaParams[key] = { type: fields[key].type };
        });
        return {
            api_version: 'legacy',
            record_uid: legacyRecord.id_to_connect || '',
            fields: fields,
            schema: { parameters: schemaParams },
        };
    }

    function normaliseListPayload(payload) {
        if (payload && payload.api_version === 'v1' && Array.isArray(payload.records)) {
            return payload;
        }
        if (isV1Only() || !isLegacyFallbackEnabled()) {
            throw new Error('Legacy list payload is disabled by UI flags');
        }
        console.warn('legacy fallback: normaliseListPayload');
        const idents = (payload && Array.isArray(payload.idents)) ? payload.idents : [];
        const records = idents.map(function (item) {
            return {
                record_uid: String(item.id || ''),
                fields: {},
                _legacy_ident: item.param_ident || '',
            };
        });
        return {
            api_version: 'legacy',
            object_id: (payload && payload.object && payload.object.id) || null,
            records: records,
            page: { limit: records.length, offset: 0, total: records.length },
            schema: { parameters: {} },
        };
    }

    function fieldsFromForm(form, schema) {
        const result = {};
        const schemaParams = (schema && schema.parameters) ? schema.parameters : {};
        Object.keys(schemaParams).forEach(function (paramId) {
            const paramMeta = schemaParams[paramId] || {};
            const fieldType = String(paramMeta.type || 'TXT');
            const fieldName = 'col_value_' + paramId + '[]';
            const values = [];
            const nodes = form.querySelectorAll('[name="' + fieldName + '"]');
            if (!nodes.length) {
                return;
            }
            nodes.forEach(function (node) {
                if (node.tagName === 'SELECT' && node.multiple) {
                    Array.from(node.selectedOptions).forEach(function (option) { values.push(option.value); });
                } else {
                    values.push(node.value);
                }
            });
            let value;
            if (fieldType.toUpperCase() === 'ARRAY') {
                value = values.filter(function (item) { return String(item).trim(); });
            } else {
                value = values.length ? values[0] : '';
            }
            result[paramId] = {
                type: fieldType,
                value: normaliseValueByType(value, fieldType),
            };
        });
        return result;
    }

    function applyRecordToForm(form, recordPayload) {
        const normalised = normaliseRecordPayload(recordPayload);
        Object.keys(normalised.fields || {}).forEach(function (paramId) {
            const field = normalised.fields[paramId];
            const fieldName = 'col_value_' + paramId + '[]';
            const nodes = form.querySelectorAll('[name="' + fieldName + '"]');
            if (!nodes.length) {
                return;
            }
            nodes.forEach(function (node) {
                if (node.tagName === 'SELECT' && node.multiple) {
                    const values = Array.isArray(field.value) ? field.value : [];
                    Array.from(node.options).forEach(function (option) {
                        option.selected = values.indexOf(option.value) !== -1;
                    });
                } else if (Array.isArray(field.value)) {
                    node.value = field.value.join(', ');
                } else {
                    node.value = field.value == null ? '' : String(field.value);
                }
            });
        });
        return normalised;
    }

    window.dbmDto = {
        isV1Only: isV1Only,
        isLegacyFallbackEnabled: isLegacyFallbackEnabled,
        normaliseField: normaliseField,
        normaliseRecordPayload: normaliseRecordPayload,
        normaliseListPayload: normaliseListPayload,
        fieldsFromForm: fieldsFromForm,
        applyRecordToForm: applyRecordToForm,
        normaliseValueByType: normaliseValueByType,
    };
})();
