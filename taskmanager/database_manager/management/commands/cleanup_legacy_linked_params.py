import json
import logging
from typing import List

from django.core.management.base import BaseCommand

from ...models import Parameter


logger = logging.getLogger("taskmanager.database_manager")


class Command(BaseCommand):
    help = "Mark legacy linked parameters as deprecated without physical deletion."

    def add_arguments(self, parser):
        parser.add_argument("--object-id", action="append", dest="object_ids", default=[])
        parser.add_argument("--limit", type=int, default=None)
        parser.add_argument("--apply", action="store_true", default=False)

    def handle(self, *args, **options):
        object_ids_raw: List[str] = list(options.get("object_ids") or [])
        limit = options.get("limit")
        apply_changes = bool(options.get("apply", False))

        queryset = Parameter.objects.filter(
            linked_object__isnull=False,
            link_meta__isnull=True,
            is_managed_link_param=False,
        ).order_by("object_id", "id")

        object_ids: List[int] = []
        for raw in object_ids_raw:
            try:
                object_ids.append(int(raw))
            except (TypeError, ValueError):
                continue
        if object_ids:
            queryset = queryset.filter(object_id__in=object_ids)

        if isinstance(limit, int) and limit > 0:
            queryset = queryset[:limit]

        parameters = list(queryset)
        already_deprecated = 0
        to_mark = []
        for parameter in parameters:
            if bool(getattr(parameter, "is_legacy_link_param_deprecated", False)):
                already_deprecated += 1
            else:
                to_mark.append(parameter)

        if apply_changes and to_mark:
            Parameter.objects.filter(id__in=[item.id for item in to_mark]).update(
                is_legacy_link_param_deprecated=True,
            )

        payload = {
            "object_ids": object_ids,
            "scanned": len(parameters),
            "already_deprecated": already_deprecated,
            "marked_deprecated": len(to_mark) if apply_changes else 0,
            "would_mark_deprecated": len(to_mark) if not apply_changes else 0,
            "apply": apply_changes,
        }
        logger.warning("cleanup_legacy_linked_params_stats %s", json.dumps(payload, ensure_ascii=False))
        self.stdout.write(f"cleanup_legacy_linked_params_stats {json.dumps(payload, ensure_ascii=False)}")
