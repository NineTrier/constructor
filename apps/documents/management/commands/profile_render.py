from __future__ import annotations
import time
from django.core.management.base import BaseCommand

try:
    from constructor.models import DocumentsPattern
except Exception:
    from document.models import DocumentsPattern  # type: ignore

from apps.documents.domain.document_template import DocumentTemplate
from apps.documents.services.document_renderer import DocumentRenderer

class Command(BaseCommand):
    help = "Профилирование рендера: измеряет время и объём замен плейсхолдеров."

    def add_arguments(self, parser):
        parser.add_argument("pattern_id", type=int, help="ID шаблона")
        parser.add_argument("--ids", dest="ids", help="JSON словарь selected_ids, напр. '{\"Org\":\"1\"}'", default="{}")


    def handle(self, *args, **opts):
        pid = int(opts["pattern_id"])
        tpl = DocumentTemplate.load(pid)
        ids_raw = opts["ids"]
        try:
            import json as _json
            selected_ids = _json.loads(ids_raw) if ids_raw else {}
        except Exception:
            selected_ids = {}

        r = DocumentRenderer()
        t0 = time.time()
        path, diag = r.generate_docx_file(tpl, selected_ids)
        dt = (time.time() - t0) * 1000.0
        self.stdout.write(self.style.SUCCESS(f"OK: {tpl.model.id} rendered in {dt:.1f} ms, placeholders={diag.placeholders_total}, replaced={diag.placeholders_replaced}, issues={len(diag.issues)}"))
        self.stdout.write(str(path))
