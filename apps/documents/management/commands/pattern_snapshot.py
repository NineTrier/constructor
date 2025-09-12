"""Create a snapshot version of a template.

Usage::

    python manage.py pattern_snapshot <pattern_id> [--label "optional label"]

This command creates a new version for the specified template.  The
version will store the current JSON representation of the template.
"""

from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from constructor.models import DocumentsPattern
from apps.documents.services.versioning import VersioningService


class Command(BaseCommand):
    help = "Snapshot a template into a new version"

    def add_arguments(self, parser):
        parser.add_argument("pattern_id", type=int)
        parser.add_argument("--label", dest="label", default=None, help="Optional label for the version")

    def handle(self, *args, **options):
        pattern_id = options["pattern_id"]
        label = options["label"]
        try:
            pattern = DocumentsPattern.objects.get(id=pattern_id)
        except DocumentsPattern.DoesNotExist:
            raise CommandError(f"Template with id {pattern_id} not found")
        service = VersioningService()
        version = service.snapshot(pattern, label)
        self.stdout.write(self.style.SUCCESS(f"Created version {version.version_number} (id={version.id}) for template {pattern_id}"))
