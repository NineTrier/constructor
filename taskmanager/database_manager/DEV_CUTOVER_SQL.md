# SQL Cutover Plan (database_manager)

## Stage 0: Preconditions

1. Apply migrations:
   - `python manage.py migrate`
2. Backfill data and links:
   - `python manage.py backfill_records_to_sql --deep --links`
3. Normalize legacy links in file storage:
   - `python manage.py normalize_legacy_links_in_file`
4. Check drift:
   - `python manage.py dbm_drift_report --links --sample 100`
   - `python manage.py dbm_drift_report --full --links --explain --top 20 --json`
5. Run preflight:
   - `python manage.py dbm_cutover_check --json`
6. (Optional safety cleanup) remove SQL-only orphan rows:
   - dry run: `python manage.py dbm_cleanup_sql_orphans --object-id <id>`
   - apply: `python manage.py dbm_cleanup_sql_orphans --object-id <id> --apply`

Go to next stage only when drift report has no diffs.

## Stage 1: Read from SQL

1. Enable read path:
   - `DBM_READ_FROM_SQL=1`
2. Keep file fallback enabled (current behavior).
3. Monitor logs:
   - `sql_miss`
   - `dual_read_diff`

Go to next stage only when `sql_miss` is close to zero and `dual_read_diff` is stable/near zero.

## Stage 2: SQL as Source of Truth (with secondary file writes)

1. Enable SQL-first writes:
   - `DBM_SQL_SOURCE_OF_TRUTH=1`
2. Keep file writes as secondary best-effort:
   - `DBM_SQL_WRITE_FILE_SECONDARY=1`
3. Keep dual-write observability:
   - monitor `sql_write_primary_failed`
   - monitor `file_write_secondary_failed`
   - monitor `dual_write_failed`

Go to next stage only when no `sql_write_primary_failed` and no critical drift.

## Stage 3: Disable secondary file writes

1. Turn off file secondary writes:
   - `DBM_SQL_WRITE_FILE_SECONDARY=0`
2. Continue drift checks:
   - `python manage.py dbm_drift_report --links --sample 100 --json`

## Stage 4: Disable file fallback reads

1. Keep `DBM_READ_FROM_SQL=1`.
2. Disable file fallback explicitly:
   - `DBM_FILE_FALLBACK_READ=0`
3. Remove/deprecate legacy file-read endpoints in phased manner.
4. Keep periodic drift report until legacy storage is fully retired.

## UI Flags During Migration

- Default v1 UI path:
  - `DBM_UI_USE_API_FOR_MUTATIONS=1`
  - `DBM_UI_LEGACY_FALLBACK=0`
- Hard mode (no legacy at all):
  - `DBM_UI_V1_ONLY=1`

## Guardrails Policy (WARN vs ERROR)

- Source of truth for rules and severities: `application/flag_guardrails.py`.
- `ERROR`:
  - always blocks `dbm_cutover_check` (`exit_code=2`).
  - in app startup strict mode (`DBM_FLAG_VALIDATION_STRICT=1`) raises `ImproperlyConfigured`.
- `WARN`:
  - does not block by default (`dbm_cutover_check` returns `exit_code=1` when only warnings exist).
  - becomes blocker with `dbm_cutover_check --strict-warnings` (promoted to `STRICT_*`, `exit_code=2`).
- CI recommendation:
  - use `--strict-warnings` for stage gates (`read`, `sql-sot`, `no-file-write`, `no-file-fallback`).

## Readiness Criteria

- `dbm_drift_report` clean for all production objects.
- `dbm_drift_report --full --links` gives either:
  - `objects_with_diff=0`, or
  - only explicitly accepted diff reasons from explain mode.
- `missing_in_file=0` and `missing_in_sql=0` before final cutover.
- `sql_miss` near zero after read switch.
- `sql_miss` absent in real UI scenarios with `DBM_READ_FROM_SQL=1`.
- `dual_read_diff` near zero on shadow compare.
- no `sql_write_primary_failed` events.
- smoke checklists pass:
  - `SMOKE_CHECKLIST_DBM_UI.md`
  - `SMOKE_CHECKLIST_DOCUMENT_DBM.md`
- staging execution follows `RUNBOOK_STAGING_CUTOVER.md`
