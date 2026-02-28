import io
import json
from typing import Any, Dict, List

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand
from django.db import connections
from django.db.migrations.executor import MigrationExecutor

from ...application.flag_guardrails import STAGE_CHOICES, CutoverStage, validate_dbm_flags


class Command(BaseCommand):
    help = "Preflight gate for SQL cutover: flags, migrations, drift report, stage readiness."

    def add_arguments(self, parser):
        parser.add_argument("--object-id", type=int, default=None, dest="object_id")
        parser.add_argument("--sample", type=int, default=100, dest="sample")
        parser.add_argument("--fast", action="store_true", default=False, dest="fast")
        parser.add_argument("--json", action="store_true", default=False, dest="json_output")
        parser.add_argument("--strict-warnings", action="store_true", default=False, dest="strict_warnings")
        parser.add_argument("--stage", type=str, default="", dest="stage")

    def handle(self, *args, **options):
        object_id = options["object_id"]
        sample = max(int(options["sample"] or 100), 1)
        fast_mode = bool(options["fast"])
        json_output = bool(options["json_output"])
        strict_warnings = bool(options["strict_warnings"])
        stage = str(options.get("stage") or "").strip().lower()

        checks: List[Dict[str, Any]] = []
        errors: List[Dict[str, Any]] = []
        warnings: List[Dict[str, Any]] = []

        flag_result = validate_dbm_flags(settings, stage=stage or None)
        checks.append(
            {
                "check": "flags",
                "ok": len(flag_result.error_issues) == 0,
                "errors_count": len(flag_result.error_issues),
                "warnings_count": len(flag_result.warning_issues),
            }
        )
        for issue in flag_result.error_issues:
            errors.append(self._issue_to_payload(source="flags", issue=issue))
        for issue in flag_result.warning_issues:
            warnings.append(self._issue_to_payload(source="flags", issue=issue))

        pending_migrations = self._pending_migrations_count()
        migrations_ok = pending_migrations == 0
        checks.append(
            {
                "check": "migrations",
                "ok": migrations_ok,
                "pending_migrations": pending_migrations,
            }
        )
        if not migrations_ok:
            errors.append(
                {
                    "code": "MIGRATIONS_PENDING",
                    "message": f"There are {pending_migrations} pending migrations.",
                    "action": "Run `python manage.py migrate` before cutover.",
                    "source": "migrations",
                }
            )

        drift_payload = self._run_drift_report(
            object_id=object_id,
            sample=sample,
            fast_mode=fast_mode,
        )
        drift_summary = drift_payload.get("summary", {})
        objects_with_diff = int(drift_summary.get("objects_with_diff", 0))
        file_warnings_count = int(drift_summary.get("file_warnings_count", 0))
        objects_with_file_warnings = int(drift_summary.get("objects_with_file_warnings", 0))

        drift_ok = objects_with_diff == 0 and file_warnings_count == 0 and objects_with_file_warnings == 0
        checks.append(
            {
                "check": "drift_report",
                "ok": drift_ok,
                "objects_with_diff": objects_with_diff,
                "diff_counts": drift_summary.get("diff_counts", {}),
                "file_warnings_count": file_warnings_count,
                "objects_with_file_warnings": objects_with_file_warnings,
            }
        )
        if objects_with_diff > 0:
            errors.append(
                {
                    "code": "DRIFT_NOT_CLEAN",
                    "message": f"Drift report has {objects_with_diff} objects with diffs.",
                    "action": "Fix drift via normalize/backfill/cleanup before cutover.",
                    "source": "drift_report",
                }
            )
        if file_warnings_count > 0 or objects_with_file_warnings > 0:
            warnings.append(
                {
                    "code": "DRIFT_FILE_WARNINGS",
                    "message": "Drift report returned file warnings.",
                    "action": "Inspect warnings and resolve file read/parse issues.",
                    "source": "drift_report",
                    "details": {
                        "file_warnings_count": file_warnings_count,
                        "objects_with_file_warnings": objects_with_file_warnings,
                    },
                }
            )

        if strict_warnings and warnings:
            for warning in warnings:
                errors.append(
                    {
                        "code": f"STRICT_{warning['code']}",
                        "message": warning["message"],
                        "action": warning.get("action", "Resolve warning."),
                        "source": "strict_warnings",
                        "details": warning.get("details", {}),
                    }
                )

        exit_code = self._resolve_exit_code(errors=errors, warnings=warnings, strict_warnings=strict_warnings)
        payload = {
            "ok": exit_code == 0,
            "exit_code": exit_code,
            "errors": errors,
            "warnings": warnings,
            "checks": checks,
            "drift_summary": drift_summary,
            "recommended_next_stage": self._recommended_next_stage(stage=stage, exit_code=exit_code),
            "options": {
                "object_id": object_id,
                "sample": sample,
                "fast": fast_mode,
                "stage": stage or None,
                "strict_warnings": strict_warnings,
            },
        }

        if json_output:
            self.stdout.write(json.dumps(payload, ensure_ascii=False, default=str))
        else:
            self.stdout.write(f"cutover_check exit_code={exit_code} ok={payload['ok']}")
            for check in checks:
                self.stdout.write(f"- {check['check']}: ok={check['ok']}")
            if errors:
                self.stdout.write(f"errors={json.dumps(errors, ensure_ascii=False)}")
            if warnings:
                self.stdout.write(f"warnings={json.dumps(warnings, ensure_ascii=False)}")
            self.stdout.write(f"recommended_next_stage={payload['recommended_next_stage']}")

        if exit_code != 0:
            raise SystemExit(exit_code)

    @staticmethod
    def _pending_migrations_count() -> int:
        connection = connections["default"]
        executor = MigrationExecutor(connection)
        targets = executor.loader.graph.leaf_nodes()
        plan = executor.migration_plan(targets)
        return len(plan)

    @staticmethod
    def _run_drift_report(*, object_id: int, sample: int, fast_mode: bool) -> Dict[str, Any]:
        stdout = io.StringIO()
        command_args = ["--json", "--links", "--sample", str(sample), "--explain", "--top", "10"]
        if not fast_mode:
            command_args.append("--full")
        if object_id is not None:
            command_args.extend(["--object-id", str(object_id)])
        call_command("dbm_drift_report", *command_args, stdout=stdout)
        lines = [line.strip() for line in stdout.getvalue().splitlines() if line.strip()]
        if not lines:
            return {"summary": {}}
        return json.loads(lines[-1])

    @staticmethod
    def _resolve_exit_code(*, errors: List[Dict[str, Any]], warnings: List[Dict[str, Any]], strict_warnings: bool) -> int:
        if errors:
            return 2
        if warnings and not strict_warnings:
            return 1
        return 0

    @staticmethod
    def _issue_to_payload(*, source: str, issue) -> Dict[str, Any]:
        return {
            "code": issue.code,
            "severity": issue.severity.value,
            "message": issue.message,
            "action": issue.action,
            "source": source,
        }

    @staticmethod
    def _recommended_next_stage(*, stage: str, exit_code: int) -> str:
        if exit_code != 0:
            return "fix-issues"
        stage_norm = (stage or "").strip().lower()
        if not stage_norm:
            read_from_sql = bool(getattr(settings, "DBM_READ_FROM_SQL", False))
            sql_sot = bool(getattr(settings, "DBM_SQL_SOURCE_OF_TRUTH", False))
            secondary = bool(getattr(settings, "DBM_SQL_WRITE_FILE_SECONDARY", True))
            file_fallback = bool(getattr(settings, "DBM_FILE_FALLBACK_READ", True))
            if not read_from_sql:
                return CutoverStage.READ.value
            if not sql_sot:
                return CutoverStage.SQL_SOT.value
            if secondary:
                return CutoverStage.NO_FILE_WRITE.value
            if file_fallback:
                return CutoverStage.NO_FILE_FALLBACK.value
            return "done"

        if stage_norm == CutoverStage.READ.value:
            return CutoverStage.SQL_SOT.value
        if stage_norm == CutoverStage.SQL_SOT.value:
            return CutoverStage.NO_FILE_WRITE.value
        if stage_norm == CutoverStage.NO_FILE_WRITE.value:
            return CutoverStage.NO_FILE_FALLBACK.value
        if stage_norm == CutoverStage.NO_FILE_FALLBACK.value:
            return "done"
        if stage_norm not in STAGE_CHOICES:
            return "unknown-stage"
        return "done"