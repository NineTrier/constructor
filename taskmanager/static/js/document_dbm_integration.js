(function () {
    const TOKEN_BATCH_SIZE = 20;
    const TOKEN_CONCURRENCY = 4;
    const TOKEN_MAX_DEPTH = 8;
    let tokenResolveRunId = 0;
    const linksCache = new Map();
    const recordCache = new Map();

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
        alert.innerHTML = '<span>' + text + '</span><button type="button" class="btn-close" data-bs-dismiss="alert" aria-label="Close"></button>';
        container.appendChild(alert);
        setTimeout(function () {
            if (alert.parentNode) {
                alert.parentNode.removeChild(alert);
            }
        }, 5000);
    }

    function parseDocTokenIndex() {
        const node = document.getElementById('doc-token-index');
        if (!node) {
            return {
                objects_by_name: {},
                params_by_object_and_name: {},
                links_meta_by_parent_and_display: {},
                links_meta_by_parent_and_child_object_name: {},
                links_meta_by_parent_and_link_param_name: {},
                links_meta_by_id: {},
                link_param_by_meta_id: {},
            };
        }
        try {
            const payload = JSON.parse(node.textContent || '{}');
            return payload && typeof payload === 'object' ? payload : {};
        } catch (_error) {
            return {
                objects_by_name: {},
                params_by_object_and_name: {},
                links_meta_by_parent_and_display: {},
                links_meta_by_parent_and_child_object_name: {},
                links_meta_by_parent_and_link_param_name: {},
                links_meta_by_id: {},
                link_param_by_meta_id: {},
            };
        }
    }

    const docTokenIndex = parseDocTokenIndex();

    function emitUiEvent(name, detail) {
        const eventName = String(name || '').trim();
        if (!eventName) { return; }
        const payload = detail && typeof detail === 'object' ? detail : {};
        try {
            if (window.docUiEvents && typeof window.docUiEvents.dispatchEvent === 'function') {
                window.docUiEvents.dispatchEvent(new CustomEvent(eventName, { detail: payload }));
            }
        } catch (_error) {
            // no-op
        }
        try {
            document.dispatchEvent(new CustomEvent(eventName, { detail: payload }));
        } catch (_error) {
            // no-op
        }
    }

    function emitRecordSelected(objectId, recordUid) {
        const oid = Number(objectId || 0);
        const uid = String(recordUid || '').trim();
        if (!oid || !uid) { return; }
        emitUiEvent('dbm:record-selected', {
            objectId: oid,
            recordUid: uid,
        });
    }

    function getObjectIdFromParamNode(node) {
        const objectContainer = node ? node.closest('.object[id^="object_"]') : null;
        if (!objectContainer) {
            return null;
        }
        const rawId = String(objectContainer.id || '').replace('object_', '');
        return rawId ? Number(rawId) : null;
    }

    function readParamValueFromNode(node) {
        const valueNode = node ? node.querySelector('.obj_parameter_value') : null;
        if (!valueNode) {
            return '';
        }
        if (valueNode.classList.contains('array_data_container')) {
            return Array.from(valueNode.querySelectorAll('.array_data span')).map(function (item) {
                return String(item.textContent || '').trim();
            }).filter(Boolean).join(', ');
        }
        if (valueNode.value !== undefined) {
            return valueNode.value == null ? '' : String(valueNode.value);
        }
        return String(valueNode.textContent || '');
    }

    function getCurrentSelectedRecordUid(objectId) {
        try {
            if (window.selectedIdent && window.selectedIdent[objectId]) {
                return String(window.selectedIdent[objectId] || '').trim();
            }
        } catch (_error) {
            // no-op
        }
        const objectElement = document.getElementById('object_' + objectId);
        const input = objectElement ? objectElement.querySelector('input[name="param_ident_id"]') : null;
        return input ? String(input.value || '').trim() : '';
    }

    function getObjectNameById(objectId) {
        const map = docTokenIndex && docTokenIndex.objects_by_name ? docTokenIndex.objects_by_name : {};
        let result = '';
        Object.keys(map).forEach(function (name) {
            if (Number(map[name]) === Number(objectId)) {
                result = String(name || '');
            }
        });
        return result;
    }

    function markSpanAsReference(span) {
        if (!span || !span.classList) { return; }
        span.classList.add('reference_to_data');
    }

    function isReferenceLikeSpan(span) {
        if (!span || !span.classList) { return false; }
        return span.classList.contains('variable_ref')
            || span.classList.contains('object_param_ref')
            || span.hasAttribute('data-token')
            || span.hasAttribute('data-human-token')
            || span.hasAttribute('data-idVar')
            || span.hasAttribute('data-idObjParam');
    }

    function syncReferenceClassForKnownSpans() {
        document.querySelectorAll('span.runs').forEach(function (span) {
            if (isReferenceLikeSpan(span)) {
                markSpanAsReference(span);
            }
        });
    }

    const valueRegistry = (function () {
        const directSources = new Map();
        const linkedSources = new Map();
        function directKey(objectId, paramId) { return String(objectId) + ':' + String(paramId); }
        function linkedKey(parentObjectId, linkMetaId, childParamId) { return String(parentObjectId) + ':' + String(linkMetaId) + ':' + String(childParamId); }
        function getLinkSteps(ast) {
            if (!ast || ast.version !== 'canonical') { return []; }
            if (Array.isArray(ast.linkSteps)) { return ast.linkSteps; }
            return ast.linkMetaId ? [{ linkMetaId: ast.linkMetaId, selector: 'first', index: null }] : [];
        }
        return {
            registerValueSource: function (source) {
                if (source && source.objectId != null && source.paramId != null && typeof source.getter === 'function') {
                    directSources.set(directKey(source.objectId, source.paramId), source);
                }
            },
            registerLinkedValueSource: function (source) {
                if (source && source.parentObjectId != null && source.linkMetaId != null && source.childParamId != null && typeof source.getter === 'function') {
                    linkedSources.set(linkedKey(source.parentObjectId, source.linkMetaId, source.childParamId), source);
                }
            },
            getValueByToken: function (ast) {
                if (!ast || ast.version !== 'canonical') { return ''; }
                const steps = getLinkSteps(ast);
                if (!steps.length) {
                    const direct = directSources.get(directKey(ast.objectId, ast.paramId));
                    return direct ? String(direct.getter() || '') : '';
                }
                if (steps.length === 1 && (steps[0].selector === 'first' || (steps[0].selector === 'index' && Number(steps[0].index || 0) === 0))) {
                    const linked = linkedSources.get(linkedKey(ast.objectId, steps[0].linkMetaId, ast.paramId));
                    return linked ? String(linked.getter() || '') : '';
                }
                return '';
            }
        };
    })();

    function bootstrapValueRegistry() {
        document.querySelectorAll('.obj_parameter[data-idparam]').forEach(function (node) {
            const objectId = getObjectIdFromParamNode(node);
            const paramId = node.getAttribute('data-idParam');
            if (!objectId || !paramId) { return; }
            valueRegistry.registerValueSource({ objectId: objectId, paramId: paramId, getter: function () { return readParamValueFromNode(node); } });
            const parts = String(node.getAttribute('data-paramName') || '').trim().split('.').map(function (item) { return String(item || '').trim(); }).filter(Boolean);
            if (parts.length !== 3) { return; }
            const rawLinkMeta = (((docTokenIndex.links_meta_by_parent_and_display || {})[String(objectId)] || {})[parts[1]]);
            const linkMetaId = Array.isArray(rawLinkMeta) ? rawLinkMeta[0] : rawLinkMeta;
            const childObjectId = Number((((docTokenIndex.links_meta_by_id || {})[String(linkMetaId)] || {}).child_object_id) || 0);
            const childParamId = (((docTokenIndex.params_by_object_and_name || {})[String(childObjectId)] || {})[parts[2]]);
            if (!linkMetaId || !childObjectId || !childParamId) { return; }
            valueRegistry.registerLinkedValueSource({ parentObjectId: objectId, linkMetaId: linkMetaId, childParamId: childParamId, getter: function () { return readParamValueFromNode(node); } });
        });
    }

    function resolveAnyTokenToCanonical(tokenCandidate) {
        if (!window.docTokenParser) { return null; }
        const parser = window.docTokenParser;
        const tokenText = String(tokenCandidate || '').trim();
        if (!tokenText) { return null; }
        const parsed = parser.parseHumanTokenToAst(tokenText, docTokenIndex);
        if (!parsed || !parsed.ok || !parsed.ast || !parsed.canonicalToken) {
            return null;
        }
        return parsed;
    }

    function ensureCanonicalTokens() {
        if (!window.docTokenParser) { return; }
        const spans = Array.from(document.querySelectorAll('span[data-invis], span[data-token]'));
        let migratedCount = 0;
        spans.forEach(function (span) {
            if (isReferenceLikeSpan(span)) {
                markSpanAsReference(span);
            }
            const canonicalRaw = String(span.getAttribute('data-token') || '').trim();
            const canonicalParsed = canonicalRaw ? resolveAnyTokenToCanonical(canonicalRaw) : null;
            if (canonicalParsed && canonicalParsed.ast && canonicalParsed.ast.version === 'canonical') {
                span.setAttribute('data-token', canonicalParsed.canonicalToken);
                span.setAttribute('data-token-version', 'v1');
                span.removeAttribute('data-token-unresolved');
                markSpanAsReference(span);
                return;
            }

            const dataInvis = String(span.getAttribute('data-invis') || '').trim();
            const textContent = String(span.textContent || '').trim();
            const rawToken = dataInvis || textContent;
            if (!rawToken || rawToken.indexOf('{:') === -1 || rawToken.indexOf(':}') === -1) {
                return;
            }
            const parsed = resolveAnyTokenToCanonical(rawToken);
            if (!parsed) {
                span.setAttribute('data-token-unresolved', 'legacy');
                span.setAttribute('title', 'Не удалось сопоставить токен с текущей схемой объекта.');
                markSpanAsReference(span);
                return;
            }
            span.setAttribute('data-token', parsed.canonicalToken);
            span.setAttribute('data-token-version', 'v1');
            span.setAttribute('data-human-token', rawToken);
            span.removeAttribute('data-token-unresolved');
            markSpanAsReference(span);
            migratedCount += 1;
        });
        if (migratedCount > 0) {
            emitUiEvent('dbm:tokens-migrated', {
                total: spans.length,
                migrated: migratedCount,
            });
        }
    }

    function getAstLinkSteps(ast) {
        if (!ast || ast.version !== 'canonical') { return []; }
        if (Array.isArray(ast.linkSteps)) { return ast.linkSteps; }
        return ast.linkMetaId ? [{ linkMetaId: ast.linkMetaId, selector: 'first', index: null }] : [];
    }
    function readDocumentIdFromMeta() {
        const meta = document.querySelector('meta[name="doc_id"]');
        return meta ? String(meta.getAttribute('content') || '').trim() : '';
    }
    function cacheRecord(objectId, recordUid) {
        const key = String(objectId) + ':' + String(recordUid);
        if (recordCache.has(key)) { return recordCache.get(key); }
        const promise = window.dbmApi.getRecord(objectId, recordUid).catch(function (error) { recordCache.delete(key); throw error; });
        recordCache.set(key, promise);
        return promise;
    }
    function cacheLinks(objectId, recordUid) {
        const key = String(objectId) + ':' + String(recordUid);
        if (linksCache.has(key)) { return linksCache.get(key); }
        const promise = window.dbmApi.getLinks(objectId, recordUid).catch(function (error) { linksCache.delete(key); throw error; });
        linksCache.set(key, promise);
        return promise;
    }
    function pickChildren(step, childUids, warnings) {
        const sorted = (childUids || []).map(function (item) { return String(item || '').trim(); }).filter(Boolean).sort();
        if (!sorted.length) { return []; }
        if (step.selector === 'all') { return sorted; }
        if (step.selector === 'index') {
            const index = Number(step.index || 0);
            if (index < 0 || index >= sorted.length) { warnings.push('INDEX_OUT_OF_RANGE'); return []; }
            return [sorted[index]];
        }
        if (sorted.length > 1) { warnings.push('MULTIPLE_TO_FIRST'); }
        return [sorted[0]];
    }
    function parseRecordFieldValue(payload, paramId) {
        const record = window.dbmDto.normaliseRecordPayload(payload || {});
        const field = window.dbmDto.normaliseField((record.fields || {})[String(paramId)] || {});
        if (Array.isArray(field.value)) { return field.value.filter(Boolean).join(', '); }
        return field.value == null ? '' : String(field.value);
    }
    async function resolveTokenAst(ast) {
        const steps = getAstLinkSteps(ast);
        if (steps.length > TOKEN_MAX_DEPTH) { throw { code: 'MAX_DEPTH_EXCEEDED', message: 'Превышена глубина связей.' }; }
        const rootUid = getCurrentSelectedRecordUid(ast.objectId);
        if (!rootUid) { throw { code: 'ROOT_RECORD_NOT_SELECTED', message: 'Не выбрана запись для корневого объекта.' }; }
        if (!steps.length) { return { value: parseRecordFieldValue(await cacheRecord(ast.objectId, rootUid), ast.paramId), warnings: [] }; }
        let states = [{ objectId: Number(ast.objectId), recordUid: rootUid, visited: new Set([String(ast.objectId) + ':' + String(rootUid)]) }];
        const warnings = [];
        for (let i = 0; i < steps.length; i += 1) {
            const step = steps[i];
            const linkInfo = ((docTokenIndex.links_meta_by_id || {})[String(step.linkMetaId)] || {});
            const childObjectId = Number(linkInfo.child_object_id || 0);
            if (!childObjectId) { throw { code: 'LINK_META_NOT_FOUND', message: 'Связь-мета не найдена в индексе.' }; }
            const next = [];
            for (let j = 0; j < states.length; j += 1) {
                const state = states[j];
                const links = (await cacheLinks(state.objectId, state.recordUid)).links || [];
                const entry = links.find(function (item) { return Number(item.link_meta_id) === Number(step.linkMetaId); });
                if (!entry) { continue; }
                const selected = pickChildren(step, entry.child_record_uids || [], warnings);
                for (let k = 0; k < selected.length; k += 1) {
                    const uid = selected[k];
                    const visitKey = String(childObjectId) + ':' + uid;
                    if (state.visited.has(visitKey)) { throw { code: 'CYCLE_DETECTED', message: 'Обнаружен цикл в связях записи.' }; }
                    const visited = new Set(state.visited); visited.add(visitKey);
                    next.push({ objectId: childObjectId, recordUid: uid, visited: visited });
                }
            }
            states = next;
            if (!states.length) { return { value: '', warnings: warnings }; }
        }
        const values = [];
        for (let i = 0; i < states.length; i += 1) {
            const value = parseRecordFieldValue(await cacheRecord(states[i].objectId, states[i].recordUid), ast.paramId);
            if (String(value || '').trim()) { values.push(String(value)); }
        }
        if (!values.length) { return { value: '', warnings: warnings }; }
        if (steps.some(function (step) { return step.selector === 'all'; })) { return { value: values.join(', '), warnings: warnings }; }
        if (values.length > 1) { warnings.push('MULTIPLE_TO_FIRST'); }
        return { value: values[0], warnings: warnings };
    }

    async function prefetchTokenAst(ast, runId) {
        const steps = getAstLinkSteps(ast);
        if (steps.length > TOKEN_MAX_DEPTH || runId !== tokenResolveRunId) { return; }
        const rootUid = getCurrentSelectedRecordUid(ast.objectId);
        if (!rootUid) { return; }
        await cacheRecord(ast.objectId, rootUid);
        let states = [{ objectId: Number(ast.objectId), recordUid: rootUid, visited: new Set([String(ast.objectId) + ':' + String(rootUid)]) }];
        for (let i = 0; i < steps.length; i += 1) {
            if (runId !== tokenResolveRunId) { return; }
            const step = steps[i];
            const linkInfo = ((docTokenIndex.links_meta_by_id || {})[String(step.linkMetaId)] || {});
            const childObjectId = Number(linkInfo.child_object_id || 0);
            if (!childObjectId) { return; }
            const next = [];
            for (let j = 0; j < states.length; j += 1) {
                if (runId !== tokenResolveRunId) { return; }
                const state = states[j];
                const linksPayload = await cacheLinks(state.objectId, state.recordUid);
                const links = linksPayload && Array.isArray(linksPayload.links) ? linksPayload.links : [];
                const entry = links.find(function (item) { return Number(item.link_meta_id) === Number(step.linkMetaId); });
                if (!entry) { continue; }
                const selected = pickChildren(step, entry.child_record_uids || [], []);
                for (let k = 0; k < selected.length; k += 1) {
                    const uid = selected[k];
                    const visitKey = String(childObjectId) + ':' + uid;
                    if (state.visited.has(visitKey)) { continue; }
                    const visited = new Set(state.visited);
                    visited.add(visitKey);
                    next.push({ objectId: childObjectId, recordUid: uid, visited: visited });
                    await cacheRecord(childObjectId, uid);
                }
            }
            states = next;
            if (!states.length) { return; }
        }
    }

    async function prefetchTokenDependencies(spans, runId) {
        if (!window.docTokenParser || !Array.isArray(spans) || !spans.length) { return; }
        const parser = window.docTokenParser;
        const astByToken = new Map();
        spans.forEach(function (span) {
            const token = String(span.getAttribute('data-token') || '').trim();
            if (!token || astByToken.has(token)) { return; }
            const ast = parser.parseCanonicalToken(token);
            if (ast) {
                astByToken.set(token, ast);
            }
        });
        const astList = Array.from(astByToken.values());
        if (!astList.length) { return; }

        const tokenList = Array.from(astByToken.keys());
        const documentId = readDocumentIdFromMeta();
        const context = {};
        astList.forEach(function (ast) {
            const objectId = Number(ast && ast.objectId);
            if (!objectId) { return; }
            const selected = getCurrentSelectedRecordUid(objectId);
            if (selected) {
                context[String(objectId)] = String(selected);
            }
        });
        if (window.dbmApi && typeof window.dbmApi.prefetchGraph === 'function' && documentId) {
            try {
                const batchPayload = await window.dbmApi.prefetchGraph(
                    Number(documentId),
                    context,
                    tokenList,
                    { maxDepth: TOKEN_MAX_DEPTH, includeTrace: false }
                );
                if (runId !== tokenResolveRunId) { return; }
                const graph = batchPayload && batchPayload.graph ? batchPayload.graph : {};
                const graphRecords = Array.isArray(graph.records) ? graph.records : [];
                const graphLinks = Array.isArray(graph.links) ? graph.links : [];

                graphRecords.forEach(function (recordItem) {
                    const objectId = Number(recordItem && recordItem.object_id);
                    const recordUid = String(recordItem && recordItem.record_uid || '').trim();
                    if (!objectId || !recordUid) { return; }
                    const recordPayload = {
                        api_version: 'v1',
                        object_id: objectId,
                        record: {
                            record_uid: recordUid,
                            fields: (recordItem && recordItem.fields && typeof recordItem.fields === 'object') ? recordItem.fields : {},
                        },
                        schema: {
                            object_id: objectId,
                            parameters: {},
                        },
                    };
                    recordCache.set(String(objectId) + ':' + recordUid, Promise.resolve(recordPayload));
                });

                const groupedByParent = {};
                graphLinks.forEach(function (linkItem) {
                    const parentObjectId = Number(linkItem && linkItem.parent_object_id);
                    const parentRecordUid = String(linkItem && linkItem.parent_record_uid || '').trim();
                    const linkMetaId = Number(linkItem && linkItem.link_meta_id);
                    const childObjectId = Number(linkItem && linkItem.child_object_id);
                    const childRecordUid = String(linkItem && linkItem.child_record_uid || '').trim();
                    if (!parentObjectId || !parentRecordUid || !linkMetaId || !childRecordUid) { return; }
                    const parentKey = String(parentObjectId) + ':' + parentRecordUid;
                    if (!groupedByParent[parentKey]) {
                        groupedByParent[parentKey] = {};
                    }
                    if (!groupedByParent[parentKey][String(linkMetaId)]) {
                        groupedByParent[parentKey][String(linkMetaId)] = {
                            link_meta_id: linkMetaId,
                            child_object_id: childObjectId || 0,
                            child_object_name: '',
                            link_type: (((docTokenIndex.links_meta_by_id || {})[String(linkMetaId)] || {}).link_type) || 'single',
                            child_record_uids: [],
                        };
                    }
                    groupedByParent[parentKey][String(linkMetaId)].child_record_uids.push(childRecordUid);
                });

                Object.keys(groupedByParent).forEach(function (parentKey) {
                    const parts = parentKey.split(':');
                    const objectId = Number(parts[0] || 0);
                    const recordUid = parts.slice(1).join(':');
                    const links = Object.keys(groupedByParent[parentKey]).map(function (metaKey) {
                        const item = groupedByParent[parentKey][metaKey];
                        item.child_record_uids = Array.from(new Set(item.child_record_uids.map(function (value) {
                            return String(value || '').trim();
                        }).filter(Boolean))).sort();
                        return item;
                    });
                    linksCache.set(
                        parentKey,
                        Promise.resolve({
                            api_version: 'v1',
                            object_id: objectId,
                            record_uid: recordUid,
                            links: links,
                        })
                    );
                });
                return;
            } catch (_error) {
                // fallback to per-record fetch flow below
            }
        }

        let cursor = 0;
        const concurrency = Math.min(TOKEN_CONCURRENCY, astList.length);
        const runners = [];
        for (let i = 0; i < concurrency; i += 1) {
            runners.push((async function () {
                while (cursor < astList.length) {
                    const idx = cursor;
                    cursor += 1;
                    if (runId !== tokenResolveRunId) { return; }
                    try {
                        await prefetchTokenAst(astList[idx], runId);
                    } catch (_error) {
                        // Prefetch is best-effort, resolve step handles token-level errors.
                    }
                }
            })());
        }
        await Promise.all(runners);
    }

    function resolveDocumentTokensAsync() {
        if (!window.docTokenParser || !window.dbmApi || !window.dbmDto) { return; }
        const parser = window.docTokenParser;
        const spans = Array.from(document.querySelectorAll('span[data-token]')).filter(function (span) {
            const ast = parser.parseCanonicalToken(String(span.getAttribute('data-token') || '').trim());
            return !!ast;
        });
        if (!spans.length) { return; }
        tokenResolveRunId += 1;
        const runId = tokenResolveRunId;
        const warningText = function (code) {
            if (code === 'MULTIPLE_TO_FIRST') { return 'multiple→first'; }
            if (code === 'INDEX_OUT_OF_RANGE') { return 'index out of range'; }
            return String(code || '');
        };
        const worker = async function (span) {
            const ast = parser.parseCanonicalToken(String(span.getAttribute('data-token') || '').trim());
            if (!ast || runId !== tokenResolveRunId) { return; }
            markSpanAsReference(span);
            span.textContent = '⏳';
            try {
                const result = await resolveTokenAst(ast);
                if (runId !== tokenResolveRunId) { return; }
                const resolvedRaw = String((result && result.value) || '').trim();
                span.dataset.rawValue = resolvedRaw;
                span.textContent = resolvedRaw || '—';
                span.removeAttribute('data-token-error');
                if (result && result.warnings && result.warnings.length) { span.title = result.warnings.map(warningText).filter(Boolean).join(', '); }
            } catch (error) {
                if (runId !== tokenResolveRunId) { return; }
                span.dataset.rawValue = '';
                span.textContent = '—';
                span.setAttribute('data-token-error', String((error && error.code) || 'RESOLVE_ERROR'));
                span.title = String((error && error.message) || 'Не удалось вычислить значение токена.');
            }
        };
        const batches = [];
        for (let i = 0; i < spans.length; i += TOKEN_BATCH_SIZE) { batches.push(spans.slice(i, i + TOKEN_BATCH_SIZE)); }
        (async function () {
            let unresolved = 0;
            let resolved = 0;
            try {
                await prefetchTokenDependencies(spans, runId);
                if (runId !== tokenResolveRunId) { return; }
                for (let batchIndex = 0; batchIndex < batches.length; batchIndex += 1) {
                    let cursor = 0;
                    const batch = batches[batchIndex];
                    const runners = [];
                    const concurrency = Math.min(TOKEN_CONCURRENCY, batch.length);
                    for (let i = 0; i < concurrency; i += 1) {
                        runners.push((async function () {
                            while (cursor < batch.length) {
                                const idx = cursor; cursor += 1;
                                await worker(batch[idx]);
                            }
                        })());
                    }
                    await Promise.all(runners);
                }
                resolved = spans.length;
                unresolved = spans.filter(function (span) { return span.hasAttribute('data-token-error'); }).length;
            } finally {
                if (runId === tokenResolveRunId) {
                    emitUiEvent('dbm:tokens-resolved', {
                        runId: runId,
                        total: spans.length,
                        resolved: resolved,
                        unresolved: unresolved,
                    });
                }
            }
        })().catch(function (_error) {
            if (runId === tokenResolveRunId) {
                emitUiEvent('dbm:tokens-resolved', {
                    runId: runId,
                    total: spans.length,
                    resolved: 0,
                    unresolved: spans.length,
                });
            }
        });
    }

    let resolveAllTokenSpansTimer = null;
    function resolveAllTokenSpansFromDataInvis() {
        ensureCanonicalTokens();
        resolveDocumentTokensAsync();
    }
    function resolveAllTokenSpansFromDataInvisDebounced(delayMs) {
        const timeoutMs = Number(delayMs || 200);
        if (resolveAllTokenSpansTimer) {
            clearTimeout(resolveAllTokenSpansTimer);
        }
        resolveAllTokenSpansTimer = setTimeout(function () {
            resolveAllTokenSpansTimer = null;
            resolveAllTokenSpansFromDataInvis();
        }, timeoutMs > 0 ? timeoutMs : 200);
    }

    function patchLegacyReferenceResolver() {
        if (!window.docTokenParser || typeof window.getDataFromReference !== 'function') { return; }
        const parser = window.docTokenParser;
        const original = window.getDataFromReference;
        window.getDataFromReference = function (element) {
            try {
                const canonicalRaw = String((element && element.getAttribute && element.getAttribute('data-token')) || '').trim();
                let ast = parser.parseCanonicalToken(canonicalRaw);
                if (!ast && element && element.getAttribute) {
                    const rawToken = String(element.getAttribute('data-invis') || '').trim();
                    if (rawToken && rawToken.indexOf('{:') !== -1) {
                        const humanParsed = parser.parseHumanTokenToAst(rawToken, docTokenIndex);
                        if (humanParsed && humanParsed.ok) {
                            element.setAttribute('data-token', humanParsed.canonicalToken);
                            element.setAttribute('data-token-version', 'v1');
                            markSpanAsReference(element);
                            ast = humanParsed.ast;
                        }
                    }
                }
                if (ast) {
                    markSpanAsReference(element);
                    const resolved = valueRegistry.getValueByToken(ast);
                    if (resolved !== '') {
                        element.dataset.rawValue = String(resolved);
                        return resolved;
                    }
                }
            } catch (_error) {
                // no-op
            }
            const legacyValue = original(element);
            if (legacyValue !== undefined && legacyValue !== null) {
                try {
                    element.dataset.rawValue = String(legacyValue);
                } catch (_error) {
                    // no-op
                }
            }
            return legacyValue;
        };
    }

    function applyFieldsToObjectUI(fields) {
        Object.entries(fields || {}).forEach(function (entry) {
            const key = entry[0];
            const valueObj = window.dbmDto.normaliseField(entry[1]);
            const objectParameter = document.querySelector('.obj_parameter[data-idParam="' + key + '"]');
            if (!objectParameter) { return; }
            if (String(valueObj.type).toUpperCase() === 'ARRAY') {
                const container = objectParameter.querySelector('.array_data_container');
                if (!container) { return; }
                container.innerHTML = '';
                (Array.isArray(valueObj.value) ? valueObj.value : []).forEach(function (item) {
                    container.insertAdjacentHTML('beforeend', '<div class="array_data"><span>' + item + '</span></div>');
                });
                return;
            }
            const input = objectParameter.querySelector('input[name="' + key + '"]');
            if (!input) { return; }
            input.value = valueObj.value == null ? '' : String(valueObj.value);
            input.dispatchEvent(new Event('change'));
        });
    }

    let modalRecordPicker = null;
    function getDocumentId() { const meta = document.querySelector('meta[name="doc_id"]'); return meta ? String(meta.getAttribute('content') || '').trim() : ''; }
    function buildRecentStorageKey(objectId) { return 'recordPicker.recent.' + getDocumentId() + '.' + String(objectId); }
    function getObjectTitle(objectId) { const objectBlock = document.getElementById('object_' + objectId); const title = objectBlock ? objectBlock.querySelector('.object_header span') : null; return title ? String(title.textContent || '').trim() : 'Объект'; }
    function destroyModalPicker() { if (modalRecordPicker && typeof modalRecordPicker.destroy === 'function') { modalRecordPicker.destroy(); } modalRecordPicker = null; }
    function invalidateCaches() { linksCache.clear(); recordCache.clear(); }

    function linkedParentElementChanged(evt) { const linkedParam = evt.target.closest('.linked_parameter'); if (linkedParam && linkedParam.value) { getLinkedObjectData(linkedParam.getAttribute('data-linked_object_id'), linkedParam.value); } }
    function getLinkedObjectData(objId, recordUid) {
        window.dbmApi.getRecord(objId, recordUid).then(function (payload) {
            applyFieldsToObjectUI(window.dbmDto.normaliseRecordPayload(payload).fields);
            invalidateCaches();
            resolveDocumentTokensAsync();
            emitRecordSelected(objId, recordUid);
            $('#objectFindDataModal').modal('hide');
        }).catch(function (error) { console.error(error); showDbmError(error.message || 'Не удалось загрузить связанную запись'); });
    }
    function ConnectNewObject() { window.dbmApi.getObjectsToConnect().then(function (data) { let select = $('#objectSelect'); if (select.is('.select2-hidden-accessible')) { select.select2('destroy'); } select.empty(); data.object.forEach(function (obj) { select.append(new Option(obj.name, obj.id, false, false)).trigger('change'); }); select.select2({ width: '100%', dropdownParent: $('#modal_objectConnectModal_body'), multiple: true }); $('#objectConnectModal').modal('show'); }).catch(function (error) { console.error(error); showDbmError(error.message || 'Не удалось получить список объектов'); }); }
    function deleteObjectFromDocument(id) { if (!confirm('Вы точно хотите отвязать объект от документа?')) { return; } const docId = document.querySelector('meta[name="doc_id"]').getAttribute('content'); window.dbmApi.deleteObjectFromDocument(docId, id).then(function () { alert('Объект отвязан. Сохраните документ и обновите страницу.'); }).catch(function (error) { console.error(error); showDbmError(error.message || 'Не удалось удалить объект из документа'); }); }
    function addObjects() { const selectedObjects = Array.from(document.getElementById('objectSelect').selectedOptions).map(function (option) { return option.value; }); const docId = document.querySelector('meta[name="doc_id"]').getAttribute('content'); window.dbmApi.connectObjectsToDocument(docId, selectedObjects).then(function () { $('#objectConnectModal').modal('hide'); alert('Объекты подключены. Сохраните документ и обновите страницу.'); }).catch(function (error) { console.error(error); showDbmError(error.message || 'Не удалось подключить объекты'); }); }
    function getDataFromObject(objId, form) { const recordUid = new FormData(form).get('param_ident_id'); if (!recordUid) { showDbmError('Не выбран идентификатор записи'); return; } window.dbmApi.getRecord(objId, recordUid).then(function (payload) { applyFieldsToObjectUI(window.dbmDto.normaliseRecordPayload(payload).fields); invalidateCaches(); resolveDocumentTokensAsync(); emitRecordSelected(objId, recordUid); $('#objectFindDataModal').modal('hide'); }).catch(function (error) { console.error(error); showDbmError(error.message || 'Не удалось получить данные объекта'); }); }
    function click_to_ident(evt, objectId, value, paramIdentId) { const objectElement = document.getElementById('object_' + objectId); if (!objectElement) { return; } const input = objectElement.querySelector('input.identificator'); const inputFast = document.querySelector('.obj_parameter_fast[data-idObj="' + objectId + '"]'); const inputId = objectElement.querySelector('input[name="param_ident_id"]'); if (input) { input.value = value; input.dispatchEvent(new Event('change')); } if (inputId) { inputId.value = paramIdentId; } if (inputFast && inputFast.querySelector('input')) { inputFast.querySelector('input').value = value; } try { selectedIdent[objectId] = paramIdentId; } catch (_error) { } getDataFromObject(objectId, objectElement.querySelector('form')); }
    function updateObjectElement(objectId) { const ident = getCurrentSelectedRecordUid(objectId); if (ident) { location.href = '/database/update_element_to_object/' + objectId + '/?id=' + ident; } }
    function openFindDataModal(arg1, arg2) {
        const objectId = arg2 || arg1;
        const pickerHost = document.getElementById('objectFindRecordPicker');
        if (!pickerHost || !window.dbmRecordPicker) { showDbmError('Компонент выбора записи недоступен'); return; }
        const name = document.getElementById('objectFindDataModalName');
        if (name) { name.textContent = getObjectTitle(objectId); }
        destroyModalPicker();
        modalRecordPicker = window.dbmRecordPicker.createRecordPicker({
            objectId: objectId, containerEl: pickerHost, mode: 'single', order: 'identificator', pageSize: 50, pageSizeOptions: [5, 10, 15, 25, 50, 100, 200],
            persistKey: 'document:record-picker:' + getDocumentId() + ':' + objectId,
            recentKey: buildRecentStorageKey(objectId),
            onSelect: function (recordUid, record) { click_to_ident(null, objectId, (record && record.identificator) || '', recordUid); $('#objectFindDataModal').modal('hide'); },
            onEscape: function () { $('#objectFindDataModal').modal('hide'); },
        });
        $('#objectFindDataModal').modal('show');
    }
    function onFilterInputInput(evt) { if (evt && evt.preventDefault) { evt.preventDefault(); } }
    function getObjectData(evt, objectId) { const caller = evt && evt.target ? evt.target : null; if (caller) { caller.disabled = true; } Promise.resolve().then(function () { openFindDataModal(objectId); }).catch(function (error) { console.error(error); showDbmError(error.message || 'Не удалось получить список записей'); }).finally(function () { if (caller) { caller.disabled = false; } }); }

    const findModal = document.getElementById('objectFindDataModal');
    if (findModal) {
        findModal.addEventListener('hidden.bs.modal', function () { destroyModalPicker(); });
    }

    ensureCanonicalTokens();
    syncReferenceClassForKnownSpans();
    bootstrapValueRegistry();
    patchLegacyReferenceResolver();
    resolveDocumentTokensAsync();

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
        registerValueSource: valueRegistry.registerValueSource,
        registerLinkedValueSource: valueRegistry.registerLinkedValueSource,
        getValueByToken: valueRegistry.getValueByToken,
        ensureCanonicalTokens: ensureCanonicalTokens,
        resolveDocumentTokensAsync: resolveDocumentTokensAsync,
        resolveAllTokenSpansFromDataInvis: resolveAllTokenSpansFromDataInvis,
        resolveAllTokenSpansFromDataInvisDebounced: resolveAllTokenSpansFromDataInvisDebounced,
        markSpanAsReference: markSpanAsReference,
        getCurrentSelectedRecordUid: getCurrentSelectedRecordUid,
        getDocTokenIndex: function () { return docTokenIndex; },
        getObjectNameById: getObjectNameById,
        emitRecordSelected: emitRecordSelected,
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
