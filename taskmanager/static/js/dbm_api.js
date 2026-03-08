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

    const uiV1Only = readFlag('DBM_UI_V1_ONLY', false);
    const legacyFallbackEnabled = readFlag('DBM_UI_LEGACY_FALLBACK', false);

    function getCsrfToken() {
        const tokenMeta = document.querySelector('meta[name="csrf_token"]');
        if (tokenMeta) {
            return tokenMeta.getAttribute('content');
        }
        const input = document.querySelector('input[name="csrfmiddlewaretoken"]');
        if (input) {
            return input.value;
        }
        const match = document.cookie.match(/(?:^|;\s*)csrftoken=([^;]+)/);
        return match ? decodeURIComponent(match[1]) : '';
    }

    function warnLegacyFallback(message) {
        console.warn('legacy fallback:', message || '');
    }

    function canUseLegacyFallback() {
        return !uiV1Only && legacyFallbackEnabled;
    }

    function createApiError(code, message, details) {
        const error = new Error(message || 'DBM API request failed');
        error.code = code || 'SERVER_ERROR';
        error.details = details || {};
        return error;
    }

    function parseJsonSafe(response) {
        return response.text().then(function (raw) {
            if (!raw) {
                return {};
            }
            try {
                return JSON.parse(raw);
            } catch (_error) {
                return { raw: raw };
            }
        });
    }

    function handleResponse(response) {
        return parseJsonSafe(response).then(function (payload) {
            if (response.ok) {
                return payload;
            }
            if (payload && payload.error) {
                throw createApiError(payload.error.code, payload.error.message, payload.error.details);
            }
            throw createApiError('SERVER_ERROR', 'DBM API request failed', { status: response.status });
        });
    }

    function request(url, options) {
        const opts = Object.assign({}, options || {});
        opts.credentials = opts.credentials || 'same-origin';
        return fetch(url, opts).then(handleResponse);
    }

    function findIdentificatorParamId(schema) {
        const params = schema && schema.parameters ? schema.parameters : {};
        const keys = Object.keys(params);
        for (let i = 0; i < keys.length; i += 1) {
            const paramId = keys[i];
            if (params[paramId] && params[paramId].identificator) {
                return paramId;
            }
        }
        return null;
    }

    function normaliseListResponse(payload, cfg) {
        const options = cfg || {};
        const recordsRaw = payload && Array.isArray(payload.records) ? payload.records : [];
        const page = payload && payload.page ? payload.page : {};
        const schema = payload && payload.schema ? payload.schema : null;
        const identParamId = findIdentificatorParamId(schema);
        const normalisedRecords = recordsRaw.map(function (record) {
            const item = Object.assign({}, record || {});
            const uid = String(item.record_uid || item.id || '');
            let identificator = item.identificator;
            if ((identificator == null || identificator === '') && identParamId && item.fields && item.fields[identParamId]) {
                const fieldValue = item.fields[identParamId];
                identificator = (fieldValue && fieldValue.value !== undefined) ? fieldValue.value : '';
            }
            if (identificator == null) {
                identificator = '';
            }
            return {
                record_uid: uid,
                identificator: String(identificator),
                fields: item.fields || {},
            };
        });
        const limit = Number(page.limit != null ? page.limit : (options.limit != null ? options.limit : 50));
        const offset = Number(page.offset != null ? page.offset : (options.offset != null ? options.offset : 0));
        const total = page.total != null ? Number(page.total) : null;
        const hasMore = page.has_more !== undefined
            ? !!page.has_more
            : (total != null ? (offset + normalisedRecords.length < total) : normalisedRecords.length === limit);
        const result = {
            api_version: 'v1',
            object_id: payload && payload.object_id != null ? payload.object_id : null,
            schema: schema,
            records: normalisedRecords,
            limit: limit,
            offset: offset,
            has_more: hasMore,
            page: {
                limit: limit,
                offset: offset,
                has_more: hasMore,
            },
        };
        if (total != null) {
            result.total = total;
            result.page.total = total;
        }
        return result;
    }

    function requestJson(url, method, payload) {
        const headers = new Headers({
            'Content-Type': 'application/json',
            'X-CSRFToken': getCsrfToken(),
        });
        return request(url, {
            method: method,
            headers: headers,
            body: payload ? JSON.stringify(payload) : null,
        });
    }

    function listRecords(objectId, options) {
        const params = new URLSearchParams();
        const cfg = options || {};
        const limit = cfg.limit != null ? cfg.limit : 50;
        const offset = cfg.offset != null ? cfg.offset : 0;
        params.set('limit', String(limit));
        params.set('offset', String(offset));
        params.set('order', String(cfg.order || 'updated_at'));
        params.set('include_schema', String(cfg.include_schema != null ? cfg.include_schema : 1));
        if (cfg.includeTotal) {
            params.set('include_total', '1');
        }
        if (cfg.q) {
            params.set('q', String(cfg.q).trim());
        }
        return request('/database/api/v1/objects/' + objectId + '/records/?' + params.toString(), {
            method: 'GET',
            headers: {
                'X-API-Version': 'v1',
            },
            signal: cfg.signal,
        }).then(function (payload) {
            return normaliseListResponse(payload, cfg);
        }).catch(function (error) {
            if (error && error.name === 'AbortError') {
                throw error;
            }
            if (!canUseLegacyFallback()) {
                throw error;
            }
            warnLegacyFallback('listRecords');
            return getObjectLegacy(objectId).then(function (legacyPayload) {
                const idents = (legacyPayload && Array.isArray(legacyPayload.idents)) ? legacyPayload.idents : [];
                const records = idents.map(function (item) {
                    return {
                        record_uid: String(item.id || ''),
                        identificator: String(item.param_ident || ''),
                        fields: {},
                    };
                });
                const fallback = {
                    api_version: 'v1',
                    object_id: objectId,
                    schema: legacyPayload && legacyPayload.schema ? legacyPayload.schema : null,
                    records: records,
                    limit: Number(limit),
                    offset: Number(offset),
                    has_more: false,
                    total: records.length,
                    page: {
                        limit: Number(limit),
                        offset: Number(offset),
                        has_more: false,
                        total: records.length,
                    },
                };
                return fallback;
            });
        });
    }

    function getRecord(objectId, recordUid) {
        return request('/database/api/v1/objects/' + objectId + '/records/' + encodeURIComponent(recordUid) + '/', {
            method: 'GET',
            headers: {
                'X-API-Version': 'v1',
            },
        }).catch(function (error) {
            if (!canUseLegacyFallback()) {
                throw error;
            }
            warnLegacyFallback('getRecord');
            return getRecordLegacy(objectId, recordUid);
        });
    }

    function createRecord(objectId, recordFieldsDto) {
        return requestJson(
            '/database/api/v1/objects/' + objectId + '/records/',
            'POST',
            { record: { fields: recordFieldsDto || {} } }
        );
    }

    function updateRecord(objectId, recordUid, recordFieldsDto) {
        return requestJson(
            '/database/api/v1/objects/' + objectId + '/records/' + encodeURIComponent(recordUid) + '/',
            'PATCH',
            { record: { fields: recordFieldsDto || {} } }
        );
    }

    function deleteRecord(objectId, recordUid) {
        return requestJson(
            '/database/api/v1/objects/' + objectId + '/records/' + encodeURIComponent(recordUid) + '/',
            'DELETE',
            {}
        );
    }

    function getLinks(objectId, recordUid) {
        return request(
            '/database/api/v1/objects/' + objectId + '/records/' + encodeURIComponent(recordUid) + '/links/',
            { method: 'GET', headers: { 'X-API-Version': 'v1' } }
        );
    }

    function listLinksMeta(objectId) {
        return request(
            '/database/api/v1/objects/' + objectId + '/links-meta/',
            { method: 'GET', headers: { 'X-API-Version': 'v1' } }
        );
    }

    function createLinkMeta(objectId, payload) {
        return requestJson(
            '/database/api/v1/objects/' + objectId + '/links-meta/',
            'POST',
            payload || {}
        );
    }

    function updateLinkMeta(objectId, linkMetaId, payload) {
        return requestJson(
            '/database/api/v1/objects/' + objectId + '/links-meta/' + encodeURIComponent(linkMetaId) + '/',
            'PATCH',
            payload || {}
        );
    }

    function deleteLinkMeta(objectId, linkMetaId) {
        return requestJson(
            '/database/api/v1/objects/' + objectId + '/links-meta/' + encodeURIComponent(linkMetaId) + '/',
            'DELETE',
            {}
        );
    }

    function createLink(objectId, parentUid, childUid, linkMetaId) {
        return requestJson(
            '/database/api/v1/objects/' + objectId + '/records/' + encodeURIComponent(parentUid) + '/links/',
            'POST',
            { child_record_uid: childUid, link_meta_id: linkMetaId }
        );
    }

    function deleteLink(objectId, parentUid, childUid, linkMetaId) {
        return requestJson(
            '/database/api/v1/objects/' + objectId + '/records/' + encodeURIComponent(parentUid) + '/links/',
            'DELETE',
            { child_record_uid: childUid, link_meta_id: linkMetaId }
        );
    }

    function postForm(url, formData) {
        return fetch(url, {
            method: 'POST',
            headers: { 'X-CSRFToken': getCsrfToken() },
            credentials: 'same-origin',
            body: formData,
        }).then(handleResponse);
    }

    function getRecordLegacy(objId, identifier) {
        if (!canUseLegacyFallback()) {
            throw createApiError('SERVER_ERROR', 'Legacy endpoint is disabled by UI flags', {});
        }
        const formData = new FormData();
        formData.append('param_ident_id', identifier);
        return postForm('/database/get_data_from_object/' + objId + '/?api_version=v1', formData);
    }

    function getObjectLegacy(objectId) {
        if (!canUseLegacyFallback()) {
            throw createApiError('SERVER_ERROR', 'Legacy endpoint is disabled by UI flags', {});
        }
        return postForm('/database/get_object/' + objectId + '/', new FormData());
    }

    function getObjectsToConnect() {
        return request('/database/api/v1/objects/', {
            method: 'GET',
            headers: {
                'X-API-Version': 'v1',
            },
        }).then(function (payload) {
            if (Array.isArray(payload.objects)) {
                return { object: payload.objects };
            }
            return payload;
        }).catch(function (error) {
            if (!canUseLegacyFallback()) {
                throw error;
            }
            warnLegacyFallback('getObjectsToConnect');
            return requestJson('/database/get_objects_to_connect/', 'POST', {});
        });
    }

    function connectObjectsToDocument(docId, selectedObjects) {
        const formData = new FormData();
        for (let i = 0; i < selectedObjects.length; i++) {
            formData.append('selectedObjects[]', selectedObjects[i]);
        }
        return postForm('/document/connect_objects_to_document/' + docId + '/', formData);
    }

    function deleteObjectFromDocument(docId, objectId) {
        const formData = new FormData();
        formData.append('object_id', objectId);
        return postForm('/document/delete_object_from_document/' + docId + '/', formData);
    }

    function prefetchGraph(documentId, context, tokens, options) {
        return requestJson(
            '/document/api/v1/prefetch_graph/',
            'POST',
            {
                document_id: documentId,
                context: context || {},
                tokens: Array.isArray(tokens) ? tokens : [],
                options: options || {},
            }
        );
    }

    window.dbmApi = {
        isV1Only: function () { return uiV1Only; },
        isLegacyFallbackEnabled: function () { return legacyFallbackEnabled; },
        listRecords: listRecords,
        getRecord: getRecord,
        createRecord: createRecord,
        updateRecord: updateRecord,
        deleteRecord: deleteRecord,
        getLinks: getLinks,
        listLinksMeta: listLinksMeta,
        createLinkMeta: createLinkMeta,
        updateLinkMeta: updateLinkMeta,
        deleteLinkMeta: deleteLinkMeta,
        createLink: createLink,
        deleteLink: deleteLink,
        getObject: getObjectLegacy,
        getObjectsToConnect: getObjectsToConnect,
        connectObjectsToDocument: connectObjectsToDocument,
        deleteObjectFromDocument: deleteObjectFromDocument,
        prefetchGraph: prefetchGraph,
    };
})();
