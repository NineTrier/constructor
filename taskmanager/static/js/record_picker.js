(function () {
    const CACHE_MAX_ITEMS = 100;
    const CACHE_TTL_MS = 60 * 1000;
    const RESPONSE_CACHE = new Map();
    let stylesInjected = false;

    const defaultI18n = {
        placeholderSearch: "Поиск по идентификатору...",
        clear: "Очистить",
        loading: "Загрузка...",
        empty: "Ничего не найдено",
        error: "Ошибка загрузки",
        retry: "Повторить",
        prev: "Назад",
        next: "Вперёд",
        shown: "Показано",
        found: "Найдено",
        limitLabel: "Показывать по",
        viewList: "Список",
        viewChips: "Плитки",
        minCharsHint: "Введите минимум 2 символа",
        querySuffix: "по запросу",
        recentTitle: "Последние выбранные",
    };

    function defaultFn() {}

    function toNumber(value, fallback) {
        const parsed = Number(value);
        return Number.isFinite(parsed) ? parsed : fallback;
    }

    function ensureStyles() {
        if (stylesInjected) {
            return;
        }
        if (!document || !document.head) {
            return;
        }
        stylesInjected = true;
        const style = document.createElement("style");
        style.type = "text/css";
        style.setAttribute("data-record-picker-style", "1");
        style.textContent = [
            ".record-picker .rp-highlight{background:rgba(255,235,59,.4);border-radius:2px;}",
            ".record-picker .rp-active{outline:2px solid rgba(13,110,253,.35);}",
            ".record-picker .rp-chip-list{display:flex;flex-wrap:wrap;gap:.5rem;}",
            ".record-picker .rp-chip{max-width:220px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;}",
            ".record-picker .rp-recent{display:flex;flex-wrap:wrap;gap:.5rem;margin-bottom:.5rem;}",
            ".record-picker .rp-status{min-height:1.2rem;}",
        ].join("");
        document.head.appendChild(style);
    }

    function mergeI18n(overrides) {
        const merged = {};
        const source = overrides && typeof overrides === "object" ? overrides : {};
        Object.keys(defaultI18n).forEach(function (key) {
            if (source[key] == null) {
                merged[key] = defaultI18n[key];
            } else {
                merged[key] = String(source[key]);
            }
        });
        return merged;
    }

    function normaliseViewMode(value) {
        const mode = String(value || "").toLowerCase();
        return mode === "chips" ? "chips" : "list";
    }

    function createElement(tag, className) {
        const el = document.createElement(tag);
        if (className) {
            el.className = className;
        }
        return el;
    }

    function safeJsonParse(raw, fallback) {
        try {
            const payload = JSON.parse(String(raw || ""));
            return payload == null ? fallback : payload;
        } catch (_error) {
            return fallback;
        }
    }

    function escapeRegExp(value) {
        return String(value || "").replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
    }

    function appendHighlightedText(target, text, query) {
        target.textContent = "";
        const source = String(text || "");
        const q = String(query || "").trim();
        if (!q || q.length < 2) {
            target.appendChild(document.createTextNode(source));
            return;
        }
        const regex = new RegExp(escapeRegExp(q), "ig");
        let cursor = 0;
        let match = regex.exec(source);
        while (match) {
            const start = match.index;
            const end = start + match[0].length;
            if (start > cursor) {
                target.appendChild(document.createTextNode(source.slice(cursor, start)));
            }
            const highlight = document.createElement("span");
            highlight.className = "rp-highlight";
            highlight.textContent = source.slice(start, end);
            target.appendChild(highlight);
            cursor = end;
            match = regex.exec(source);
        }
        if (cursor < source.length) {
            target.appendChild(document.createTextNode(source.slice(cursor)));
        }
    }

    function cacheGet(key) {
        const entry = RESPONSE_CACHE.get(key);
        if (!entry) {
            return null;
        }
        if (Date.now() - entry.ts > CACHE_TTL_MS) {
            RESPONSE_CACHE.delete(key);
            return null;
        }
        RESPONSE_CACHE.delete(key);
        RESPONSE_CACHE.set(key, entry);
        return entry.payload;
    }

    function cacheSet(key, payload) {
        if (!key) {
            return;
        }
        if (RESPONSE_CACHE.has(key)) {
            RESPONSE_CACHE.delete(key);
        }
        RESPONSE_CACHE.set(key, { ts: Date.now(), payload: payload });
        while (RESPONSE_CACHE.size > CACHE_MAX_ITEMS) {
            const firstKey = RESPONSE_CACHE.keys().next().value;
            if (firstKey == null) {
                break;
            }
            RESPONSE_CACHE.delete(firstKey);
        }
    }

    function createRecordPicker(options) {
        const opts = options || {};
        const container = opts.containerEl;
        if (!container) {
            throw new Error("RecordPicker requires containerEl");
        }
        if (!window.dbmApi || typeof window.dbmApi.listRecords !== "function") {
            throw new Error("dbmApi.listRecords is required");
        }
        ensureStyles();

        const i18n = mergeI18n(opts.i18n);
        const persistKey = String(opts.persistKey || "").trim();
        const scopedViewKey = persistKey ? ("recordPicker.viewMode." + persistKey) : "";
        const scopedPageSizeKey = persistKey ? ("recordPicker.pageSize." + persistKey) : "";
        const globalViewMode = localStorage.getItem("recordPicker.viewMode");
        const scopedViewMode = scopedViewKey ? localStorage.getItem(scopedViewKey) : null;
        const globalPageSize = localStorage.getItem("recordPicker.pageSize");
        const scopedPageSize = scopedPageSizeKey ? localStorage.getItem(scopedPageSizeKey) : null;

        const pageSizeOptions = Array.isArray(opts.pageSizeOptions) && opts.pageSizeOptions.length
            ? opts.pageSizeOptions.slice()
            : [5, 10, 15, 25, 50, 100, 200];
        const fallbackPageSize = toNumber(opts.pageSize, 50);
        const persistedPageSize = toNumber(scopedPageSize, toNumber(globalPageSize, fallbackPageSize));
        const initialPageSize = pageSizeOptions.indexOf(persistedPageSize) !== -1 ? persistedPageSize : fallbackPageSize;
        const initialViewMode = normaliseViewMode(
            opts.viewMode || scopedViewMode || globalViewMode || "list"
        );

        const state = {
            objectId: String(opts.objectId || ""),
            mode: String(opts.mode || "single"),
            pageSizeOptions: pageSizeOptions,
            pageSize: initialPageSize,
            offset: 0,
            order: String(opts.order || "identificator"),
            query: String(opts.initialQuery || "").trim(),
            viewMode: initialViewMode,
            records: [],
            loading: false,
            error: null,
            hasMore: false,
            total: null,
            activeIndex: -1,
            requestId: 0,
            abortController: null,
            includeTotalFromPaging: false,
            recentKey: String(opts.recentKey || "").trim(),
            recentLimit: Math.max(toNumber(opts.recentLimit, 20), 0),
            recentItems: [],
        };

        const onSelect = typeof opts.onSelect === "function" ? opts.onSelect : defaultFn;
        const onEscape = typeof opts.onEscape === "function" ? opts.onEscape : defaultFn;

        const root = createElement("div", "record-picker");
        const controls = createElement("div", "record-picker-controls d-flex gap-2 align-items-center flex-wrap");
        const searchWrap = createElement("div", "input-group");
        searchWrap.style.maxWidth = "420px";
        const searchInput = createElement("input", "form-control");
        searchInput.type = "search";
        searchInput.placeholder = i18n.placeholderSearch;
        searchInput.value = state.query;
        searchInput.autocomplete = "off";
        const clearBtn = createElement("button", "btn btn-outline-secondary");
        clearBtn.type = "button";
        clearBtn.textContent = i18n.clear;
        searchWrap.appendChild(searchInput);
        searchWrap.appendChild(clearBtn);

        const pageSizeWrap = createElement("div", "d-flex align-items-center gap-2");
        const pageSizeLabel = createElement("span", "small text-muted");
        pageSizeLabel.textContent = i18n.limitLabel;
        const pageSizeSelect = createElement("select", "form-select");
        pageSizeSelect.style.width = "auto";
        state.pageSizeOptions.forEach(function (value) {
            const option = createElement("option");
            option.value = String(value);
            option.textContent = String(value);
            option.selected = Number(value) === state.pageSize;
            pageSizeSelect.appendChild(option);
        });
        pageSizeWrap.appendChild(pageSizeLabel);
        pageSizeWrap.appendChild(pageSizeSelect);

        const viewToggle = createElement("div", "btn-group");
        viewToggle.setAttribute("role", "group");
        const listBtn = createElement("button", "btn btn-outline-secondary");
        listBtn.type = "button";
        listBtn.textContent = i18n.viewList;
        const chipsBtn = createElement("button", "btn btn-outline-secondary");
        chipsBtn.type = "button";
        chipsBtn.textContent = i18n.viewChips;
        viewToggle.appendChild(listBtn);
        viewToggle.appendChild(chipsBtn);

        controls.appendChild(searchWrap);
        controls.appendChild(pageSizeWrap);
        controls.appendChild(viewToggle);

        const hintEl = createElement("div", "small text-muted mt-2");
        const recentWrap = createElement("div", "record-picker-recent mt-2");
        const statusEl = createElement("div", "small text-muted mt-2 rp-status");
        const resultWrap = createElement("div", "record-picker-results mt-2");
        resultWrap.tabIndex = 0;
        resultWrap.style.maxHeight = "420px";
        resultWrap.style.overflow = "auto";
        const pagination = createElement("div", "record-picker-pagination d-flex gap-2 mt-2");
        const prevBtn = createElement("button", "btn btn-outline-secondary btn-sm");
        prevBtn.type = "button";
        prevBtn.textContent = i18n.prev;
        const nextBtn = createElement("button", "btn btn-outline-secondary btn-sm");
        nextBtn.type = "button";
        nextBtn.textContent = i18n.next;
        pagination.appendChild(prevBtn);
        pagination.appendChild(nextBtn);

        root.appendChild(controls);
        root.appendChild(hintEl);
        root.appendChild(recentWrap);
        root.appendChild(statusEl);
        root.appendChild(resultWrap);
        root.appendChild(pagination);
        container.innerHTML = "";
        container.appendChild(root);

        function saveViewMode() {
            try {
                localStorage.setItem("recordPicker.viewMode", state.viewMode);
                if (scopedViewKey) {
                    localStorage.setItem(scopedViewKey, state.viewMode);
                }
            } catch (_error) {}
        }

        function savePageSize() {
            try {
                localStorage.setItem("recordPicker.pageSize", String(state.pageSize));
                if (scopedPageSizeKey) {
                    localStorage.setItem(scopedPageSizeKey, String(state.pageSize));
                }
            } catch (_error) {}
        }

        function loadRecent() {
            if (!state.recentKey) {
                return [];
            }
            const payload = safeJsonParse(localStorage.getItem(state.recentKey), []);
            if (!Array.isArray(payload)) {
                return [];
            }
            return payload
                .map(function (item) {
                    if (!item || typeof item !== "object") {
                        return null;
                    }
                    const uid = String(item.record_uid || "").trim();
                    if (!uid) {
                        return null;
                    }
                    return {
                        record_uid: uid,
                        identificator: String(item.identificator || uid),
                    };
                })
                .filter(Boolean)
                .slice(0, state.recentLimit);
        }

        function saveRecent(record) {
            if (!state.recentKey || !record) {
                return;
            }
            const uid = String(record.record_uid || "").trim();
            if (!uid) {
                return;
            }
            const label = String(record.identificator || uid);
            const items = loadRecent().filter(function (item) {
                return String(item.record_uid) !== uid;
            });
            items.unshift({
                record_uid: uid,
                identificator: label,
            });
            state.recentItems = items.slice(0, state.recentLimit);
            try {
                localStorage.setItem(state.recentKey, JSON.stringify(state.recentItems));
            } catch (_error) {}
        }

        function setLoading(value) {
            state.loading = !!value;
            root.setAttribute("aria-busy", state.loading ? "true" : "false");
            pageSizeSelect.disabled = state.loading;
            listBtn.disabled = state.loading;
            chipsBtn.disabled = state.loading;
            prevBtn.disabled = state.loading || state.offset <= 0;
            nextBtn.disabled = state.loading || !state.hasMore;
        }

        function renderHint() {
            if (state.query.length === 1) {
                hintEl.textContent = i18n.minCharsHint;
            } else {
                hintEl.textContent = "";
            }
        }

        function renderStatus() {
            const shownFrom = state.records.length ? (state.offset + 1) : 0;
            const shownTo = state.offset + state.records.length;
            const queryText = state.query ? (" " + i18n.querySuffix + " '" + state.query + "'") : "";
            if (state.total != null) {
                statusEl.textContent = i18n.found + ": " + state.total + queryText;
            } else {
                statusEl.textContent = i18n.shown + " " + shownFrom + "–" + shownTo + queryText;
            }
        }

        function updateViewButtons() {
            listBtn.classList.toggle("active", state.viewMode === "list");
            chipsBtn.classList.toggle("active", state.viewMode === "chips");
        }

        function renderRecent() {
            recentWrap.innerHTML = "";
            if (!state.recentKey || state.query) {
                return;
            }
            state.recentItems = loadRecent();
            if (!state.recentItems.length) {
                return;
            }
            const title = createElement("div", "small text-muted mb-1");
            title.textContent = i18n.recentTitle;
            recentWrap.appendChild(title);
            const list = createElement("div", "rp-recent");
            state.recentItems.forEach(function (item) {
                const btn = createElement("button", "btn btn-sm btn-outline-secondary rp-chip");
                btn.type = "button";
                btn.title = item.identificator || item.record_uid;
                appendHighlightedText(btn, item.identificator || item.record_uid, state.query);
                btn.addEventListener("click", function () {
                    saveRecent(item);
                    onSelect(item.record_uid, item);
                });
                list.appendChild(btn);
            });
            recentWrap.appendChild(list);
        }

        function selectRecord(index) {
            if (index < 0 || index >= state.records.length) {
                return;
            }
            state.activeIndex = index;
            renderResults();
            const record = state.records[index];
            saveRecent(record);
            renderRecent();
            onSelect(record.record_uid, record);
        }

        function moveActive(delta) {
            if (!state.records.length) {
                return;
            }
            const current = state.activeIndex < 0 ? 0 : state.activeIndex;
            const next = Math.min(Math.max(current + delta, 0), state.records.length - 1);
            state.activeIndex = next;
            renderResults();
            const activeEl = resultWrap.querySelector('[data-record-index="' + next + '"]');
            if (activeEl) {
                activeEl.scrollIntoView({ block: "nearest" });
            }
        }

        function renderResults() {
            resultWrap.innerHTML = "";
            if (state.loading) {
                const loadingEl = createElement("div", "text-muted py-2");
                loadingEl.textContent = i18n.loading;
                resultWrap.appendChild(loadingEl);
                return;
            }
            if (state.error) {
                const errorEl = createElement("div", "alert alert-danger");
                errorEl.textContent = state.error;
                const retryBtn = createElement("button", "btn btn-sm btn-outline-danger ms-2");
                retryBtn.type = "button";
                retryBtn.textContent = i18n.retry;
                retryBtn.addEventListener("click", function () {
                    fetchRecords({ force: true });
                });
                errorEl.appendChild(retryBtn);
                resultWrap.appendChild(errorEl);
                return;
            }
            if (!state.records.length) {
                const emptyEl = createElement("div", "text-muted py-2");
                emptyEl.textContent = i18n.empty;
                resultWrap.appendChild(emptyEl);
                return;
            }

            if (state.viewMode === "chips") {
                const chips = createElement("div", "rp-chip-list");
                state.records.forEach(function (record, index) {
                    const btn = createElement("button", "btn btn-outline-primary btn-sm rp-chip");
                    btn.type = "button";
                    btn.dataset.recordIndex = String(index);
                    if (index === state.activeIndex) {
                        btn.classList.add("rp-active");
                    }
                    const label = record.identificator || record.record_uid;
                    btn.title = label;
                    appendHighlightedText(btn, label, state.query);
                    btn.addEventListener("click", function () {
                        selectRecord(index);
                    });
                    chips.appendChild(btn);
                });
                resultWrap.appendChild(chips);
            } else {
                const list = createElement("div", "list-group");
                state.records.forEach(function (record, index) {
                    const item = createElement("button", "list-group-item list-group-item-action");
                    item.type = "button";
                    item.dataset.recordIndex = String(index);
                    if (index === state.activeIndex) {
                        item.classList.add("rp-active");
                    }
                    const label = record.identificator || record.record_uid;
                    item.title = label;
                    appendHighlightedText(item, label, state.query);
                    item.addEventListener("click", function () {
                        selectRecord(index);
                    });
                    list.appendChild(item);
                });
                resultWrap.appendChild(list);
            }
        }

        function shouldIncludeTotal() {
            if (state.query.length > 0) {
                return true;
            }
            if (state.viewMode === "list" && (state.includeTotalFromPaging || state.offset > 0)) {
                return true;
            }
            return false;
        }

        function buildCacheKey(includeTotal) {
            return JSON.stringify({
                objectId: state.objectId,
                q: state.query,
                limit: state.pageSize,
                offset: state.offset,
                order: state.order,
                includeTotal: includeTotal ? 1 : 0,
            });
        }

        function applyPayload(payload) {
            const list = Array.isArray(payload && payload.records) ? payload.records : [];
            state.records = list.map(function (record) {
                const uid = String((record && record.record_uid) || "");
                const identificator = String((record && record.identificator) || uid);
                return {
                    record_uid: uid,
                    identificator: identificator,
                    fields: record && record.fields ? record.fields : {},
                };
            });
            state.hasMore = !!(payload && payload.has_more);
            if (payload && payload.total != null) {
                state.total = Number(payload.total);
            } else {
                state.total = null;
            }
            state.activeIndex = state.records.length ? 0 : -1;
            renderHint();
            renderRecent();
            renderStatus();
            renderResults();
        }

        function fetchRecords(options) {
            const fetchOptions = options || {};
            const force = !!fetchOptions.force;
            if (!state.objectId) {
                state.records = [];
                state.error = null;
                state.hasMore = false;
                state.total = null;
                state.activeIndex = -1;
                setLoading(false);
                renderHint();
                renderRecent();
                renderStatus();
                renderResults();
                return Promise.resolve();
            }
            renderHint();
            if (state.query.length === 1) {
                state.records = [];
                state.error = null;
                state.hasMore = false;
                state.total = null;
                state.activeIndex = -1;
                setLoading(false);
                renderRecent();
                renderStatus();
                renderResults();
                return Promise.resolve();
            }

            if (state.abortController) {
                state.abortController.abort();
            }
            const includeTotal = shouldIncludeTotal();
            const cacheKey = buildCacheKey(includeTotal);
            const cached = force ? null : cacheGet(cacheKey);
            if (cached) {
                applyPayload(cached);
                setLoading(false);
                return Promise.resolve(cached);
            }

            state.abortController = new AbortController();
            const currentRequestId = ++state.requestId;
            state.error = null;
            setLoading(true);
            renderResults();
            return window.dbmApi.listRecords(state.objectId, {
                limit: state.pageSize,
                offset: state.offset,
                order: state.order,
                q: state.query,
                includeTotal: includeTotal,
                include_schema: 0,
                signal: state.abortController.signal,
            }).then(function (payload) {
                if (currentRequestId !== state.requestId) {
                    return;
                }
                cacheSet(cacheKey, payload);
                applyPayload(payload || {});
            }).catch(function (error) {
                if (currentRequestId !== state.requestId) {
                    return;
                }
                if (error && error.name === "AbortError") {
                    return;
                }
                state.error = (error && error.message) ? error.message : i18n.error;
                state.records = [];
                state.hasMore = false;
                state.total = null;
                state.activeIndex = -1;
                renderRecent();
                renderStatus();
                renderResults();
            }).finally(function () {
                if (currentRequestId === state.requestId) {
                    setLoading(false);
                    renderStatus();
                    renderResults();
                }
            });
        }

        const debouncedSearch = (function () {
            let timer = null;
            return function () {
                if (timer) {
                    clearTimeout(timer);
                }
                timer = setTimeout(function () {
                    state.query = String(searchInput.value || "").trim();
                    state.offset = 0;
                    state.includeTotalFromPaging = false;
                    fetchRecords();
                }, 300);
            };
        })();

        function onKeyDown(evt) {
            if (evt.key === "ArrowDown") {
                evt.preventDefault();
                moveActive(1);
                return;
            }
            if (evt.key === "ArrowUp") {
                evt.preventDefault();
                moveActive(-1);
                return;
            }
            if (evt.key === "Enter") {
                if (state.activeIndex >= 0) {
                    evt.preventDefault();
                    selectRecord(state.activeIndex);
                }
                return;
            }
            if (evt.key === "Escape") {
                onEscape();
                root.dispatchEvent(new CustomEvent("recordpicker:escape", { bubbles: true }));
            }
        }

        searchInput.addEventListener("input", debouncedSearch);
        searchInput.addEventListener("keydown", onKeyDown);
        resultWrap.addEventListener("keydown", onKeyDown);

        clearBtn.addEventListener("click", function () {
            searchInput.value = "";
            state.query = "";
            state.offset = 0;
            state.includeTotalFromPaging = false;
            fetchRecords();
            searchInput.focus();
        });

        pageSizeSelect.addEventListener("change", function () {
            state.pageSize = toNumber(pageSizeSelect.value, state.pageSize);
            state.offset = 0;
            state.includeTotalFromPaging = false;
            savePageSize();
            fetchRecords();
        });

        listBtn.addEventListener("click", function () {
            state.viewMode = "list";
            updateViewButtons();
            saveViewMode();
            renderRecent();
            renderResults();
        });

        chipsBtn.addEventListener("click", function () {
            state.viewMode = "chips";
            updateViewButtons();
            saveViewMode();
            renderRecent();
            renderResults();
        });

        prevBtn.addEventListener("click", function () {
            if (state.offset <= 0) {
                return;
            }
            state.offset = Math.max(0, state.offset - state.pageSize);
            if (state.viewMode === "list") {
                state.includeTotalFromPaging = true;
            }
            fetchRecords();
        });

        nextBtn.addEventListener("click", function () {
            if (!state.hasMore) {
                return;
            }
            state.offset += state.pageSize;
            if (state.viewMode === "list") {
                state.includeTotalFromPaging = true;
            }
            fetchRecords();
        });

        updateViewButtons();
        renderHint();
        renderRecent();
        renderStatus();
        renderResults();
        fetchRecords();

        return {
            root: root,
            refresh: function () {
                return fetchRecords({ force: true });
            },
            setObjectId: function (objectId) {
                state.objectId = String(objectId || "");
                state.offset = 0;
                state.includeTotalFromPaging = false;
                return fetchRecords({ force: true });
            },
            destroy: function () {
                if (state.abortController) {
                    state.abortController.abort();
                }
                if (container.contains(root)) {
                    container.removeChild(root);
                }
            },
        };
    }

    window.dbmRecordPicker = {
        createRecordPicker: createRecordPicker,
    };
})();
