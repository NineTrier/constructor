"""Management command to migrate legacy placeholders.

This command scans all ``DocumentsPattern`` instances and attempts to
prefix legacy placeholders of the form ``{: param :}`` with the name of
the only connected object when exactly one object is connected. The
original placeholder syntax is preserved if multiple objects are
connected, ensuring backwards compatibility. Use the ``--dry-run``
flag to preview changes without persisting them.
"""

from __future__ import annotations

from django.core.management.base import BaseCommand
from django.db import transaction

from constructor.models import DocumentsPattern
from apps.documents.domain.document_template import DocumentTemplate, TemplateStructure
from apps.documents.domain.placeholders import find_placeholders_in_text


class Command(BaseCommand):
    help = (
        "Prefix legacy placeholders with the sole connected object's "
        "name when exactly one object is attached."
    )

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Print changes without saving them to the database",
        )

    def handle(self, *args, **options):
        dry_run: bool = bool(options.get("dry_run"))
        updated_count = 0
        checked_count = 0

        with transaction.atomic():
            for pattern in DocumentsPattern.objects.all().iterator():
                checked_count += 1
                template = DocumentTemplate.load(pattern.id)
                structure = TemplateStructure(template.to_json())
                changed = False

                # Build a name index of connected objects
                connected = {obj["name"]: obj["id"] for obj in template.connected_objects}

                # Iterate over text nodes and replace placeholders as needed
                for path, text in structure.iter_text_nodes():
                    new_text = text
                    for ph in find_placeholders_in_text(text):
                        if ph.object_key is None and len(connected) == 1:
                            only_name = next(iter(connected.keys()))
                            new_text = new_text.replace(
                                ph.raw, f"{{: {only_name}.{ph.field_key} :}}"
                            )
                            changed = True
                    if new_text != text:
                        structure.replace_text_at_path(path, new_text)

                if changed:
                    updated_count += 1
                    if not dry_run:
                        template.structure = structure
                        template.save()

            # Roll back transaction on dry run to avoid changes
            if dry_run:
                transaction.set_rollback(True)

        msg = f"Checked={checked_count}, Updated={updated_count}, dry_run={dry_run}"
        self.stdout.write(self.style.SUCCESS(msg))
