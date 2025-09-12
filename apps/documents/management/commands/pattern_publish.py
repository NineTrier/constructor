"""Publish a version of a template.

Usage::

    python manage.py pattern_publish <pattern_id> [--version-id <id>]

If ``version-id`` is omitted, the most recent version for the template
will be published.  All other versions of the template will be
unpublished.
"""

from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from constructor.models import DocumentsPattern
from apps.documents.models.versioning import DocumentPatternVersion
from apps.documents.services.versioning import VersioningService


class Command(BaseCommand):
    help = "Publish a version of a template"

    def add_arguments(self, parser):
        parser.add_argument("pattern_id", type=int)
        parser.add_argument("--version-id", type=int, dest="version_id", default=None, help="Version id to publish (defaults to latest)")

    def handle(self, *args, **options):
        pattern_id = options["pattern_id"]
        version_id = options["version_id"]
        try:
            pattern = DocumentsPattern.objects.get(id=pattern_id)
        except DocumentsPattern.DoesNotExist:
            raise CommandError(f"Template with id {pattern_id} not found")
        service = VersioningService()
        version = None
        if version_id is not None:
            try:
                version = DocumentPatternVersion.objects.get(id=version_id, pattern=pattern)
            except DocumentPatternVersion.DoesNotExist:
                raise CommandError(f"Version {version_id} not found for template {pattern_id}")
        version = service.publish(pattern, version)
        self.stdout.write(self.style.SUCCESS(f"Published version {version.version_number} (id={version.id}) for template {pattern_id}"))
