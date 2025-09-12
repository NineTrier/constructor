from __future__ import annotations

"""Management command to detect unknown filters in template placeholders.

This command iterates over all ``DocumentsPattern`` instances, parses
their associated template structures and placeholder specifications,
and reports any filters that are not registered in ``FILTERS_REGISTRY``.
It is useful during development to identify typos or unsupported
transformations in template definitions.
"""

from django.core.management.base import BaseCommand

from constructor.models import DocumentsPattern
from apps.documents.domain.document_template import DocumentTemplate
from apps.documents.domain.placeholders import find_placeholders_in_text, _parse_placeholder
from apps.documents.domain.filters import FILTERS_REGISTRY


class Command(BaseCommand):
    help = "Diagnose unknown filters used in templates."

    def handle(self, *args, **options) -> None:
        total = 0
        issue_count = 0
        for pattern in DocumentsPattern.objects.all().iterator():
            total += 1
            dt = DocumentTemplate.load(pattern.id)
            unknown_filters = set()
            # Iterate through all text nodes of the template
            for path, text in dt.structure.iter_text_nodes():
                for ph in find_placeholders_in_text(text):
                    # ph.raw includes delimiters '{:' and ':}', remove them to parse
                    # Remove leading '{:' and trailing ':}' while preserving internal content
                    content = ph.raw.strip()
                    if content.startswith("{:") and content.endswith(":}"):
                        content = content[2:-2].strip()
                    obj_key, field_key, filters = _parse_placeholder(content)
                    for name, _ in filters:
                        if name not in FILTERS_REGISTRY:
                            unknown_filters.add(name)
            if unknown_filters:
                issue_count += 1
                filters_list = ", ".join(sorted(unknown_filters))
                self.stdout.write(self.style.WARNING(
                    f"[{pattern.id}] '{getattr(pattern, 'name', '')}' contains unknown filters: {filters_list}"
                ))
        if issue_count == 0:
            self.stdout.write(self.style.SUCCESS(
                f"OK: {total} templates checked, no unknown filters found."
            ))
        else:
            self.stdout.write(self.style.ERROR(
                f"Checked {total} templates, {issue_count} have unknown filters."
            ))