import json
import logging
from collections import Counter
from datetime import date, datetime
from typing import Any, Dict, List, Mapping, Optional, Set, Tuple

from django.core.management.base import BaseCommand

from ...application.services import ObjectDataService
from ...domain.normalize import canonicalize_record, is_empty_like, schema_from_parameters
from ...infrastructure.repositories import FileRecordRepository, SqlRecordRepository
from ...models import Object, ObjectLink_identificators, Object_ParentObject, Parameter, RecordLink
from ...presentation.dto import legacy_record_to_dto, serialise_record_dto

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Build SQL/file drift report for database_manager records and links."

    def add_arguments(self, parser):
        parser.add_argument("--object-id", type=int, default=None, dest="object_id")
        parser.add_argument("--sample", type=int, default=20, dest="sample")
        parser.add_argument("--full", action="store_true", default=False, dest="full")
        parser.add_argument("--links", action="store_true", default=True, dest="links")
        parser.add_argument("--no-links", action="store_false", dest="links")
        parser.add_argument("--json", action="store_true", default=False, dest="json_output")
        parser.add_argument("--explain", action="store_true", default=False, dest="explain")
        parser.add_argument("--top", type=int, default=10, dest="top")

    def handle(self, *args, **options):
        object_id = options["object_id"]
        sample = max(int(options["sample"] or 0), 0)
        full_mode = bool(options["full"])
        include_links = bool(options["links"])
        json_output = bool(options["json_output"])
        explain_mode = bool(options["explain"])
        top_n = max(int(options["top"] or 10), 1)

        data_service = ObjectDataService(logger=logger)
        file_repo = FileRecordRepository()
        sql_repo = SqlRecordRepository()

        objects_qs = Object.objects.all().order_by("id")
        if object_id is not None:
            objects_qs = objects_qs.filter(id=object_id)
        objects = list(objects_qs)

        object_reports: List[Dict[str, Any]] = []
        objects_with_diff = 0
        global_reason_counter: Counter[str] = Counter()
        file_warnings_total = 0
        objects_with_file_warnings = 0

        for obj in objects:
            parameters = list(Parameter.objects.filter(object=obj).order_by("id"))
            schema_map = schema_from_parameters(parameters)

            file_canonical, file_raw, warnings = self._build_file_maps(
                obj=obj,
                parameters=parameters,
                schema_map=schema_map,
                file_repo=file_repo,
            )
            if warnings:
                file_warnings_total += len(warnings)
                objects_with_file_warnings += 1
                logger.warning(
                    "dbm_drift_report_file_warnings %s",
                    json.dumps(
                        {
                            "object_id": obj.id,
                            "warnings": warnings,
                        },
                        ensure_ascii=False,
                    ),
                )
            sql_canonical, sql_raw = self._build_sql_maps(
                obj=obj,
                parameters=parameters,
                schema_map=schema_map,
                sql_repo=sql_repo,
            )

            file_uids = set(file_canonical.keys())
            sql_uids = set(sql_canonical.keys())
            missing_in_sql = sorted(file_uids - sql_uids)
            missing_in_file = sorted(sql_uids - file_uids)

            comparable_uids = sorted(file_uids & sql_uids)
            if full_mode:
                sampled_uids = comparable_uids
            else:
                sampled_uids = comparable_uids[:sample]

            sampled_record_diffs: List[str] = []
            reason_counter: Counter[str] = Counter()
            parameter_counter: Counter[str] = Counter()
            examples: List[Dict[str, Any]] = []

            for uid in sampled_uids:
                file_record = file_canonical.get(uid)
                sql_record = sql_canonical.get(uid)
                if file_record == sql_record:
                    continue
                sampled_record_diffs.append(uid)
                if not explain_mode:
                    continue
                diff_items = self._explain_record_diff(
                    record_uid=uid,
                    schema_map=schema_map,
                    file_raw_record=file_raw.get(uid, {}),
                    sql_raw_record=sql_raw.get(uid, {}),
                    file_canonical_record=file_record or {},
                    sql_canonical_record=sql_record or {},
                )
                for item in diff_items:
                    reason = str(item.get("reason") or "value_mismatch")
                    reason_counter[reason] += 1
                    parameter_counter[str(item.get("parameter_id") or "")] += 1
                    if len(examples) < 5:
                        examples.append(item)

            links_diff_count = 0
            links_diff_details: List[Dict[str, Any]] = []
            if include_links:
                links_diff_count, links_diff_details = self._compare_links(
                    obj=obj,
                    data_service=data_service,
                )

            has_diff = bool(
                missing_in_sql
                or missing_in_file
                or sampled_record_diffs
                or links_diff_count
            )
            if has_diff:
                objects_with_diff += 1

            top_parameters: List[Dict[str, Any]] = []
            if explain_mode:
                name_map = {str(parameter.id): parameter.name for parameter in parameters}
                for param_id, count in parameter_counter.most_common(top_n):
                    if not param_id:
                        continue
                    top_parameters.append(
                        {
                            "parameter_id": param_id,
                            "parameter_name": name_map.get(param_id, ""),
                            "count": int(count),
                        }
                    )
                global_reason_counter.update(reason_counter)

            object_report = {
                "object_id": obj.id,
                "object_name": obj.name,
                "file_records": len(file_uids),
                "sql_records": len(sql_uids),
                "missing_in_sql_count": len(missing_in_sql),
                "missing_in_file_count": len(missing_in_file),
                "sample_size": len(sampled_uids),
                "sample_record_diff_count": len(sampled_record_diffs),
                "missing_in_sql_sample": missing_in_sql[:20],
                "missing_in_file_sample": missing_in_file[:20],
                "sample_record_diff_uids": sampled_record_diffs[:20],
                "link_diff_count": links_diff_count,
                "link_diff_details": links_diff_details[:20],
                "warnings_count": len(warnings),
                "warnings_sample": warnings[:10],
            }
            if explain_mode:
                object_report["diff_reasons"] = dict(reason_counter)
                object_report["top_parameters"] = top_parameters
                object_report["examples"] = examples
            object_reports.append(object_report)

        summary = {
            "objects_total": len(object_reports),
            "objects_ok": len(object_reports) - objects_with_diff,
            "objects_with_diff": objects_with_diff,
            "diff_counts": {
                "missing_in_sql": sum(item["missing_in_sql_count"] for item in object_reports),
                "missing_in_file": sum(item["missing_in_file_count"] for item in object_reports),
                "sample_record_diff": sum(item["sample_record_diff_count"] for item in object_reports),
                "links_diff": sum(item["link_diff_count"] for item in object_reports),
            },
            "file_warnings_count": file_warnings_total,
            "objects_with_file_warnings": objects_with_file_warnings,
            "options": {
                "object_id": object_id,
                "sample": sample,
                "full": full_mode,
                "links": include_links,
                "explain": explain_mode,
                "top": top_n,
            },
        }
        if explain_mode:
            summary["diff_reasons"] = dict(global_reason_counter)

        logger.warning(
            "dbm_drift_report %s",
            json.dumps(
                {
                    "summary": summary,
                },
                ensure_ascii=False,
                default=str,
            ),
        )

        payload = {
            "summary": summary,
            "objects": object_reports,
        }
        if json_output:
            self.stdout.write(json.dumps(payload, ensure_ascii=False, default=str))
            return

        self.stdout.write(
            "drift_report summary: objects_total={objects_total}, objects_ok={objects_ok}, objects_with_diff={objects_with_diff}".format(
                **summary
            )
        )
        self.stdout.write(
            "diff_counts: missing_in_sql={missing_in_sql}, missing_in_file={missing_in_file}, sample_record_diff={sample_record_diff}, links_diff={links_diff}".format(
                **summary["diff_counts"]
            )
        )
        self.stdout.write(
            f"file_warnings: total={summary['file_warnings_count']} objects={summary['objects_with_file_warnings']}"
        )
        if explain_mode and summary.get("diff_reasons"):
            self.stdout.write(f"diff_reasons: {json.dumps(summary['diff_reasons'], ensure_ascii=False)}")
        for item in object_reports:
            self.stdout.write(
                "[object={object_id}] file={file_records} sql={sql_records} missing_sql={missing_in_sql_count} missing_file={missing_in_file_count} sampled_diff={sample_record_diff_count} links_diff={link_diff_count}".format(
                    **item
                )
            )

    @staticmethod
    def _build_file_maps(
        *,
        obj: Object,
        parameters: List[Parameter],
        schema_map: Mapping[str, Mapping[str, Any]],
        file_repo: FileRecordRepository,
    ) -> Tuple[Dict[str, Dict[str, Any]], Dict[str, Dict[str, Any]], List[str]]:
        rows, warnings = file_repo.list_raw_rows(
            obj,
            allow_empty=True,
            ensure_record_uid=True,
            persist=False,
        )
        canonical_map: Dict[str, Dict[str, Any]] = {}
        raw_map: Dict[str, Dict[str, Any]] = {}
        for row in rows:
            record_uid = str(row.get("record_uid") or row.get("id_to_connect") or "").strip()
            if not record_uid:
                continue
            legacy_record: Dict[str, Any] = {"id_to_connect": record_uid}
            for parameter in parameters:
                key = str(parameter.id)
                legacy_record[key] = {
                    "data_type": parameter.data_type,
                    "value": row.get(key, ""),
                }
            raw_v1 = serialise_record_dto(
                legacy_record_to_dto(legacy_record, schema=schema_map, canonicalize=False)
            )
            canonical_map[record_uid] = canonicalize_record(raw_v1, schema_map)
            raw_map[record_uid] = raw_v1
        return canonical_map, raw_map, warnings

    @staticmethod
    def _build_sql_maps(
        *,
        obj: Object,
        parameters: List[Parameter],
        schema_map: Mapping[str, Mapping[str, Any]],
        sql_repo: SqlRecordRepository,
    ) -> Tuple[Dict[str, Dict[str, Any]], Dict[str, Dict[str, Any]]]:
        canonical_map: Dict[str, Dict[str, Any]] = {}
        raw_map: Dict[str, Dict[str, Any]] = {}
        for record in sql_repo.list_records(obj, order_by="record_uid"):
            legacy_record = sql_repo.serialise_record_to_legacy(record, parameters)
            legacy_record["id_to_connect"] = record.record_uid
            raw_v1 = serialise_record_dto(
                legacy_record_to_dto(legacy_record, schema=schema_map, canonicalize=False)
            )
            canonical_map[record.record_uid] = canonicalize_record(raw_v1, schema_map)
            raw_map[record.record_uid] = raw_v1
        return canonical_map, raw_map

    @staticmethod
    def _explain_record_diff(
        *,
        record_uid: str,
        schema_map: Mapping[str, Mapping[str, Any]],
        file_raw_record: Mapping[str, Any],
        sql_raw_record: Mapping[str, Any],
        file_canonical_record: Mapping[str, Any],
        sql_canonical_record: Mapping[str, Any],
    ) -> List[Dict[str, Any]]:
        details: List[Dict[str, Any]] = []
        file_raw_fields = file_raw_record.get("fields", {}) if isinstance(file_raw_record, Mapping) else {}
        sql_raw_fields = sql_raw_record.get("fields", {}) if isinstance(sql_raw_record, Mapping) else {}
        file_canonical_fields = file_canonical_record.get("fields", {}) if isinstance(file_canonical_record, Mapping) else {}
        sql_canonical_fields = sql_canonical_record.get("fields", {}) if isinstance(sql_canonical_record, Mapping) else {}

        for param_id in schema_map.keys():
            file_field = file_raw_fields.get(param_id, {}) if isinstance(file_raw_fields, Mapping) else {}
            sql_field = sql_raw_fields.get(param_id, {}) if isinstance(sql_raw_fields, Mapping) else {}
            file_canonical_field = (
                file_canonical_fields.get(param_id, {}) if isinstance(file_canonical_fields, Mapping) else {}
            )
            sql_canonical_field = (
                sql_canonical_fields.get(param_id, {}) if isinstance(sql_canonical_fields, Mapping) else {}
            )

            raw_file_value = file_field.get("value") if isinstance(file_field, Mapping) else file_field
            raw_sql_value = sql_field.get("value") if isinstance(sql_field, Mapping) else sql_field
            canonical_file_value = (
                file_canonical_field.get("value") if isinstance(file_canonical_field, Mapping) else file_canonical_field
            )
            canonical_sql_value = (
                sql_canonical_field.get("value") if isinstance(sql_canonical_field, Mapping) else sql_canonical_field
            )
            if canonical_file_value == canonical_sql_value:
                continue

            data_type = str(schema_map.get(param_id, {}).get("type", "TXT") or "TXT")
            reason = Command._classify_reason(
                data_type=data_type,
                raw_file_value=raw_file_value,
                raw_sql_value=raw_sql_value,
                canonical_file_value=canonical_file_value,
                canonical_sql_value=canonical_sql_value,
            )
            details.append(
                {
                    "record_uid": record_uid,
                    "parameter_id": param_id,
                    "parameter_name": str(schema_map.get(param_id, {}).get("name", "") or ""),
                    "reason": reason,
                    "file_value": Command._json_safe(raw_file_value),
                    "sql_value": Command._json_safe(raw_sql_value),
                    "canonical_file": Command._json_safe(canonical_file_value),
                    "canonical_sql": Command._json_safe(canonical_sql_value),
                }
            )
        return details

    @staticmethod
    def _classify_reason(
        *,
        data_type: str,
        raw_file_value: Any,
        raw_sql_value: Any,
        canonical_file_value: Any,
        canonical_sql_value: Any,
    ) -> str:
        del canonical_file_value
        del canonical_sql_value
        if is_empty_like(raw_file_value) or is_empty_like(raw_sql_value):
            return "null_vs_empty"
        normalised_type = str(data_type or "TXT").upper()
        if normalised_type == "DATE":
            return "date_format"
        if normalised_type == "ARRAY":
            return "array_format"
        if normalised_type == "INT":
            return "numeric_cast"
        if isinstance(raw_file_value, str) and isinstance(raw_sql_value, str):
            if raw_file_value != raw_file_value.strip() or raw_sql_value != raw_sql_value.strip():
                return "whitespace"
        return "value_mismatch"

    @staticmethod
    def _json_safe(value: Any) -> Any:
        if isinstance(value, Mapping):
            return {str(k): Command._json_safe(v) for k, v in value.items()}
        if isinstance(value, list):
            return [Command._json_safe(item) for item in value]
        if isinstance(value, tuple):
            return [Command._json_safe(item) for item in value]
        if isinstance(value, set):
            return [Command._json_safe(item) for item in sorted(value)]
        if isinstance(value, datetime):
            return value.isoformat()
        if isinstance(value, date):
            return value.isoformat()
        if is_empty_like(value):
            return None
        return value

    @staticmethod
    def _compare_links(
        *,
        obj: Object,
        data_service: ObjectDataService,
    ) -> Tuple[int, List[Dict[str, Any]]]:
        mismatch_count = 0
        details: List[Dict[str, Any]] = []
        relations = Object_ParentObject.objects.filter(parent_object=obj).select_related("object")
        for relation in relations:
            file_pairs: Set[Tuple[str, str]] = set()
            row_links = ObjectLink_identificators.objects.filter(object_link=relation)
            for row_link in row_links:
                parent_uid = data_service.resolve_record_uid_from_identifier(
                    obj=relation.parent_object,
                    identifier=str(row_link.parent_object_identificator),
                )
                child_uid = data_service.resolve_record_uid_from_identifier(
                    obj=relation.object,
                    identifier=str(row_link.object_identificator),
                )
                file_pairs.add((parent_uid, child_uid))

            sql_pairs: Set[Tuple[str, str]] = set()
            sql_links = RecordLink.objects.filter(object_link=relation).select_related("parent_record", "child_record")
            for sql_link in sql_links:
                sql_pairs.add((sql_link.parent_record.record_uid, sql_link.child_record.record_uid))

            if file_pairs != sql_pairs:
                mismatch_count += 1
                details.append(
                    {
                        "link_meta_id": relation.id,
                        "child_object_id": relation.object_id,
                        "file_pairs_count": len(file_pairs),
                        "sql_pairs_count": len(sql_pairs),
                        "missing_in_sql_count": len(file_pairs - sql_pairs),
                        "missing_in_file_count": len(sql_pairs - file_pairs),
                    }
                )
        return mismatch_count, details
