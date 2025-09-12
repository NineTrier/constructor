"""
Management command to diagnose object relations used by document templates.

This command examines each document template (``DocumentsPattern``) and
its connected objects to determine whether there is a valid join path
between every pair of objects.  When generating a document, the
renderer may need to resolve a placeholder that refers to a parent
object without an explicitly selected record ID.  In that case it
relies on relations between the selected objects and the referenced
object.  This diagnostic helps identify missing or broken relations
that could lead to unresolved placeholders.
"""

from __future__ import annotations

from django.core.management.base import BaseCommand
from typing import Optional

try:
    from constructor.models import DocumentsPattern  # type: ignore
except Exception:
    DocumentsPattern = None  # type: ignore

from apps.documents.domain.document_template import DocumentTemplate
from apps.documents.services.datajoiner import DataJoiner


class Command(BaseCommand):
    help = "Check that connected objects in document templates have join paths between them."

    def handle(self, *args: str, **options: str) -> None:
        if DocumentsPattern is None:
            self.stdout.write(self.style.ERROR("DocumentsPattern model could not be imported."))
            return
        joiner = DataJoiner()
        total_templates = 0
        issues = 0
        for pattern in DocumentsPattern.objects.all():  # type: ignore
            total_templates += 1
            try:
                dt = DocumentTemplate.load(pattern.id)
            except Exception:
                continue
            objs = dt.connected_objects
            obj_ids = [obj_meta["id"] for obj_meta in objs]
            # Check each pair of distinct objects for a join path
            for start_id in obj_ids:
                for target_id in obj_ids:
                    if start_id == target_id:
                        continue
                    path = joiner._find_path(start_id, target_id)
                    if not path:
                        issues += 1
                        self.stdout.write(self.style.WARNING(
                            f"Template id={pattern.id} ('{getattr(pattern, 'name', '')}') has no relation path from Object {start_id} to Object {target_id}."
                        ))
        if issues:
            self.stdout.write(self.style.ERROR(f"Checked {total_templates} templates, found {issues} relation issues."))
        else:
            self.stdout.write(self.style.SUCCESS(f"All {total_templates} templates have join paths between connected objects."))