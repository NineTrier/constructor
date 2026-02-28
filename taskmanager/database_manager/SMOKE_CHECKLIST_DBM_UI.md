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

## Environment note

- Canonical backend test environment: Docker + PostgreSQL (`docker compose exec web ...`).
- On Windows local venv runs, `psycopg2 UnicodeDecodeError` may appear; in this case use Docker test execution.

## Encoding policy

- Frontend/source files (`.html`, `.js`, `.css`) must be saved as `UTF-8 with BOM`.
- Before commit run:
  - `python tools/check_encoding_mojibake.py`
