"""Models to store rendering progress and logs.

These models capture the state of a render job and the log events
generated during its execution. They are intended primarily for
observability: users can poll the status of a job while it's running
and view a log of key events after completion. The default
implementation stores simple text messages, but projects can extend
RenderEvent to include structured metadata if desired.
"""

from __future__ import annotations

from django.db import models
from django.utils import timezone
from typing import Optional

from constructor.models import DocumentsPattern


class RenderJob(models.Model):
    """Represents a single document generation run."""

    STATUS_CHOICES = [
        ("queued", "Queued"),
        ("running", "Running"),
        ("completed", "Completed"),
        ("failed", "Failed"),
    ]

    pattern = models.ForeignKey(
        DocumentsPattern,
        on_delete=models.CASCADE,
        related_name="render_jobs",
        help_text="Template being rendered",
    )
    created_at = models.DateTimeField(
        auto_now_add=True,
        help_text="Time when the render job was created",
    )
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default="queued",
        help_text="Current status of the render job",
    )
    placeholders_total = models.PositiveIntegerField(
        default=0,
        help_text="Total number of placeholders in the template",
    )
    placeholders_replaced = models.PositiveIntegerField(
        default=0,
        help_text="Number of placeholders successfully replaced",
    )
    output_file = models.FileField(
        upload_to="rendered_docs/",
        blank=True,
        null=True,
        help_text="Generated DOCX file",
    )
    meta = models.JSONField(
        blank=True,
        null=True,
        help_text="Arbitrary metadata (e.g. selected_ids)",
    )
    output_path = models.TextField(null=True, blank=True)
    output_name = models.CharField(max_length=255, null=True, blank=True)

    def mark_running(self):
        self.status = "running"
        self.save(update_fields=["status"])

    def mark_completed(self, output_file: Optional[str] = None):
        self.status = "completed"
        if output_file:
            # Save path or file to the output_file field if provided
            self.output_file.name = output_file
        self.save(update_fields=["status", "output_file"])

    def mark_failed(self):
        self.status = "failed"
        self.save(update_fields=["status"])

    def log_event(self, message: str) -> "RenderEvent":
        return RenderEvent.objects.create(job=self, message=message)


class RenderEvent(models.Model):
    """A log message associated with a ``RenderJob``."""

    job = models.ForeignKey(
        RenderJob,
        on_delete=models.CASCADE,
        related_name="events",
        help_text="Render job this event belongs to",
    )
    timestamp = models.DateTimeField(
        default=timezone.now,
        help_text="Time of the event",
    )
    message = models.TextField(
        help_text="Log message",
    )

    class Meta:
        ordering = ["timestamp"]

    def __str__(self) -> str:
        return f"[{self.timestamp:%H:%M:%S}] {self.message}"
