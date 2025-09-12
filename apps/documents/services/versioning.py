"""Service for managing versions of document templates.

The :class:`VersioningService` encapsulates the logic for creating
snapshots of a :class:`~constructor.models.DocumentsPattern`, marking
versions as published, and rolling back to an earlier version.

Snapshots capture the JSON structure of the template and assign
sequential version numbers per template. Publishing a version
updates the ``is_published`` flag on the chosen version and clears it
for all other versions of the same template. Rolling back updates
``DocumentsPattern.json`` to the stored JSON and (optionally) can
recreate the docx file, though that is out of scope here.
"""

from __future__ import annotations

from typing import Optional
from django.db import transaction
from django.core.exceptions import ObjectDoesNotExist

from constructor.models import DocumentsPattern

from ..models.versioning import DocumentPatternVersion


class VersioningService:
    """Provides methods to snapshot, publish and rollback templates."""

    def snapshot(self, pattern: DocumentsPattern, label: Optional[str] = None) -> DocumentPatternVersion:
        """Create a new version snapshot of the given template.

        The snapshot will have a version number one greater than the
        current maximum for this template and store the current
        ``json`` field from the pattern.  If ``label`` is provided it
        will be stored for later identification.
        """
        # Determine next version number
        last = (
            DocumentPatternVersion.objects.filter(pattern=pattern)
            .order_by("-version_number")
            .first()
        )
        next_number = 1 if last is None else last.version_number + 1
        # Create snapshot
        version = DocumentPatternVersion.objects.create(
            pattern=pattern,
            version_number=next_number,
            label=label or f"v{next_number}",
            json_data=pattern.json,
        )
        return version

    def publish(self, pattern: DocumentsPattern, version: Optional[DocumentPatternVersion] = None) -> DocumentPatternVersion:
        """Mark a specific version (or the latest) as published for a template.

        All other versions for the template will have ``is_published`` cleared.
        """
        if version is None:
            version = (
                DocumentPatternVersion.objects.filter(pattern=pattern)
                .order_by("-version_number")
                .first()
            )
            if version is None:
                raise ObjectDoesNotExist("No versions exist to publish")
        with transaction.atomic():
            # Clear existing published flags
            DocumentPatternVersion.objects.filter(pattern=pattern, is_published=True).update(is_published=False)
            # Mark chosen version as published
            version.is_published = True
            version.save(update_fields=["is_published"])
        return version

    def rollback(self, pattern: DocumentsPattern, version: DocumentPatternVersion) -> None:
        """Rollback the template to the specified version's JSON structure.

        The pattern's current JSON field is replaced with the stored JSON.
        """
        if version.pattern_id != pattern.id:
            raise ValueError("Version does not belong to this template")
        pattern.json = version.json_data
        pattern.save(update_fields=["json"])
        # Optionally update any docx preview here if needed
        return None
