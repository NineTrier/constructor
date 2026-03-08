(function () {
    if (!window.DOCUMENT_LINK_TREE_UI) {
        return;
    }
    if (!window.dbmApi || !window.docTokenParser) {
        return;
    }

    const MAX_DEPTH = 8;
    const SCHEMA_MAX_DEPTH = 3;
    const MAX_CHILDREN_PER_ROLE = 50;
    const recordCache = new Map();
    const linksCache = new Map();

    function asArray(value) {
        return Array.isArray(value) ? value : [];
    }

    function concatSafe(left, right) {
        return asArray(left).concat(asArray(right));
    }

    function isDebugEnabled() {
        return !!window.DOCUMENT_LINK_TREE_DEBUG;
    }

    function debugLog(message, payload) {
        if (!isDebugEnabled()) {
            return;
        }
        try {
            console.debug('[DBM_TREE]', message, payload || {});
        } catch (_error) {
            // no-op
        }
    }

    function getIntegrationApi() {
        return window.dbmIntegration || window.documentDbmIntegration || null;
    }

    function getDocTokenIndex() {
        const integration = getIntegrationApi();
        if (integration && typeof integration.getDocTokenIndex === 'function') {
            return integration.getDocTokenIndex() || {};
        }
        const node = document.getElementById('doc-token-index');
        if (!node) {
            return {};
        }
        try {
            return JSON.parse(node.textContent || '{}') || {};
        } catch (_error) {
            return {};
        }
    }

    const tokenIndex = getDocTokenIndex();

    function injectStyles() {
        if (document.getElementById('dbm-tree-style')) {
            return;
        }
        const style = document.createElement('style');
        style.id = 'dbm-tree-style';
        style.textContent = '' +
            '.dbm-tree-host{border:1px solid #dee2e6;border-radius:6px;padding:8px;max-height:380px;overflow:auto;background:#fff;}\n' +
            '.dbm-tree-list{list-style:none;padding-left:14px;margin:0;}\n' +
            '.dbm-tree-row{display:flex;align-items:center;gap:6px;line-height:1.35;padding:2px 0;}\n' +
            '.dbm-tree-expand{border:0;background:transparent;padding:0 2px;color:#0d6efd;cursor:pointer;}\n' +
            '.dbm-tree-expand[disabled]{color:#6c757d;cursor:default;}\n' +
            '.dbm-tree-label{font-size:13px;}\n' +
            '.dbm-tree-param-btn{border:0;background:transparent;padding:0;color:#198754;text-align:left;font-size:13px;cursor:pointer;}\n' +
            '.dbm-tree-param-btn:hover{text-decoration:underline;}\n' +
            '.dbm-tree-muted{font-size:12px;color:#6c757d;}\n' +
            '.dbm-tree-error{font-size:12px;color:#dc3545;}\n' +
            '.dbm-tree-selector .btn{padding:0 6px;font-size:11px;line-height:1.2;}\n' +
            '.dbm-tree-value{font-size:12px;color:#6c757d;max-width:360px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}\n' +
            '.dbm-tree-actions{margin-left:auto;display:flex;gap:6px;}\n' +
            '.dbm-tree-toast{position:fixed;right:16px;bottom:16px;z-index:2500;min-width:220px;}';
        document.head.appendChild(style);
    }

    function renderHostText(host, className, text) {
        host.innerHTML = '';
        const node = document.createElement('div');
        node.className = className;
        node.textContent = text;
        host.appendChild(node);
    }

    function renderHostError(host, text) {
        renderHostText(host, 'dbm-tree-error', text || 'Не удалось построить дерево.');
    }

    function objectNameById(objectId) {
        const integration = getIntegrationApi();
        if (integration && typeof integration.getObjectNameById === 'function') {
            const name = integration.getObjectNameById(objectId);
            if (name) {
                return name;
            }
        }
        const map = tokenIndex.objects_by_name || {};
        let result = String(objectId);
        Object.keys(map).forEach(function (name) {
            if (Number(map[name]) === Number(objectId)) {
                result = name;
            }
        });
        return result;
    }

    function getHumanPathSegments(context, paramName) {
        const rootName = String(context.rootObjectName || objectNameById(context.rootObjectId || context.objectId));
        const roleNames = asArray(context.roleSteps).map(function (step) {
            return String((step && step.displayName) || (step && step.metaId) || '').trim();
        }).filter(Boolean);
        const segments = concatSafe([rootName], roleNames);
        if (paramName != null && String(paramName).trim()) {
            return concatSafe(segments, [String(paramName).trim()]);
        }
        return segments;
    }

    function paramsForObject(objectId) {
        const map = ((tokenIndex.params_by_object_and_name || {})[String(objectId)] || {});
        const linkParamIds = new Set(Object.values(tokenIndex.link_param_by_meta_id || {}).map(function (value) { return Number(value); }));
        const rows = [];
        Object.keys(map).forEach(function (name) {
            const id = Number(map[name]);
            if (!id || linkParamIds.has(id)) {
                return;
            }
            rows.push({ id: id, name: String(name) });
        });
        rows.sort(function (a, b) {
            return String(a.name).localeCompare(String(b.name), 'ru');
        });
        return rows;
    }

    function metasForParent(parentObjectId) {
        const byId = tokenIndex.links_meta_by_id || {};
        const rows = [];
        Object.keys(byId).forEach(function (metaId) {
            const meta = byId[metaId] || {};
            if (Number(meta.parent_object_id) !== Number(parentObjectId)) {
                return;
            }
            rows.push({
                id: Number(metaId),
                parent_object_id: Number(meta.parent_object_id),
                child_object_id: Number(meta.child_object_id),
                display_name: String(meta.display_name || metaId),
                link_type: String(meta.link_type || 'single'),
                order: Number(meta.order || 0),
            });
        });
        rows.sort(function (a, b) {
            if (a.order !== b.order) {
                return a.order - b.order;
            }
            return a.id - b.id;
        });
        return rows;
    }

    function getCurrentRecordUid(objectId) {
        const integration = getIntegrationApi();
        if (integration && typeof integration.getCurrentSelectedRecordUid === 'function') {
            return String(integration.getCurrentSelectedRecordUid(objectId) || '').trim();
        }
        const objectElement = document.getElementById('object_' + objectId);
        const input = objectElement ? objectElement.querySelector('input[name="param_ident_id"]') : null;
        return input ? String(input.value || '').trim() : '';
    }

    function cacheRecord(objectId, recordUid) {
        const key = String(objectId) + ':' + String(recordUid);
        if (recordCache.has(key)) {
            return recordCache.get(key);
        }
        const promise = window.dbmApi.getRecord(objectId, recordUid).catch(function (error) {
            recordCache.delete(key);
            throw error;
        });
        recordCache.set(key, promise);
        return promise;
    }

    function cacheLinks(objectId, recordUid) {
        const key = String(objectId) + ':' + String(recordUid);
        if (linksCache.has(key)) {
            return linksCache.get(key);
        }
        const promise = window.dbmApi.getLinks(objectId, recordUid).catch(function (error) {
            linksCache.delete(key);
            throw error;
        });
        linksCache.set(key, promise);
        return promise;
    }

    function selectorStorageKey(rootObjectId, chain) {
        return 'dbmTree.selector.' + String(rootObjectId) + '.' + asArray(chain).map(function (item) { return item.metaId; }).join('-');
    }

    function selectorForChain(rootObjectId, chain, linkType) {
        if (String(linkType) !== 'multiple') {
            return 'single';
        }
        const key = selectorStorageKey(rootObjectId, chain);
        const stored = String(localStorage.getItem(key) || '').trim();
        if (stored === 'all') {
            return 'all';
        }
        return 'index';
    }

    function setSelectorForChain(rootObjectId, chain, selector) {
        const key = selectorStorageKey(rootObjectId, chain);
        localStorage.setItem(key, selector === 'all' ? 'all' : 'index');
    }

    function canonicalToken(rootObjectId, roleSteps, paramId) {
        let token = '{:obj(' + Number(rootObjectId) + ')';
        asArray(roleSteps).forEach(function (step) {
            token += '.link(' + Number(step.metaId) + ')';
            if (step.selector === 'all') {
                token += '[*]';
            } else if (step.selector === 'index') {
                token += '[0]';
            }
        });
        token += '.param(' + Number(paramId) + '):}';
        return token;
    }

    function humanToken(rootObjectName, roleSteps, paramName) {
        const segments = [String(rootObjectName)];
        asArray(roleSteps).forEach(function (step) {
            let part = String(step.displayName || step.metaId);
            if (step.selector === 'all') {
                part += '[*]';
            } else if (step.selector === 'index') {
                part += '[0]';
            }
            segments.push(part);
        });
        segments.push(String(paramName));
        return '{: ' + segments.join('.') + ' :}';
    }

    function getSelectionRangeInDocument() {
        const documentMain = document.getElementById('document_main');
        if (!documentMain) {
            return null;
        }
        const selection = window.getSelection();
        if (!selection || !selection.rangeCount) {
            return null;
        }
        const range = selection.getRangeAt(0);
        const container = range.commonAncestorContainer && range.commonAncestorContainer.nodeType === 1
            ? range.commonAncestorContainer
            : range.commonAncestorContainer && range.commonAncestorContainer.parentElement;
        if (!container || !documentMain.contains(container)) {
            return null;
        }
        return range;
    }

    function hasSelectionInDocument() {
        return !!getSelectionRangeInDocument();
    }

    function showCopyToast(message, typeClass) {
        const className = typeClass || 'alert-success';
        const text = String(message || 'Скопировано');
        const hostId = 'dbm-tree-toast-host';
        let host = document.getElementById(hostId);
        if (!host) {
            host = document.createElement('div');
            host.id = hostId;
            host.className = 'dbm-tree-toast';
            document.body.appendChild(host);
        }
        const alert = document.createElement('div');
        alert.className = 'alert ' + className + ' py-2 px-3 mb-2';
        alert.textContent = text;
        host.appendChild(alert);
        setTimeout(function () {
            if (alert.parentNode) {
                alert.parentNode.removeChild(alert);
            }
        }, 1800);
    }

    async function copyToClipboard(text) {
        const payload = String(text || '').trim();
        if (!payload) {
            return false;
        }
        try {
            if (navigator.clipboard && typeof navigator.clipboard.writeText === 'function') {
                await navigator.clipboard.writeText(payload);
                return true;
            }
        } catch (_error) {
            // fallback below
        }
        let textarea = null;
        try {
            textarea = document.createElement('textarea');
            textarea.value = payload;
            textarea.setAttribute('readonly', 'readonly');
            textarea.style.position = 'fixed';
            textarea.style.opacity = '0';
            textarea.style.pointerEvents = 'none';
            document.body.appendChild(textarea);
            textarea.select();
            const copied = document.execCommand('copy');
            return !!copied;
        } catch (_error) {
            return false;
        } finally {
            if (textarea && textarea.parentNode) {
                textarea.parentNode.removeChild(textarea);
            }
        }
    }

    function insertTokenAtSelection(human, canonical) {
        const range = getSelectionRangeInDocument();
        if (!range) {
            alert('Выберите место в тексте документа для вставки токена.');
            return false;
        }
        const span = document.createElement('span');
        span.classList.add('runs', 'docelement', 'reference_to_data', 'object_param_ref');
        span.textContent = human;
        span.setAttribute('data-invis', human);
        span.setAttribute('data-name', human);
        span.setAttribute('data-token', canonical);
        span.setAttribute('data-token-version', 'v1');
        span.setAttribute('data-human-token', human);
        if (typeof window.SetDefaultRun === 'function') {
            window.SetDefaultRun(span);
        }

        range.deleteContents();
        range.insertNode(span);
        range.setStartAfter(span);
        range.collapse(true);
        const selection = window.getSelection();
        selection.removeAllRanges();
        selection.addRange(range);

        const integration = getIntegrationApi();
        if (integration && typeof integration.resolveDocumentTokensAsync === 'function') {
            integration.resolveDocumentTokensAsync();
        }
        if (typeof window.FillTree === 'function') {
            const docTree = document.querySelector('#document_tree');
            const paragraphs = document.querySelectorAll('#document_main .paragraph');
            if (docTree && paragraphs) {
                window.FillTree(docTree, paragraphs);
            }
        }
        return true;
    }

    function recordIdentificator(recordPayload) {
        const payload = window.dbmDto && typeof window.dbmDto.normaliseRecordPayload === 'function'
            ? window.dbmDto.normaliseRecordPayload(recordPayload || {})
            : (recordPayload || {});
        return String(payload.identificator || payload.record_uid || '').trim();
    }

    function recordFieldValue(recordPayload, paramId) {
        if (window.dbmDto && typeof window.dbmDto.normaliseRecordPayload === 'function') {
            const payload = window.dbmDto.normaliseRecordPayload(recordPayload || {});
            const field = window.dbmDto.normaliseField((payload.fields || {})[String(paramId)] || {});
            if (Array.isArray(field.value)) {
                return field.value.join(', ');
            }
            return field.value == null ? '' : String(field.value);
        }
        const fields = (recordPayload && recordPayload.fields) || {};
        const raw = fields[String(paramId)];
        if (raw && typeof raw === 'object' && 'value' in raw) {
            if (Array.isArray(raw.value)) {
                return raw.value.join(', ');
            }
            return raw.value == null ? '' : String(raw.value);
        }
        return '';
    }

    async function renderParams(listEl, context) {
        const params = paramsForObject(context.objectId);
        const shouldLoadValues = context.mode === 'values' && String(context.recordUid || '').trim();
        const recordPayload = shouldLoadValues
            ? await cacheRecord(context.objectId, context.recordUid).catch(function () { return null; })
            : null;
        if (!params.length) {
            const empty = document.createElement('li');
            empty.className = 'dbm-tree-muted';
            empty.textContent = 'Нет параметров.';
            listEl.appendChild(empty);
            return;
        }

        params.forEach(function (param) {
            const li = document.createElement('li');
            const row = document.createElement('div');
            row.className = 'dbm-tree-row';
            const icon = document.createElement('span');
            icon.textContent = '🏷️';
            const btn = document.createElement('button');
            btn.type = 'button';
            btn.className = 'dbm-tree-param-btn';
            btn.textContent = param.name;
            btn.title = 'Копировать токен: ' + getHumanPathSegments(context, param.name).join('.');

            const value = document.createElement('span');
            value.className = 'dbm-tree-value';
            const currentValue = recordPayload ? recordFieldValue(recordPayload, param.id) : '';
            value.textContent = currentValue || '—';

            btn.addEventListener('click', async function () {
                const human = humanToken(context.rootObjectName, asArray(context.roleSteps), param.name);
                const copied = await copyToClipboard(human);
                if (copied) {
                    showCopyToast('Скопировано');
                } else {
                    showCopyToast('Не удалось скопировать токен', 'alert-danger');
                }
            });

            const actions = document.createElement('div');
            actions.className = 'dbm-tree-actions';
            if (hasSelectionInDocument()) {
                const insertBtn = document.createElement('button');
                insertBtn.type = 'button';
                insertBtn.className = 'btn btn-sm btn-outline-primary';
                insertBtn.textContent = 'Вставить';
                insertBtn.addEventListener('click', function (event) {
                    event.stopPropagation();
                    if (!hasSelectionInDocument()) {
                        showCopyToast('Нет выделения в документе', 'alert-warning');
                        return;
                    }
                    const human = humanToken(context.rootObjectName, asArray(context.roleSteps), param.name);
                    const canonical = canonicalToken(context.rootObjectId, asArray(context.roleSteps), param.id);
                    if (insertTokenAtSelection(human, canonical)) {
                        showCopyToast('Токен вставлен');
                    }
                });
                actions.appendChild(insertBtn);
            }

            row.appendChild(icon);
            row.appendChild(btn);
            row.appendChild(value);
            row.appendChild(actions);
            li.appendChild(row);
            listEl.appendChild(li);
        });
    }

    function roleSelectorControls(roleMeta, context, onChanged) {
        if (String(roleMeta.link_type) !== 'multiple') {
            return null;
        }
        const wrapper = document.createElement('div');
        wrapper.className = 'btn-group btn-group-sm dbm-tree-selector';
        const firstBtn = document.createElement('button');
        firstBtn.type = 'button';
        firstBtn.className = 'btn btn-outline-secondary';
        firstBtn.textContent = 'Первый';
        const allBtn = document.createElement('button');
        allBtn.type = 'button';
        allBtn.className = 'btn btn-outline-secondary';
        allBtn.textContent = 'Все';

        const chain = concatSafe(context.roleSteps, [{ metaId: roleMeta.id }]);
        const current = selectorForChain(context.rootObjectId, chain, roleMeta.link_type);
        if (current === 'all') {
            allBtn.classList.add('active');
        } else {
            firstBtn.classList.add('active');
        }

        firstBtn.addEventListener('click', function (evt) {
            evt.stopPropagation();
            setSelectorForChain(context.rootObjectId, chain, 'index');
            onChanged();
        });
        allBtn.addEventListener('click', function (evt) {
            evt.stopPropagation();
            setSelectorForChain(context.rootObjectId, chain, 'all');
            onChanged();
        });

        wrapper.appendChild(firstBtn);
        wrapper.appendChild(allBtn);
        return wrapper;
    }

    function normaliseChildRecordUids(entry) {
        const direct = entry && entry.child_record_uids;
        if (Array.isArray(direct)) {
            return direct;
        }
        if (typeof direct === 'string' && direct.trim()) {
            return [direct.trim()];
        }
        const legacy = entry && entry.child_record_uid;
        if (typeof legacy === 'string' && legacy.trim()) {
            return [legacy.trim()];
        }
        return [];
    }

    async function renderRoleChildrenValue(container, roleMeta, context) {
        container.innerHTML = '';
        if (context.depth >= MAX_DEPTH) {
            const depthWarn = document.createElement('div');
            depthWarn.className = 'dbm-tree-error';
            depthWarn.textContent = 'Достигнута максимальная глубина дерева.';
            container.appendChild(depthWarn);
            return;
        }

        const linksPayload = await cacheLinks(context.objectId, context.recordUid).catch(function (error) {
            const node = document.createElement('div');
            node.className = 'dbm-tree-error';
            node.textContent = error && error.message ? error.message : 'Ошибка загрузки связей.';
            container.appendChild(node);
            return null;
        });
        if (!linksPayload) {
            return;
        }

        const links = asArray(linksPayload.links);
        const entry = links.find(function (item) {
            return Number(item && item.link_meta_id) === Number(roleMeta.id);
        });
        const childUids = normaliseChildRecordUids(entry)
            .map(function (item) { return String(item || '').trim(); })
            .filter(Boolean)
            .sort();

        if (!childUids.length) {
            const node = document.createElement('div');
            node.className = 'dbm-tree-muted';
            node.textContent = 'Связанные записи не выбраны.';
            container.appendChild(node);
            return;
        }

        const selector = selectorForChain(
            context.rootObjectId,
            concatSafe(context.roleSteps, [{ metaId: roleMeta.id }]),
            roleMeta.link_type
        );
        const selectedUids = (String(roleMeta.link_type) === 'multiple' && selector === 'all')
            ? childUids
            : asArray(childUids.length ? [childUids[0]] : []);

        const limitedUids = selectedUids.slice(0, MAX_CHILDREN_PER_ROLE);
        const list = document.createElement('ul');
        list.className = 'dbm-tree-list';
        container.appendChild(list);

        for (let i = 0; i < limitedUids.length; i += 1) {
            const childUid = limitedUids[i];
            const visitKey = String(roleMeta.child_object_id) + ':' + childUid;
            if (context.visited.has(visitKey)) {
                const cycleNode = document.createElement('li');
                cycleNode.className = 'dbm-tree-error';
                cycleNode.textContent = 'Цикл: ' + visitKey;
                list.appendChild(cycleNode);
                continue;
            }

            const childLi = document.createElement('li');
            const row = document.createElement('div');
            row.className = 'dbm-tree-row';
            row.innerHTML = '<span>📦</span><span class="dbm-tree-label"></span>';
            childLi.appendChild(row);
            list.appendChild(childLi);

            const label = row.querySelector('.dbm-tree-label');
            try {
                const childRecordPayload = await cacheRecord(roleMeta.child_object_id, childUid);
                const ident = recordIdentificator(childRecordPayload);
                label.textContent = ident ? (objectNameById(roleMeta.child_object_id) + ' [' + ident + ']') : (objectNameById(roleMeta.child_object_id) + ' [' + childUid + ']');
            } catch (_error) {
                label.textContent = objectNameById(roleMeta.child_object_id) + ' [' + childUid + ']';
            }

            const childCtx = {
                mode: 'values',
                rootObjectId: context.rootObjectId,
                rootObjectName: context.rootObjectName,
                objectId: roleMeta.child_object_id,
                objectName: objectNameById(roleMeta.child_object_id),
                recordUid: childUid,
                depth: context.depth + 1,
                roleSteps: concatSafe(context.roleSteps, [{
                    metaId: roleMeta.id,
                    displayName: roleMeta.display_name,
                    selector: String(roleMeta.link_type) === 'multiple' ? selector : 'single',
                    linkType: roleMeta.link_type,
                }]),
                visited: new Set(concatSafe(Array.from(context.visited), [visitKey])),
            };

            const childParams = document.createElement('ul');
            childParams.className = 'dbm-tree-list';
            childLi.appendChild(childParams);
            await renderParams(childParams, childCtx);

            const childRoles = metasForParent(childCtx.objectId);
            for (let roleIdx = 0; roleIdx < childRoles.length; roleIdx += 1) {
                renderRoleNode(childParams, childRoles[roleIdx], childCtx);
            }
        }

        if (selectedUids.length > MAX_CHILDREN_PER_ROLE) {
            const tail = document.createElement('div');
            tail.className = 'dbm-tree-muted';
            tail.textContent = 'Показаны первые ' + MAX_CHILDREN_PER_ROLE + ' записей.';
            container.appendChild(tail);
        }
    }

    function renderRoleChildrenSchema(container, roleMeta, context) {
        container.innerHTML = '';
        if (context.depth >= SCHEMA_MAX_DEPTH) {
            const depthWarn = document.createElement('div');
            depthWarn.className = 'dbm-tree-muted';
            depthWarn.textContent = 'Глубина схемы ограничена.';
            container.appendChild(depthWarn);
            return Promise.resolve();
        }

        const childObjectId = Number(roleMeta.child_object_id || 0);
        if (!childObjectId) {
            const node = document.createElement('div');
            node.className = 'dbm-tree-error';
            node.textContent = 'Не найден дочерний объект для роли.';
            container.appendChild(node);
            return Promise.resolve();
        }

        if (context.visitedObjects && context.visitedObjects.has(childObjectId)) {
            const cycleNode = document.createElement('div');
            cycleNode.className = 'dbm-tree-error';
            cycleNode.textContent = 'Цикл в схеме связей: объект уже в цепочке.';
            container.appendChild(cycleNode);
            return Promise.resolve();
        }

        const selector = selectorForChain(
            context.rootObjectId,
            concatSafe(context.roleSteps, [{ metaId: roleMeta.id }]),
            roleMeta.link_type
        );
        const childCtx = {
            mode: 'schema',
            rootObjectId: context.rootObjectId,
            rootObjectName: context.rootObjectName,
            objectId: childObjectId,
            objectName: objectNameById(childObjectId),
            recordUid: '',
            depth: context.depth + 1,
            roleSteps: concatSafe(context.roleSteps, [{
                metaId: roleMeta.id,
                displayName: roleMeta.display_name,
                selector: String(roleMeta.link_type) === 'multiple' ? selector : 'single',
                linkType: roleMeta.link_type,
            }]),
            visited: new Set(),
            visitedObjects: new Set(concatSafe(Array.from(context.visitedObjects || []), [childObjectId])),
        };

        const list = document.createElement('ul');
        list.className = 'dbm-tree-list';
        container.appendChild(list);

        const childLi = document.createElement('li');
        const row = document.createElement('div');
        row.className = 'dbm-tree-row';
        row.innerHTML = '<span>📦</span><span class="dbm-tree-label"></span>';
        const label = row.querySelector('.dbm-tree-label');
        label.textContent = childCtx.objectName;
        childLi.appendChild(row);
        list.appendChild(childLi);

        const childParams = document.createElement('ul');
        childParams.className = 'dbm-tree-list';
        childLi.appendChild(childParams);

        return renderParams(childParams, childCtx).then(function () {
            const childRoles = metasForParent(childCtx.objectId);
            for (let roleIdx = 0; roleIdx < childRoles.length; roleIdx += 1) {
                renderRoleNode(childParams, childRoles[roleIdx], childCtx);
            }
        });
    }

    function renderRoleNode(parentList, roleMeta, context) {
        const li = document.createElement('li');
        const row = document.createElement('div');
        row.className = 'dbm-tree-row';

        const expandBtn = document.createElement('button');
        expandBtn.type = 'button';
        expandBtn.className = 'dbm-tree-expand';
        expandBtn.textContent = '▸';

        const icon = document.createElement('span');
        icon.textContent = '🔗';
        const label = document.createElement('span');
        label.className = 'dbm-tree-label';
        label.textContent = roleMeta.display_name;

        const childrenContainer = document.createElement('div');
        childrenContainer.className = 'ms-3';
        childrenContainer.hidden = true;
        let loaded = false;

        const rerenderRole = function () {
            loaded = false;
            childrenContainer.hidden = false;
            expandBtn.textContent = '▾';
            childrenContainer.innerHTML = '<div class="dbm-tree-muted">Загрузка...</div>';
            const renderChildren = context.mode === 'values' ? renderRoleChildrenValue : renderRoleChildrenSchema;
            renderChildren(childrenContainer, roleMeta, context).then(function () {
                loaded = true;
            }).catch(function (error) {
                childrenContainer.innerHTML = '';
                const node = document.createElement('div');
                node.className = 'dbm-tree-error';
                node.textContent = String((error && error.message) || 'Ошибка загрузки связей.');
                childrenContainer.appendChild(node);
            });
        };

        const selectorControls = roleSelectorControls(roleMeta, context, rerenderRole);

        expandBtn.addEventListener('click', function () {
            if (childrenContainer.hidden) {
                childrenContainer.hidden = false;
                expandBtn.textContent = '▾';
                if (!loaded) {
                    childrenContainer.innerHTML = '<div class="dbm-tree-muted">Загрузка...</div>';
                    const renderChildren = context.mode === 'values' ? renderRoleChildrenValue : renderRoleChildrenSchema;
                    renderChildren(childrenContainer, roleMeta, context).then(function () {
                        loaded = true;
                    }).catch(function (error) {
                        childrenContainer.innerHTML = '';
                        const node = document.createElement('div');
                        node.className = 'dbm-tree-error';
                        node.textContent = String((error && error.message) || 'Ошибка загрузки связей.');
                        childrenContainer.appendChild(node);
                    });
                }
                return;
            }
            childrenContainer.hidden = true;
            expandBtn.textContent = '▸';
        });

        row.appendChild(expandBtn);
        row.appendChild(icon);
        row.appendChild(label);
        if (selectorControls) {
            row.appendChild(selectorControls);
        }

        li.appendChild(row);
        li.appendChild(childrenContainer);
        parentList.appendChild(li);
    }

    async function renderTreeByMode(host, objectId, mode, forcedRootUid) {
        host.innerHTML = '';
        const rawMode = String(mode || '').toLowerCase();
        const rootUid = rawMode === 'values'
            ? String(forcedRootUid || getCurrentRecordUid(objectId) || '').trim()
            : '';
        const resolvedMode = rawMode === 'values' && rootUid ? 'values' : 'schema';
        const metasCount = metasForParent(objectId).length;
        debugLog('render-root-tree', {
            objectId: Number(objectId || 0),
            rootUid: rootUid || '',
            metasCount: metasCount,
            mode: resolvedMode,
        });

        const rootCtx = {
            mode: resolvedMode,
            rootObjectId: Number(objectId),
            rootObjectName: objectNameById(objectId),
            objectId: Number(objectId),
            objectName: objectNameById(objectId),
            recordUid: rootUid,
            depth: 0,
            roleSteps: [],
            visited: new Set(rootUid ? [String(objectId) + ':' + String(rootUid)] : []),
            visitedObjects: new Set([Number(objectId)]),
        };

        if (resolvedMode === 'schema') {
            const hint = document.createElement('div');
            hint.className = 'dbm-tree-muted';
            hint.textContent = 'Выберите запись, чтобы увидеть значения. Токены доступны уже сейчас.';
            host.appendChild(hint);
        }

        const rootList = document.createElement('ul');
        rootList.className = 'dbm-tree-list';
        host.appendChild(rootList);

        const titleNode = document.createElement('li');
        titleNode.innerHTML = '<div class="dbm-tree-row"><span>📦</span><span class="dbm-tree-label"></span></div>';
        const titleLabel = titleNode.querySelector('.dbm-tree-label');
        titleLabel.textContent = resolvedMode === 'values'
            ? (rootCtx.rootObjectName + ' [' + rootUid + ']')
            : rootCtx.rootObjectName;
        rootList.appendChild(titleNode);

        const innerList = document.createElement('ul');
        innerList.className = 'dbm-tree-list';
        titleNode.appendChild(innerList);

        await renderParams(innerList, rootCtx);
        metasForParent(objectId).forEach(function (meta) {
            renderRoleNode(innerList, meta, rootCtx);
        });
    }

    async function buildSchemaTree(host, objectId) {
        return renderTreeByMode(host, objectId, 'schema', '');
    }

    async function buildValueTree(host, objectId, rootUid) {
        return renderTreeByMode(host, objectId, 'values', rootUid);
    }

    async function renderRootTree(host, objectId) {
        const currentRootUid = getCurrentRecordUid(objectId);
        if (currentRootUid) {
            return buildValueTree(host, objectId, currentRootUid);
        }
        return buildSchemaTree(host, objectId);
    }

    function attachTree(host, objectId) {
        const oid = Number(objectId || 0);
        if (!oid || !host) {
            return;
        }
        if (host.dataset.dbmTreeBound === '1') {
            return;
        }
        host.dataset.dbmTreeBound = '1';

        const rerender = function () {
            try {
                renderRootTree(host, oid).catch(function (error) {
                    renderHostError(host, String((error && error.message) || 'Не удалось построить дерево.'));
                });
            } catch (error) {
                renderHostError(host, String((error && error.message) || 'Не удалось построить дерево.'));
            }
        };

        rerender();
        document.addEventListener('dbm:record-selected', function (event) {
            const detail = event && event.detail ? event.detail : {};
            if (Number(detail.objectId) !== oid) {
                return;
            }
            rerender();
        });
    }

    function init() {
        injectStyles();
        document.querySelectorAll('[data-dbmtree-object]').forEach(function (node) {
            const objectId = Number(node.getAttribute('data-dbmtree-object') || 0);
            const host = node.querySelector('.dbm-tree-host');
            attachTree(host, objectId);
        });
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }

    window.documentDbmTree = {
        rebuildAll: function () {
            document.querySelectorAll('[data-dbmtree-object] .dbm-tree-host').forEach(function (host) {
                host.dataset.dbmTreeBound = '';
            });
            init();
        },
    };
})();
