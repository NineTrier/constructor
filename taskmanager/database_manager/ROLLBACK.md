# DBM SQL Cutover Rollback

## Scope

This document describes reversible flag-only rollback steps for `database_manager` during staged SQL cutover.

## Emergency Levels

### Level 1: Disable SQL-first writes (keep SQL reads)

Use when writes fail (`sql_write_primary_failed`) but SQL reads are still stable.

1. Set:
   - `DBM_SQL_SOURCE_OF_TRUTH=0`
   - `DBM_DUAL_WRITE=1`
   - `DBM_READ_FROM_SQL=1`
2. Keep:
   - `DBM_SQL_WRITE_FILE_SECONDARY=1` (harmless in this mode)
3. Restart web.

Effect:
- Writes revert to file-primary with dual-write attempt to SQL.
- Reads continue from SQL with fallback.

### Level 2: Disable SQL reads (return to file-read)

Use when SQL read path is unstable (`sql_miss`, `dual_read_diff`, query errors).

1. Set:
   - `DBM_READ_FROM_SQL=0`
   - `DBM_SQL_SOURCE_OF_TRUTH=0`
   - `DBM_DUAL_WRITE=1` (recommended during rollback transition)
2. Restart web.

Effect:
- Reads come from file storage.
- Writes remain file-primary; SQL stays a sink for later reconciliation.

### Level 3: Full legacy mode (temporary)

Use only as short-term emergency mode.

1. Set:
   - `DBM_READ_FROM_SQL=0`
   - `DBM_SQL_SOURCE_OF_TRUTH=0`
   - `DBM_DUAL_WRITE=0`
   - `DBM_UI_LEGACY_FALLBACK=1`
   - `DBM_UI_V1_ONLY=0`
2. Restart web.

Effect:
- File-only behavior.
- SQL state may drift and must be reconciled before re-enabling SQL paths.

## Post-rollback reconciliation

1. Normalize file links:
   - `python manage.py normalize_legacy_links_in_file`
2. Rebuild SQL from file:
   - `python manage.py backfill_records_to_sql --links --deep`
3. Check drift:
   - `python manage.py dbm_drift_report --full --links --explain --json`
4. Run preflight:
   - `python manage.py dbm_cutover_check --json`

## Safety notes

- Do not run `dbm_cleanup_sql_orphans --apply` during active incident unless explicitly approved.
- If `DBM_FLAG_VALIDATION_STRICT=1`, invalid flag combinations will fail startup.
- Prefer staged rollback (Level 1 -> Level 2) over immediate full legacy mode.
