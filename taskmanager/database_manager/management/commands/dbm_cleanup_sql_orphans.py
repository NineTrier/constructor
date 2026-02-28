import json
import logging
from typing import Any, Dict, List, Set

from django.core.management.base import BaseCommand, CommandError

from ...infrastructure.repositories import FileRecordRepository, SqlRecordRepository
from ...models import Object, ObjectRecord

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Detect and optionally delete SQL records missing in file storage."

    def add_arguments(self, parser):
        parser.add_argument("--object-id", type=int, default=None, dest="object_id")
        parser.add_argument("--limit", type=int, default=None, dest="limit")
        parser.add_argument("--apply", action="store_true", default=False, dest="apply")
        parser.add_argument("--soft-delete", action="store_true", default=False, dest="soft_delete")
        parser.add_argument("--whitelist", type=str, default="", dest="whitelist")

    def handle(self, *args, **options):
        object_id = options["object_id"]
        limit = options["limit"]
        apply_changes = bool(options["apply"])
        soft_delete = bool(options["soft_delete"])
        whitelist_raw = str(options["whitelist"] or "").strip()
        whitelist_ids = self._parse_whitelist(whitelist_raw)

        if apply_changes and object_id is None and not whitelist_ids:
            raise CommandError("For safety, --apply requires --object-id or --whitelist.")

        file_repo = FileRecordRepository()
        sql_repo = SqlRecordRepository()
        limit_value = int(limit) if limit is not None else None

        objects_qs = Object.objects.all().order_by("id")
        if object_id is not None:
            objects_qs = objects_qs.filter(id=object_id)
        if whitelist_ids:
            objects_qs = objects_qs.filter(id__in=list(whitelist_ids))
        objects = list(objects_qs)

        if object_id is not None and whitelist_ids and object_id not in whitelist_ids:
            raise CommandError("--object-id is not present in --whitelist.")

        supports_soft_delete = hasattr(ObjectRecord, "is_deleted")
        use_soft_delete = soft_delete and supports_soft_delete

        total_candidates = 0
        total_deleted = 0
        total_processed = 0
        total_with_warnings = 0
        object_reports: List[Dict[str, Any]] = []

        for obj in objects:
            rows, warnings = file_repo.list_raw_rows(
                obj,
                allow_empty=True,
                ensure_record_uid=True,
                persist=False,
            )
            if warnings:
                total_with_warnings += 1
            file_uids: Set[str] = set()
            for row in rows:
                record_uid = str(row.get("record_uid") or row.get("id_to_connect") or "").strip()
                if record_uid:
                    file_uids.add(record_uid)

            candidates: List[ObjectRecord] = []
            for record in sql_repo.list_records(obj, order_by="record_uid"):
                if record.record_uid not in file_uids:
                    candidates.append(record)

            if limit_value is not None:
                remaining = max(limit_value - total_processed, 0)
                if remaining <= 0:
                    candidates = []
                elif len(candidates) > remaining:
                    candidates = candidates[:remaining]

            deleted_for_object = 0
            if apply_changes:
                for candidate in candidates:
                    if use_soft_delete:
                        setattr(candidate, "is_deleted", True)
                        candidate.save(update_fields=["is_deleted"])
                    else:
                        sql_repo.delete_record(obj, candidate.record_uid)
                    deleted_for_object += 1
                    total_deleted += 1
                    total_processed += 1
            else:
                total_processed += len(candidates)

            total_candidates += len(candidates)
            object_reports.append(
                {
                    "object_id": obj.id,
                    "object_name": obj.name,
                    "candidates": len(candidates),
                    "deleted": deleted_for_object,
                    "warnings": warnings,
                    "uids_sample": [candidate.record_uid for candidate in candidates[:20]],
                }
            )

            if limit_value is not None and total_processed >= limit_value:
                break

        payload = {
            "objects_total": len(object_reports),
            "objects_with_candidates": sum(1 for item in object_reports if item["candidates"] > 0),
            "candidates": total_candidates,
            "deleted": total_deleted,
            "apply": apply_changes,
            "soft_delete_requested": soft_delete,
            "soft_delete_used": use_soft_delete,
            "limit": limit_value,
            "object_id": object_id,
            "whitelist": sorted(whitelist_ids),
            "objects_with_warnings": total_with_warnings,
            "reports": object_reports,
        }
        logger.warning("cleanup_sql_orphans_stats %s", json.dumps(payload, ensure_ascii=False, default=str))
        self.stdout.write(json.dumps(payload, ensure_ascii=False, default=str))

    @staticmethod
    def _parse_whitelist(raw: str) -> Set[int]:
        if not raw:
            return set()
        result: Set[int] = set()
        for chunk in raw.split(","):
            token = chunk.strip()
            if not token:
                continue
            try:
                result.add(int(token))
            except ValueError as exc:
                raise CommandError(f"Invalid whitelist token: {token}") from exc
        return result
