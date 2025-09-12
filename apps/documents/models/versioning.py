"""Models to support versioning of document templates.

This module defines a model that stores historical versions of a
``DocumentsPattern``. Each version captures the JSON structure of the
template at the time of snapshot along with optional metadata such as
a label and whether the version has been published. Versions enable
rollback to previous states and safe experimentation without losing
prior work.

The fields defined here are minimal; projects may extend this model
with additional fields (e.g. author relationships) as needed.
"""

from __future__ import annotations

from django.db import models

from constructor.models import DocumentsPattern


class DocumentPatternVersion(models.Model):
    """Snapshot of a ``DocumentsPattern`` at a specific point in time."""

    pattern = models.ForeignKey(
        DocumentsPattern,
        on_delete=models.CASCADE,
        related_name="versions",
        help_text="Template to which this version belongs",
    )
    version_number = models.PositiveIntegerField(
        help_text="Sequential version number (1-based)",
    )
    label = models.CharField(
        max_length=255,
        blank=True,
        help_text="Optional human‑friendly label for this version",
    )
    created_at = models.DateTimeField(
        auto_now_add=True,
        help_text="Timestamp when the snapshot was taken",
    )
    json_data = models.JSONField(
        help_text="Captured JSON structure of the template",
    )
    is_published = models.BooleanField(
        default=False,
        help_text="Whether this version has been marked as published",
    )

    class Meta:
        unique_together = ("pattern", "version_number")
        ordering = ["pattern", "-version_number"]

    def __str__(self) -> str:
        label = f" ({self.label})" if self.label else ""
        return f"{self.pattern_id}@{self.version_number}{label}"
