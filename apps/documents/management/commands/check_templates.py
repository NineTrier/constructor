from __future__ import annotations
from django.core.management.base import BaseCommand
from constructor.models import DocumentsPattern, Parameter
from apps.documents.domain.document_template import DocumentTemplate
from apps.documents.domain.placeholders import find_placeholders_in_text

class Command(BaseCommand):
    help = "Диагностика шаблонов: плейсхолдеры без объекта при нескольких источниках; объекты без identificator."

    def handle(self, *args, **opts):
        total = 0
        issues = 0
        for m in DocumentsPattern.objects.all().iterator():
            total += 1
            dt = DocumentTemplate.load(m.id)
            connected = dt.connected_objects

            # Идентификаторы
            missing_id = []
            for o in connected:
                if not Parameter.objects.filter(object_id=o["id"], identificator=True).exists():
                    missing_id.append(o["name"])
            if missing_id:
                self.stdout.write(self.style.WARNING(
                    f"[{m.id}] '{getattr(m,'name','')}' — у объектов без identificator: {', '.join(missing_id)}"
                ))
                issues += 1

            # Плейсхолдеры
            ambiguous = 0
            for path, text in dt.structure.iter_text_nodes():
                for ph in find_placeholders_in_text(text):
                    if ph.object_key is None and len(connected) > 1:
                        ambiguous += 1
            if ambiguous:
                self.stdout.write(self.style.WARNING(
                    f"[{m.id}] '{getattr(m,'name','')}' — {ambiguous} плейсхолдер(ов) без objectKey при >1 источнике"
                ))
                issues += 1

        if issues == 0:
            self.stdout.write(self.style.SUCCESS(f"OK: {total} шаблонов, проблем не выявлено"))
        else:
            self.stdout.write(self.style.ERROR(f"Проверено: {total}, найдено проблем: {issues}"))
