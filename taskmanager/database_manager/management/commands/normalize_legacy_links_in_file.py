import json
import logging
from typing import Dict, Optional, Set, Tuple

from django.core.management.base import BaseCommand
from django.db.models import Q

from ...application.services import ObjectDataService
from ...infrastructure.repositories import FileRecordRepository
from ...models import Object, ObjectLinkMeta, ObjectLink_identificators, Object_ParentObject

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Normalize legacy row links to stable record_uid identifiers and remove orphan links."

    def add_arguments(self, parser):
        parser.add_argument("--object-id", type=int, default=None, dest="object_id")
        parser.add_argument("--dry-run", action="store_true", default=False, dest="dry_run")
        parser.add_argument("--limit", type=int, default=None, dest="limit")

    def handle(self, *args, **options):
        object_id = options["object_id"]
        dry_run = bool(options["dry_run"])
        limit = options["limit"]
        processed = 0
        fixed = 0
        removed = 0
        collisions = 0

        data_service = ObjectDataService(logger=logger)
        file_repo = FileRecordRepository()
        cache: Dict[int, Tuple[Dict[str, str], Set[str]]] = {}

        links_qs = ObjectLink_identificators.objects.select_related(
            "object_link",
            "object_link_meta",
            "object_link__parent_object",
            "object_link__object",
        ).order_by("id")
        if object_id is not None:
            links_qs = links_qs.filter(
                Q(object_link__parent_object_id=object_id) | Q(object_link__object_id=object_id)
            )

        for row_link in links_qs:
            if limit is not None and processed >= int(limit):
                break
            processed += 1

            relation: Object_ParentObject = row_link.object_link
            parent_mapping, parent_ambiguous = self._load_identifier_mapping(
                obj=relation.parent_object,
                data_service=data_service,
                file_repo=file_repo,
                cache=cache,
            )
            child_mapping, child_ambiguous = self._load_identifier_mapping(
                obj=relation.object,
                data_service=data_service,
                file_repo=file_repo,
                cache=cache,
            )
            parent_identifier = str(row_link.parent_object_identificator)
            child_identifier = str(row_link.object_identificator)

            parent_uid: Optional[str] = None
            child_uid: Optional[str] = None
            if parent_identifier in parent_ambiguous:
                collisions += 1
            else:
                parent_uid = parent_mapping.get(parent_identifier)
            if child_identifier in child_ambiguous:
                collisions += 1
            else:
                child_uid = child_mapping.get(child_identifier)

            if not parent_uid or not child_uid:
                removed += 1
                if not dry_run:
                    row_link.delete()
                continue

            default_meta = row_link.object_link_meta
            if default_meta is None:
                default_meta = (
                    ObjectLinkMeta.objects.filter(object_link=relation).order_by("order", "id").first()
                )

            if (
                parent_uid == parent_identifier
                and child_uid == child_identifier
                and (default_meta is None or row_link.object_link_meta_id == default_meta.id)
            ):
                continue

            fixed += 1
            if not dry_run:
                row_link.parent_object_identificator = parent_uid
                row_link.object_identificator = child_uid
                if default_meta is not None:
                    row_link.object_link_meta = default_meta
                    row_link.save(
                        update_fields=["parent_object_identificator", "object_identificator", "object_link_meta"]
                    )
                else:
                    row_link.save(update_fields=["parent_object_identificator", "object_identificator"])

        payload = {
            "processed": processed,
            "fixed": fixed,
            "removed": removed,
            "collisions": collisions,
            "dry_run": dry_run,
            "object_id": object_id,
        }
        logger.warning("normalize_legacy_links_stats %s", json.dumps(payload, ensure_ascii=False))
        self.stdout.write(f"normalize_legacy_links_stats {json.dumps(payload, ensure_ascii=False)}")

    @staticmethod
    def _load_identifier_mapping(
        *,
        obj: Object,
        data_service: ObjectDataService,
        file_repo: FileRecordRepository,
        cache: Dict[int, Tuple[Dict[str, str], Set[str]]],
    ) -> Tuple[Dict[str, str], Set[str]]:
        if obj.id in cache:
            return cache[obj.id]

        rows, _ = file_repo.list_raw_rows(obj, allow_empty=True, ensure_record_uid=True, persist=False)
        mapping: Dict[str, str] = {}
        ambiguous: Set[str] = set()
        for row in rows:
            record_uid = str(row.get("record_uid") or "").strip()
            legacy_id = str(row.get("id_to_connect") or "").strip()
            if not record_uid:
                if legacy_id:
                    record_uid = data_service.resolve_record_uid(obj=obj, legacy_id=legacy_id)
                else:
                    continue
            for identifier in [record_uid, legacy_id]:
                if not identifier:
                    continue
                existing = mapping.get(identifier)
                if existing is not None and existing != record_uid:
                    ambiguous.add(identifier)
                    continue
                mapping[identifier] = record_uid
        cache[obj.id] = (mapping, ambiguous)
        return mapping, ambiguous
