import json
import logging
import uuid
from datetime import datetime
from typing import Any, Dict, Optional, Set, Tuple

import pandas as pd
from django.core.management.base import BaseCommand, CommandError
from django.utils.dateparse import parse_datetime

from ...application.services import ObjectDataService
from ...domain.normalize import schema_from_parameters
from ...infrastructure.repositories import FileRecordRepository, SqlRecordRepository
from ...models import Object, ObjectLinkMeta, ObjectLink_identificators, Object_ParentObject, Parameter, RecordLink

logger = logging.getLogger(__name__)

RECORD_UID_COLUMN = "record_uid"


class Command(BaseCommand):
    help = "Backfill dataframe-based records into SQL storage (ObjectRecord/ParameterValue/RecordLink)."

    def add_arguments(self, parser):
        parser.add_argument("--object-id", type=int, default=None, dest="object_id")
        parser.add_argument("--dry-run", action="store_true", default=False, dest="dry_run")
        parser.add_argument("--limit", type=int, default=None, dest="limit")
        parser.add_argument("--since", type=str, default=None, dest="since")
        parser.add_argument("--links", action="store_true", default=True, dest="links")
        parser.add_argument("--no-links", action="store_false", dest="links")
        parser.add_argument("--deep", action="store_true", default=False, dest="deep")

    def handle(self, *args, **options):
        object_id = options["object_id"]
        dry_run = bool(options["dry_run"])
        limit = options["limit"]
        since_raw = options["since"]
        include_links = bool(options["links"])
        deep_mode = bool(options["deep"])

        since_dt: Optional[datetime] = None
        if since_raw:
            since_dt = parse_datetime(since_raw)
            if since_dt is None:
                raise CommandError("--since must be a valid ISO datetime")

        data_service = ObjectDataService(logger=logger)
        file_repo = FileRecordRepository()
        sql_repo = SqlRecordRepository()

        objects_qs = Object.objects.all().order_by("id")
        if object_id is not None:
            objects_qs = objects_qs.filter(id=object_id)
        objects = list(objects_qs)

        processed_rows = 0
        created_or_updated = 0
        processed_links = 0
        compared = 0
        mismatches = 0
        deep_diff_count = 0

        for obj in objects:
            parameters = list(Parameter.objects.filter(object=obj).order_by("id"))
            df, warnings = file_repo.load_dataframe(obj, allow_empty=True)
            if warnings:
                self.stdout.write(self.style.WARNING(f"[object={obj.id}] load warnings: {'; '.join(warnings)}"))
            if df is None:
                continue
            df, changed = self._ensure_record_uid_column(obj=obj, df=df, data_service=data_service)
            if changed and not dry_run:
                file_repo.save_dataframe(obj, df)
            if "id_to_connect" not in df.columns:
                continue

            for _, row in df.iterrows():
                if limit is not None and processed_rows >= limit:
                    break
                record_uid = self._string_value(row.get(RECORD_UID_COLUMN, ""))
                legacy_id = self._string_value(row.get("id_to_connect", ""))
                if not record_uid and not legacy_id:
                    continue
                if not record_uid:
                    record_uid = data_service.resolve_record_uid(obj=obj, legacy_id=legacy_id)
                if not legacy_id:
                    legacy_id = record_uid
                existing_record = sql_repo.get_record_by_uid_or_legacy(obj, record_uid)
                if since_dt and existing_record is not None and existing_record.updated_at < since_dt:
                    continue

                fields: Dict[str, Dict[str, Any]] = {}
                for parameter in parameters:
                    key = str(parameter.id)
                    value = row.get(key, "")
                    fields[key] = {
                        "type": parameter.data_type,
                        "value": data_service._normalise_field_value(parameter, value),
                    }
                processed_rows += 1
                if dry_run:
                    continue
                sql_repo.upsert_record(
                    obj=obj,
                    record_uid=record_uid,
                    fields=fields,
                    legacy_id_to_connect=legacy_id,
                )
                created_or_updated += 1
            if limit is not None and processed_rows >= limit:
                break

        if include_links and not dry_run:
            link_qs = Object_ParentObject.objects.select_related("parent_object", "object").order_by("id")
            if object_id is not None:
                link_qs = link_qs.filter(parent_object_id=object_id)
            for meta_link in link_qs:
                row_links = ObjectLink_identificators.objects.filter(object_link=meta_link).select_related("object_link_meta")
                for row_link in row_links:
                    parent_identifier = str(row_link.parent_object_identificator)
                    child_identifier = str(row_link.object_identificator)
                    parent_uid = data_service.resolve_record_uid_from_identifier(
                        obj=meta_link.parent_object,
                        identifier=parent_identifier,
                    )
                    child_uid = data_service.resolve_record_uid_from_identifier(
                        obj=meta_link.object,
                        identifier=child_identifier,
                    )
                    parent_record = sql_repo.upsert_record(
                        obj=meta_link.parent_object,
                        record_uid=parent_uid,
                        fields={},
                        legacy_id_to_connect=parent_identifier,
                    )
                    child_record = sql_repo.upsert_record(
                        obj=meta_link.object,
                        record_uid=child_uid,
                        fields={},
                        legacy_id_to_connect=child_identifier,
                    )
                    sql_repo.upsert_link(
                        meta_link,
                        parent_record,
                        child_record,
                        object_link_meta=row_link.object_link_meta,
                    )
                    processed_links += 1

        for obj in objects:
            parameters = list(Parameter.objects.filter(object=obj).order_by("id"))
            schema_map = schema_from_parameters(parameters)
            file_df, _ = file_repo.load_dataframe(obj, allow_empty=True)
            if file_df is None:
                continue
            file_df, _ = self._ensure_record_uid_column(obj=obj, df=file_df, data_service=data_service)
            if "id_to_connect" not in file_df.columns:
                continue
            file_rows = int(file_df["id_to_connect"].dropna().shape[0])
            sql_rows = sql_repo.list_records(obj).count()
            sample_size = file_rows if deep_mode else min(5, file_rows)
            if sample_size > 0:
                for _, row in file_df.head(sample_size).iterrows():
                    record_uid = self._string_value(row.get(RECORD_UID_COLUMN, ""))
                    legacy_id = self._string_value(row.get("id_to_connect", ""))
                    identifier = record_uid or legacy_id
                    if not identifier:
                        continue
                    sql_record = sql_repo.get_record_by_uid_or_legacy(obj, identifier)
                    if sql_record is None:
                        mismatches += 1
                        compared += 1
                        continue
                    file_probe: Dict[str, Any] = {"id_to_connect": identifier}
                    for parameter in parameters:
                        key = str(parameter.id)
                        file_probe[key] = {
                            "data_type": parameter.data_type,
                            "value": data_service._normalise_field_value(parameter, row.get(key, "")),
                        }
                    sql_probe = sql_repo.serialise_record_to_legacy(sql_record, parameters)
                    compared += 1
                    if data_service._normalise_for_compare(file_probe, schema=schema_map) != data_service._normalise_for_compare(
                        sql_probe,
                        schema=schema_map,
                    ):
                        mismatches += 1

            if deep_mode:
                deep_mismatch = self._deep_compare_links(
                    obj=obj,
                    data_service=data_service,
                    sql_repo=sql_repo,
                )
                if deep_mismatch:
                    deep_diff_count += 1
                    logger.warning(
                        "backfill_deep_diff %s",
                        json.dumps(
                            {
                                "object_id": obj.id,
                                "mismatch_links": deep_mismatch,
                            },
                            ensure_ascii=False,
                            default=str,
                        ),
                    )

            stats_payload = {
                "object_id": obj.id,
                "file_rows": file_rows,
                "sql_rows": sql_rows,
                "compared": compared,
                "mismatches": mismatches,
                "deep_mode": deep_mode,
                "dry_run": dry_run,
            }
            logger.warning("backfill_stats %s", json.dumps(stats_payload, ensure_ascii=False, default=str))
            self.stdout.write(f"backfill_stats {json.dumps(stats_payload, ensure_ascii=False)}")

        self.stdout.write(
            self.style.SUCCESS(
                f"Done. processed_rows={processed_rows}, upserts={created_or_updated}, links={processed_links}, compared={compared}, mismatches={mismatches}, deep_diff={deep_diff_count}"
            )
        )

    @staticmethod
    def _ensure_record_uid_column(*, obj: Object, df: pd.DataFrame, data_service: ObjectDataService):
        changed = False
        if RECORD_UID_COLUMN not in df.columns:
            df[RECORD_UID_COLUMN] = pd.NA
            changed = True
        if "id_to_connect" not in df.columns:
            df["id_to_connect"] = pd.NA
            changed = True
        for idx, row in df.iterrows():
            record_uid = Command._string_value(row.get(RECORD_UID_COLUMN, ""))
            legacy_id = Command._string_value(row.get("id_to_connect", ""))
            if not record_uid:
                if legacy_id:
                    record_uid = data_service.resolve_record_uid(obj=obj, legacy_id=legacy_id)
                else:
                    record_uid = uuid.uuid4().hex
                df.at[idx, RECORD_UID_COLUMN] = record_uid
                changed = True
            if not legacy_id:
                df.at[idx, "id_to_connect"] = record_uid
                changed = True
        return df, changed

    @staticmethod
    def _string_value(value: Any) -> str:
        if value is None:
            return ""
        try:
            if pd.isna(value):
                return ""
        except Exception:
            pass
        return str(value).strip()

    @staticmethod
    def _deep_compare_links(*, obj: Object, data_service: ObjectDataService, sql_repo: SqlRecordRepository) -> int:
        mismatches = 0
        relations = Object_ParentObject.objects.filter(parent_object=obj).select_related("object")
        for relation in relations:
            relation_metas = list(ObjectLinkMeta.objects.filter(object_link=relation).order_by("order", "id"))
            if not relation_metas:
                relation_metas = [None]
            for relation_meta in relation_metas:
                file_pairs: Set[Tuple[str, str]] = set()
                row_links = ObjectLink_identificators.objects.filter(object_link=relation)
                if relation_meta is None:
                    row_links = row_links.filter(object_link_meta__isnull=True)
                else:
                    row_links = row_links.filter(object_link_meta=relation_meta)
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
                sql_links = RecordLink.objects.filter(object_link=relation)
                if relation_meta is None:
                    sql_links = sql_links.filter(object_link_meta__isnull=True)
                else:
                    sql_links = sql_links.filter(object_link_meta=relation_meta)
                sql_links = sql_links.select_related("parent_record", "child_record")
                for sql_link in sql_links:
                    sql_pairs.add((sql_link.parent_record.record_uid, sql_link.child_record.record_uid))
                if file_pairs != sql_pairs:
                    mismatches += 1
        return mismatches
