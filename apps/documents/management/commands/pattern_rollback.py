"""Rollback a template to a given version.

Usage::

    python manage.py pattern_rollback <pattern_id> --version-id <id>

This command will update the template's JSON structure to that stored
in the specified version.  The version must belong to the template.
"""

from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from constructor.models import DocumentsPattern
from apps.documents.models.versioning import DocumentPatternVersion
from apps.documents.services.versioning import VersioningService


class Command(BaseCommand):
    help = "Rollback a template to a specified version"

    def add_arguments(self, parser):
        parser.add_argument("pattern_id", type=int)
        parser.add_argument("--version-id", type=int, dest="version_id", required=True, help="Version id to roll back to")

    def handle(self, *args, **options):
        pattern_id = options["pattern_id"]
        version_id = options["version_id"]
        try:
            pattern = DocumentsPattern.objects.get(id=pattern_id)
        except DocumentsPattern.DoesNotExist:
            raise CommandError(f"Template with id {pattern_id} not found")
        try:
            version = DocumentPatternVersion.objects.get(id=version_id, pattern=pattern)
        except DocumentPatternVersion.DoesNotExist:
            raise CommandError(f"Version {version_id} not found for template {pattern_id}")
        service = VersioningService()
        service.rollback(pattern, version)
        self.stdout.write(self.style.SUCCESS(f"Template {pattern_id} rolled back to version {version.version_number}"))
