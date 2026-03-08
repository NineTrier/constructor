# Document/DBM Smoke Checklist

1. Open `/document/view?id=<doc_id>` and verify object panels are rendered and existing object values remain visible.
2. Click identifier input for an object, search in modal, choose one entry, and verify parameter fields are populated.
3. For linked parameters (`linked_object`), change parent identifier and verify child fields are auto-updated.
4. Open "Подключить объект", select one or more objects, submit, save document, reload, and verify links persist.
5. Delete linked object from document, save, reload, and verify detached object is no longer connected.
6. Click edit object-row action (pencil icon), ensure redirect to `/database/update_element_to_object/<id>/?id=<row>` still works.
7. Set `DBM_UI_V1_ONLY=1` and verify record selection in document UI works without legacy fallback warnings.
8. With `DBM_UI_V1_ONLY=1`, verify DBM requests for list/get record go through `/database/api/v1/...` only.
9. In default mode (`DBM_UI_LEGACY_FALLBACK=0`) verify DBM flows still use v1 endpoints and do not call `/database/get_data_from_object` or `/database/get_object` for record data.
10. In record selection modal, verify `RecordPicker` search is debounced and query with 1 symbol shows hint (no request), with 2+ symbols loads filtered records by identificator.
11. Change `RecordPicker` page size and navigate `Prev/Next`; ensure selected record still opens and parameters are populated correctly.
12. Switch `RecordPicker` view mode `List/Chips`, close/reopen modal, and verify mode persists (global + object-scoped preference).
13. Select several records and verify block "Последние выбранные" updates (max 20 items) and quick-clicking a recent record applies values.
14. In modal keyboard smoke: `ArrowUp/ArrowDown` moves active item, `Enter` selects, `Esc` closes modal.
15. While typing quickly in modal search, verify focus/caret are preserved and input does not lose focus after result refresh.
16. Add legacy token `{: <Объект>.<Параметр> :}` into document text and verify target span gets `data-token=\"{:obj(<object_id>).param(<parameter_id>):}\"`.
17. Add legacy linked token `{: <Объект>.<Имя связи>.<Параметр> :}` and verify target span gets `data-token=\"{:obj(<parent_object_id>).link(<link_meta_id>).param(<child_parameter_id>):}\"`.
18. Rename object/parameter/link display in DBM and verify existing spans with `data-token` still resolve values correctly.
19. Create two named links to one child object (for example `Заявитель`/`Ответчик`) and verify both token paths resolve independently.
20. Create depth-2 chain (`A -> roleB -> B -> roleC -> C`) and verify canonical token `{:obj(<A>).link(<roleB>).link(<roleC>).param(<paramC>):}` resolves in document.
21. For token with selector `[*]` (for example `{:obj(<A>).link(<roleB>)[*].param(<paramB>):}`) verify joined values are shown as comma-separated string.
22. Create record-level cycle in links and verify token render does not crash page: span shows placeholder (`—`) and tooltip contains cycle error.
23. Call `POST /document/api/v1/resolve_tokens/` with canonical token and `context`, verify per-token statuses (`ok`/`error`) and summary counters.
24. Save and export DOCX for document with canonical token in `json.text`; verify exported file contains resolved value and does not contain service markers (`{:` / `obj(` / `link(` / `data-token` / `data-invis`).
25. After export, reload document from DB and verify `document.json` still contains original token text (no persisted substitution).
26. Insert human token `{: <Объект>.<Роль>.<Параметр> :}` into text, reopen document, and verify span receives canonical `data-token` while visible text remains human-readable.
27. Insert depth-2/3 human token (for example `{: A.РольB.РольC.ПараметрC :}`), select root record, and verify value is resolved via prefetch (without long placeholder delays).
28. For role with multiple children, check `{: A.Роль[*].Параметр :}` resolves joined values and `{: A.Роль[0].Параметр :}` resolves first deterministic child.
29. Call `POST /document/api/v1/resolve_tokens/` with mixed canonical + human token list; verify response contains `input_token`, `canonical_token`, and per-token `status` (`ok`/`unresolved`/`error`).
30. Export DOCX from document containing only human tokens in `json.text`; verify export succeeds, values are substituted, and no token artifacts remain in DOCX XML.
31. Enable `DOCUMENT_LINK_TREE_UI=1`, open document and verify each connected object contains block `Связи и параметры` with expandable role tree.
32. Select root record in object panel, expand role depth-2/3 in tree, click parameter and verify inserted span contains `data-token`, `data-token-version=v1`, `data-human-token`.
33. For multiple role in tree, toggle `Первый/Все`, insert token and verify resulting human token uses `[0]` or `[*]` accordingly.
34. With `DOCUMENT_LINK_TREE_UI=1`, verify pseudo linked rows in flat list are hidden (`legacy-linked-pseudo`) to reduce duplicate UI noise.
35. Call `POST /document/api/v1/prefetch_graph/` with `document_id/context/tokens`; verify response contains `graph.records`, `graph.links`, `summary`, and `maxDepth` is limited to `<= 8`.
36. Enable `DOC_TOKEN_HUMAN_STRICT=1`, use token with fuzzy role casing (for example `ВЛАДЕЛЕЦ` instead of exact `Владелец`) and verify resolver returns `status=unresolved` (no implicit fallback).
37. Enable `DBM_DISABLE_LEGACY_LINKED_PARAMS=1` and verify legacy linked parameters are hidden in document object panels even when `DOCUMENT_LINK_TREE_UI=0`.
38. Run `python manage.py document_dbm_tree_smoke --object-id <id> --doc-id <doc_id> --json` and verify smoke report is `ok=true`.
39. Open document with `DOCUMENT_LINK_TREE_UI=1`: for object without selected record tree is visible in schema-mode (object/roles/params), parameter values are `-`, and no console exception appears.
40. Select record in picker for object and verify tree switches to value-mode: expand roles and check values are loaded (or `Связанные записи не выбраны.`) without runtime errors.
41. Click parameter in tree in schema-mode and value-mode: token copy works in both; if `Вставить` action is used, inserted span must contain `data-token`, `data-token-version=v1`, `data-human-token`.
42. Enable `DOCUMENT_VARIABLES_TREE_UNIFIED_UI=1` and verify in Variables -> Objects there is one tree block per object, while parameter input form remains available inside collapsible section `Параметры и ввод значений`.
43. Select root record in picker and verify `<datalist id="variable_list">` is rebuilt with depth-2/3 human tokens only for reachable branches of selected records.
44. Click parameter in DBM tree and verify human token is copied to clipboard and short toast (`Скопировано`) is shown.
45. In quick editor input (`#span_value`) enter depth-2 human token (for example `{: Объект.Роль.Параметр :}`) and verify runtime bridge sets `data-token` and span value is resolved automatically.
46. In quick editor input enter depth-3 human token (for example `{: Объект.Роль1.Роль2.Параметр :}`) and verify value resolves in live UI without manual reload.
47. After token was resolved in run/span, change selected root record in picker and verify value is recalculated for the same token.
48. Save document with resolved depth-2/3 token, reload page, select root record again and verify token still resolves (no manual re-insert needed).
49. After reload inspect run/span and verify `data-token` + `data-token-version="v1"` are present for depth-N token.
50. Verify all variable/token runs (legacy + v1) have unified class `reference_to_data` and keep common highlight style.
51. Open document with v1 tokens and keep page idle for at least one `acceptFilters` interval (~5s); verify no `500` on `POST /document/acceptFilters` and no `KeyError: 'phrase'` in logs.
52. Enable `DOCUMENT_EVENT_DRIVEN_UI=1` and keep document open for 15-20 seconds without edits: verify there is no periodic `POST /document/acceptFilters` every 5 seconds.
53. With `DOCUMENT_EVENT_DRIVEN_UI=1`, change variable value/filter/text run and verify `acceptFilters` is called only after change (debounced), not by timer.
54. With `DOCUMENT_EVENT_DRIVEN_UI=1`, select another DBM record (picker), wait resolve completion, and verify recalculation is triggered by `dbm:tokens-resolved` / `dbm:links-changed` events without manual refresh.
55. With `DOCUMENT_EVENT_DRIVEN_UI=1`, change `child_link_<meta_id>` / linked parameter in DBM forms and verify related document tokens are recalculated through `dbm:links-changed`.
56. With `DOCUMENT_EVENT_DRIVEN_UI=1`, switch browser tab away and return after date change (or mock next midnight): verify `Дата сегодня` updates without 1-second polling.

## Test Environment Note

- PostgreSQL test runs are considered canonical via Docker (`docker compose exec web ...`).
- Windows local `venv` direct PostgreSQL runs may fail with `psycopg2 UnicodeDecodeError`; in that case use Docker test execution.

## Encoding policy

- Frontend/source files (`.html`, `.js`, `.css`) must be saved as `UTF-8 with BOM`.
- Before commit run:
  - `python tools/check_encoding_mojibake.py`
