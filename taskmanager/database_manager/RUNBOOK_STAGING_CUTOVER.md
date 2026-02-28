# RUNBOOK: Staging SQL Cutover (database_manager)

## Preconditions

1. Deploy code with latest migrations.
2. Ensure backups exist for DB and media.
3. Run:
   - `python manage.py migrate`
   - `python manage.py normalize_legacy_links_in_file`
   - `python manage.py backfill_records_to_sql --links --deep`

Rollback reference: [ROLLBACK.md](d:/work/constructor/constructor/taskmanager/database_manager/ROLLBACK.md)

## Stage 0: Baseline (v1 UI default)

`.env`:
- `DBM_READ_FROM_SQL=0`
- `DBM_SQL_SOURCE_OF_TRUTH=0`
- `DBM_SQL_WRITE_FILE_SECONDARY=0`
- `DBM_FILE_FALLBACK_READ=1`
- `DBM_UI_USE_API_FOR_MUTATIONS=1`
- `DBM_UI_LEGACY_FALLBACK=0`
- `DBM_UI_V1_ONLY=0`

Checks:
- `python manage.py dbm_cutover_check --stage read --strict-warnings --json` should fail (expected: read not enabled yet).
- `python manage.py dbm_cutover_check --json` should be clean or warn-only by policy.
- `python manage.py dbm_ui_smoke_http --object-id <id> --skip-document --json`

Go/No-Go:
- GO if baseline smoke passes and drift is clean.

## Stage 1: SQL Read + File fallback ON

`.env`:
- `DBM_READ_FROM_SQL=1`
- `DBM_SQL_SOURCE_OF_TRUTH=0`
- `DBM_SQL_WRITE_FILE_SECONDARY=0`
- `DBM_FILE_FALLBACK_READ=1`

Commands:
- `python manage.py dbm_cutover_check --stage read --strict-warnings --json`
- `python manage.py dbm_drift_report --links --sample 200 --json`
- `python manage.py dbm_ui_smoke_http --object-id <id> --doc-id <doc_id> --json`

Go/No-Go:
- GO if `exit_code=0`, no drift, no critical runtime errors.
- NO-GO on sustained `sql_miss` or `dual_read_diff` spikes.

## Stage 2: SQL Source of Truth + secondary file write ON

`.env`:
- `DBM_READ_FROM_SQL=1`
- `DBM_SQL_SOURCE_OF_TRUTH=1`
- `DBM_SQL_WRITE_FILE_SECONDARY=1`
- `DBM_FILE_FALLBACK_READ=1`
- `DBM_DUAL_WRITE=0`

Commands:
- `python manage.py dbm_cutover_check --stage sql-sot --strict-warnings --json`
- `python manage.py dbm_drift_report --full --links --explain --top 20 --json`
- `python manage.py dbm_ui_smoke_http --object-id <id> --doc-id <doc_id> --json`

Go/No-Go:
- GO if no blocker errors and smoke passes.
- NO-GO if `sql_write_primary_failed` appears.

## Stage 3: Disable secondary file writes

`.env`:
- `DBM_READ_FROM_SQL=1`
- `DBM_SQL_SOURCE_OF_TRUTH=1`
- `DBM_SQL_WRITE_FILE_SECONDARY=0`
- `DBM_FILE_FALLBACK_READ=1`

Commands:
- `python manage.py dbm_cutover_check --stage no-file-write --strict-warnings --json`
- `python manage.py dbm_drift_report --links --sample 200 --json`
- `python manage.py dbm_ui_smoke_http --object-id <id> --doc-id <doc_id> --json`

Go/No-Go:
- GO if cutover check is green and user smoke stable.

## Stage 4: Disable file fallback reads

`.env`:
- `DBM_READ_FROM_SQL=1`
- `DBM_SQL_SOURCE_OF_TRUTH=1`
- `DBM_SQL_WRITE_FILE_SECONDARY=0`
- `DBM_FILE_FALLBACK_READ=0`

Commands:
- `python manage.py dbm_cutover_check --stage no-file-fallback --strict-warnings --json`
- `python manage.py dbm_drift_report --full --links --explain --top 20 --json`
- `python manage.py dbm_ui_smoke_http --object-id <id> --doc-id <doc_id> --json`

Go/No-Go:
- GO if all checks pass and no `sql_miss`.
- If failed, rollback by stage using [ROLLBACK.md](d:/work/constructor/constructor/taskmanager/database_manager/ROLLBACK.md).
