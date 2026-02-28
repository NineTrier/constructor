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

## Test Environment Note

- PostgreSQL test runs are considered canonical via Docker (`docker compose exec web ...`).
- Windows local `venv` direct PostgreSQL runs may fail with `psycopg2 UnicodeDecodeError`; in that case use Docker test execution.

## Encoding policy

- Frontend/source files (`.html`, `.js`, `.css`) must be saved as `UTF-8 with BOM`.
- Before commit run:
  - `python tools/check_encoding_mojibake.py`
