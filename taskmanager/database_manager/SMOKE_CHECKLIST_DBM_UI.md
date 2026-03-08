# DBM UI Smoke Checklist

1. Open `/database/get_object/<object_id>/` and verify the record list loads via `/database/api/v1/objects/<id>/records/` (network tab).
2. Click a record in the list and verify navigation to `/database/update_element_to_object/<id>/?id=<record_uid>`.
3. Open `/database/add_element_to_object/<object_id>/`, create a record in API mode, and verify it appears in object list.
4. Open `/database/update_element_to_object/<object_id>/?id=<record_uid>`, change values, save in API mode, and verify changes are visible in object list.
5. From update page, delete the record in API mode and verify it disappears from object list.
6. On update page, create parent->child link and verify `GET /database/api/v1/objects/<id>/records/<uid>/links/` returns the link.
7. Remove the link and verify it is removed both in UI and via links API.
8. Set `DBM_UI_V1_ONLY=1` and verify there are no legacy DBM calls (`/database/get_data_from_object`, `/database/get_object` POST) during record/list/link operations.
9. Verify default flags are v1-first (`DBM_UI_USE_API_FOR_MUTATIONS=1`, `DBM_UI_LEGACY_FALLBACK=0`) and record CRUD works without legacy fallback warnings.
10. In record list (`/database/get_object/<object_id>/`) type search query with 1 symbol and verify hint appears and request is not sent; type 2+ symbols and verify filtered list is loaded via v1 endpoint.
11. Change page size in `RecordPicker` (for example 50 -> 10) and verify offset resets to first page and page contains exactly chosen amount.
12. Use `Prev/Next` in `RecordPicker` and verify deterministic order by identificator is stable between pages (no duplicates/skips).
13. Switch view mode `List/Chips`, reload page, and verify selected mode is restored from localStorage (global + object-scoped preference).
14. In picker use keyboard: `ArrowUp/ArrowDown` changes active row and `Enter` selects it.
15. While typing quickly in search input, verify focus and caret do not jump or reset and input keeps focus after list refresh.
16. Set `DBM_LINKS_META_UI=1`, open `/database/update_object/<object_id>/`, and verify legacy block `Связанные объекты` is absent; only section `Связи (роли)` is rendered.
17. In `Связи (роли)` create two roles for one child object (`Заявитель`/`Ответчик`) via v1 links-meta API and verify two different link parameters (`Связь: ...`) are created in schema.
18. Edit role `display_name/code/order/link_type` inline and verify changes persist after reload; for managed parameter verify parameter name synchronizes to `Связь: <display_name>`.
19. Delete role with existing row-links and verify role is removed, links are cleaned, and linked parameter stays in schema (detached from meta, not hard-deleted).
20. Attempt to create cyclic schema role (`child -> ... -> parent`) and verify UI shows backend cycle validation message.
21. Enable `DBM_DISABLE_LEGACY_LINKED_PARAMS=1`, open add/update/get object pages and verify legacy linked parameters (without `link_meta`) are hidden from UI.
22. In API save (`POST/PATCH /database/api/v1/objects/<id>/records/...`) send legacy linked parameter field and verify it is ignored (record save succeeds without legacy bridge creation).
23. Run `python manage.py cleanup_legacy_linked_params --object-id <id> --apply` and verify legacy linked parameters are marked deprecated (without physical deletion).
24. Open `/database/update_element_to_object/<object_id>/?id=<record_uid>` and verify each link parameter (`select[data-link-meta-id]`) contains selectable child records (not empty when records exist).
25. On update page choose child record via tab selector/picker (`child_link_<meta_id>`), save, reload, and verify selection persisted both in child tab selector and in linked parameter select (`col_value_<link_param_id>[]`).
26. Change value directly in linked parameter select and verify child tab selector updates to the same record before save; after save+reload both controls remain in sync.
27. On update page select child record in tab and verify readonly fields (`.child-param-field`) are hydrated immediately without page reload.
28. Change linked parameter select (`col_value_<link_param_id>[]`) and verify tab selector (`child_link_<meta_id>`) syncs immediately and readonly fields refresh for the new child record.
29. Clear child selection in tab and verify readonly fields are cleared (`''`) and corresponding linked parameter select is also cleared.
30. Save update form, reload page, and verify all three states are restored consistently: `child_link_<meta_id>`, `col_value_<link_param_id>[]`, and readonly `.child-param-field`.
31. For a new link/meta where tab select initially has no loaded option, choose UID in linked parameter select first and verify UID is injected into tab select options, sync stays consistent, and save persists relation.
32. Open update form for a newly created object/schema and wait 2-3 seconds after load: server-rendered values in `col_value_*` inputs/selects must stay visible (not reset to empty by JS init).
33. In the same form verify readonly child fields are non-blocking: if selected child exists they are auto-hydrated; if no child is selected they remain empty until user chooses child.
34. On update form change only linked parameter select (`col_value_<link_param_id>[]`) and do not touch tabs: tab select `child_link_<meta_id>` must switch to the same UID immediately.
35. Save right after step 34 and verify debug payload (`collectLinkSelections`) contains `link_meta_id -> [uid]` for the changed role.
36. Set `DBM_OBJECT_FORMS_DEBUG=1`, change linked parameter select (`col_value_*`) and verify console `submit snapshot` includes per-role values: `child_uid`, `param_uid`, `final_payload_uid` (all equal for changed role).
37. For two roles pointing to one child object, change each linked parameter select independently and verify each corresponding tab selector (`child_link_<meta_id>`) syncs only its own role.
38. Clear linked parameter select and verify matching tab selector is cleared too; save and reload should keep both controls empty and readonly child fields blank.

## Environment note

- Canonical backend test environment: Docker + PostgreSQL (`docker compose exec web ...`).
- On Windows local venv runs, `psycopg2 UnicodeDecodeError` may appear; in this case use Docker test execution.

## Encoding policy

- Frontend/source files (`.html`, `.js`, `.css`) must be saved as `UTF-8 with BOM`.
- Before commit run:
  - `python tools/check_encoding_mojibake.py`
