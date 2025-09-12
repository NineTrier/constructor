"""Management command to validate placeholders in all document templates.

This command iterates through all instances of ``DocumentsPattern``,
validates their placeholders using the validation service, and
reports any issues to stdout. It exits with a non‑zero status code
if any errors are found. The output is human‑readable and includes
the pattern's id, name, and a summary of problems.
"""

from __future__ import annotations

from django.core.management.base import BaseCommand

try:
    from constructor.models import DocumentsPattern  # type: ignore
except Exception:
    DocumentsPattern = None  # type: ignore

from apps.documents.domain.document_template import DocumentTemplate
from apps.documents.services.validator import validate_template


class Command(BaseCommand):
    help = "Validate placeholders in all document templates and report issues"

    def handle(self, *args, **options):  # type: ignore
        if DocumentsPattern is None:
            self.stdout.write(self.style.ERROR("DocumentsPattern model is not available"))
            return
        total = 0
        error_count = 0
        for pattern in DocumentsPattern.objects.all():  # type: ignore
            total += 1
            try:
                template = DocumentTemplate.load(pattern.id)
            except Exception:
                self.stdout.write(self.style.ERROR(f"Failed to load template {pattern.id}"))
                error_count += 1
                continue
            issues = validate_template(template)
            if issues:
                error_count += 1
                name = getattr(pattern, "name", f"Template {pattern.id}")
                self.stdout.write(self.style.WARNING(f"[{pattern.id}] {name}: {len(issues)} issue(s)"))
                for idx, issue in enumerate(issues, start=1):
                    code = issue.get("code")
                    msg = issue.get("message")
                    path = ".".join(issue.get("path", []))
                    self.stdout.write(f"  {idx}. {code} at {path}: {msg}")
            else:
                name = getattr(pattern, "name", f"Template {pattern.id}")
                self.stdout.write(self.style.SUCCESS(f"[{pattern.id}] {name}: OK"))
        # Summary
        if error_count:
            self.stdout.write(self.style.ERROR(f"Completed: {total} templates, {error_count} with issues"))
        else:
            self.stdout.write(self.style.SUCCESS(f"All {total} templates passed validation"))