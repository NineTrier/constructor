import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional

from django.core.exceptions import ImproperlyConfigured


class GuardrailSeverity(str, Enum):
    ERROR = "ERROR"
    WARN = "WARN"


class CutoverStage(str, Enum):
    READ = "read"
    SQL_SOT = "sql-sot"
    NO_FILE_WRITE = "no-file-write"
    NO_FILE_FALLBACK = "no-file-fallback"


STAGE_CHOICES = [stage.value for stage in CutoverStage]


@dataclass(frozen=True)
class GuardrailIssue:
    code: str
    severity: GuardrailSeverity
    message: str
    action: str


@dataclass
class FlagValidationResult:
    issues: List[GuardrailIssue] = field(default_factory=list)

    @property
    def errors(self) -> List[str]:
        return [issue.message for issue in self.issues if issue.severity == GuardrailSeverity.ERROR]

    @property
    def warnings(self) -> List[str]:
        return [issue.message for issue in self.issues if issue.severity == GuardrailSeverity.WARN]

    @property
    def error_issues(self) -> List[GuardrailIssue]:
        return [issue for issue in self.issues if issue.severity == GuardrailSeverity.ERROR]

    @property
    def warning_issues(self) -> List[GuardrailIssue]:
        return [issue for issue in self.issues if issue.severity == GuardrailSeverity.WARN]

    @property
    def is_valid(self) -> bool:
        return not self.error_issues


def validate_dbm_flags(settings_obj, *, stage: Optional[str] = None) -> FlagValidationResult:
    result = FlagValidationResult()

    read_from_sql = bool(getattr(settings_obj, "DBM_READ_FROM_SQL", False))
    dual_write = bool(getattr(settings_obj, "DBM_DUAL_WRITE", False))
    sql_source_of_truth = bool(getattr(settings_obj, "DBM_SQL_SOURCE_OF_TRUTH", False))
    sql_write_file_secondary = bool(getattr(settings_obj, "DBM_SQL_WRITE_FILE_SECONDARY", True))
    file_fallback_read = bool(getattr(settings_obj, "DBM_FILE_FALLBACK_READ", True))
    strict_dual_write_tests = bool(getattr(settings_obj, "DBM_DUAL_WRITE_STRICT_FOR_TESTS", False))
    ui_v1_only = bool(getattr(settings_obj, "DBM_UI_V1_ONLY", False))
    ui_legacy_fallback = bool(getattr(settings_obj, "DBM_UI_LEGACY_FALLBACK", False))
    ui_api_mutations = bool(getattr(settings_obj, "DBM_UI_USE_API_FOR_MUTATIONS", True))

    _add_issue(
        result,
        condition=sql_source_of_truth and not read_from_sql,
        code="SQL_SOT_REQUIRES_SQL_READ",
        severity=GuardrailSeverity.ERROR,
        message="DBM_SQL_SOURCE_OF_TRUTH=1 requires DBM_READ_FROM_SQL=1.",
        action="Set DBM_READ_FROM_SQL=1 or disable DBM_SQL_SOURCE_OF_TRUTH.",
    )
    _add_issue(
        result,
        condition=ui_v1_only and ui_legacy_fallback,
        code="UI_V1_ONLY_CONFLICTS_LEGACY_FALLBACK",
        severity=GuardrailSeverity.ERROR,
        message="DBM_UI_V1_ONLY=1 is incompatible with DBM_UI_LEGACY_FALLBACK=1.",
        action="Set DBM_UI_LEGACY_FALLBACK=0 when DBM_UI_V1_ONLY=1.",
    )
    _add_issue(
        result,
        condition=(not read_from_sql) and (not file_fallback_read),
        code="FILE_FALLBACK_DISABLED_WITHOUT_SQL_READ",
        severity=GuardrailSeverity.ERROR,
        message="DBM_FILE_FALLBACK_READ=0 cannot be used while DBM_READ_FROM_SQL=0.",
        action="Enable DBM_READ_FROM_SQL=1 or set DBM_FILE_FALLBACK_READ=1.",
    )

    _add_issue(
        result,
        condition=sql_write_file_secondary and not sql_source_of_truth,
        code="SECONDARY_WRITE_REDUNDANT",
        severity=GuardrailSeverity.WARN,
        message="DBM_SQL_WRITE_FILE_SECONDARY=1 has no effect while DBM_SQL_SOURCE_OF_TRUTH=0.",
        action="Either set DBM_SQL_SOURCE_OF_TRUTH=1 or disable DBM_SQL_WRITE_FILE_SECONDARY.",
    )
    _add_issue(
        result,
        condition=sql_source_of_truth and dual_write,
        code="DUAL_WRITE_REDUNDANT_IN_SQL_SOT",
        severity=GuardrailSeverity.WARN,
        message="DBM_DUAL_WRITE=1 is redundant when DBM_SQL_SOURCE_OF_TRUTH=1.",
        action="Set DBM_DUAL_WRITE=0 in SQL source-of-truth mode.",
    )
    _add_issue(
        result,
        condition=strict_dual_write_tests and not (dual_write or sql_source_of_truth),
        code="STRICT_DUAL_WRITE_UNUSED",
        severity=GuardrailSeverity.WARN,
        message="DBM_DUAL_WRITE_STRICT_FOR_TESTS=1 has no effect while SQL write path is disabled.",
        action="Enable DBM_DUAL_WRITE=1/DBM_SQL_SOURCE_OF_TRUTH=1 or disable strict dual-write tests flag.",
    )
    _add_issue(
        result,
        condition=(not ui_api_mutations) and (not ui_legacy_fallback),
        code="UI_MUTATIONS_DISABLED",
        severity=GuardrailSeverity.WARN,
        message="DBM_UI_USE_API_FOR_MUTATIONS=0 with DBM_UI_LEGACY_FALLBACK=0 can block UI save operations.",
        action="Enable DBM_UI_USE_API_FOR_MUTATIONS=1 or DBM_UI_LEGACY_FALLBACK=1.",
    )

    stage_norm = (stage or "").strip().lower()
    if stage_norm:
        if stage_norm not in STAGE_CHOICES:
            _add_issue(
                result,
                condition=True,
                code="UNKNOWN_STAGE",
                severity=GuardrailSeverity.ERROR,
                message=f"Unknown cutover stage '{stage_norm}'.",
                action=f"Use one of: {', '.join(STAGE_CHOICES)}.",
            )
            return result

        if stage_norm == CutoverStage.READ.value:
            _add_issue(
                result,
                condition=not read_from_sql,
                code="STAGE_READ_REQUIRES_SQL_READ",
                severity=GuardrailSeverity.ERROR,
                message="Stage 'read' requires DBM_READ_FROM_SQL=1.",
                action="Set DBM_READ_FROM_SQL=1.",
            )
            _add_issue(
                result,
                condition=not file_fallback_read,
                code="STAGE_READ_EXPECTS_FILE_FALLBACK",
                severity=GuardrailSeverity.WARN,
                message="Stage 'read' usually keeps file fallback enabled.",
                action="Set DBM_FILE_FALLBACK_READ=1 unless you intentionally skip fallback.",
            )

        if stage_norm == CutoverStage.SQL_SOT.value:
            _add_issue(
                result,
                condition=not read_from_sql,
                code="STAGE_SQL_SOT_REQUIRES_SQL_READ",
                severity=GuardrailSeverity.ERROR,
                message="Stage 'sql-sot' requires DBM_READ_FROM_SQL=1.",
                action="Set DBM_READ_FROM_SQL=1.",
            )
            _add_issue(
                result,
                condition=not sql_source_of_truth,
                code="STAGE_SQL_SOT_REQUIRES_SQL_SOT",
                severity=GuardrailSeverity.ERROR,
                message="Stage 'sql-sot' requires DBM_SQL_SOURCE_OF_TRUTH=1.",
                action="Set DBM_SQL_SOURCE_OF_TRUTH=1.",
            )
            _add_issue(
                result,
                condition=not sql_write_file_secondary,
                code="STAGE_SQL_SOT_REQUIRES_SECONDARY_WRITE",
                severity=GuardrailSeverity.ERROR,
                message="Stage 'sql-sot' requires DBM_SQL_WRITE_FILE_SECONDARY=1.",
                action="Set DBM_SQL_WRITE_FILE_SECONDARY=1.",
            )

        if stage_norm == CutoverStage.NO_FILE_WRITE.value:
            _add_issue(
                result,
                condition=not read_from_sql,
                code="STAGE_NO_FILE_WRITE_REQUIRES_SQL_READ",
                severity=GuardrailSeverity.ERROR,
                message="Stage 'no-file-write' requires DBM_READ_FROM_SQL=1.",
                action="Set DBM_READ_FROM_SQL=1.",
            )
            _add_issue(
                result,
                condition=not sql_source_of_truth,
                code="STAGE_NO_FILE_WRITE_REQUIRES_SQL_SOT",
                severity=GuardrailSeverity.ERROR,
                message="Stage 'no-file-write' requires DBM_SQL_SOURCE_OF_TRUTH=1.",
                action="Set DBM_SQL_SOURCE_OF_TRUTH=1.",
            )
            _add_issue(
                result,
                condition=sql_write_file_secondary,
                code="STAGE_NO_FILE_WRITE_REQUIRES_SECONDARY_OFF",
                severity=GuardrailSeverity.ERROR,
                message="Stage 'no-file-write' requires DBM_SQL_WRITE_FILE_SECONDARY=0.",
                action="Set DBM_SQL_WRITE_FILE_SECONDARY=0.",
            )

        if stage_norm == CutoverStage.NO_FILE_FALLBACK.value:
            _add_issue(
                result,
                condition=not read_from_sql,
                code="STAGE_NO_FALLBACK_REQUIRES_SQL_READ",
                severity=GuardrailSeverity.ERROR,
                message="Stage 'no-file-fallback' requires DBM_READ_FROM_SQL=1.",
                action="Set DBM_READ_FROM_SQL=1.",
            )
            _add_issue(
                result,
                condition=not sql_source_of_truth,
                code="STAGE_NO_FALLBACK_REQUIRES_SQL_SOT",
                severity=GuardrailSeverity.ERROR,
                message="Stage 'no-file-fallback' requires DBM_SQL_SOURCE_OF_TRUTH=1.",
                action="Set DBM_SQL_SOURCE_OF_TRUTH=1.",
            )
            _add_issue(
                result,
                condition=sql_write_file_secondary,
                code="STAGE_NO_FALLBACK_REQUIRES_SECONDARY_OFF",
                severity=GuardrailSeverity.ERROR,
                message="Stage 'no-file-fallback' requires DBM_SQL_WRITE_FILE_SECONDARY=0.",
                action="Set DBM_SQL_WRITE_FILE_SECONDARY=0.",
            )
            _add_issue(
                result,
                condition=file_fallback_read,
                code="STAGE_NO_FALLBACK_REQUIRES_FILE_FALLBACK_OFF",
                severity=GuardrailSeverity.ERROR,
                message="Stage 'no-file-fallback' requires DBM_FILE_FALLBACK_READ=0.",
                action="Set DBM_FILE_FALLBACK_READ=0.",
            )

    return result


def enforce_dbm_flag_guardrails(settings_obj, *, strict: bool = False, logger=None) -> FlagValidationResult:
    result = validate_dbm_flags(settings_obj)
    log = logger or logging.getLogger(__name__)
    for issue in result.warning_issues:
        log.warning("dbm_flag_guardrail_warning %s", _format_issue(issue))
    for issue in result.error_issues:
        log.error("dbm_flag_guardrail_error %s", _format_issue(issue))
    if strict and result.error_issues:
        raise ImproperlyConfigured("; ".join(_format_issue(issue) for issue in result.error_issues))
    return result


def _add_issue(
    result: FlagValidationResult,
    *,
    condition: bool,
    code: str,
    severity: GuardrailSeverity,
    message: str,
    action: str,
) -> None:
    if not condition:
        return
    result.issues.append(
        GuardrailIssue(
            code=code,
            severity=severity,
            message=message,
            action=action,
        )
    )


def _format_issue(issue: GuardrailIssue) -> str:
    return f"[{issue.code}] {issue.message} Action: {issue.action}"