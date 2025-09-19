"""
Models for tracking document rendering progress.

This file defines a simple ``RenderJob`` model used to monitor asynchronous
tasks that generate documents from templates.  It records the total number
of placeholders in the source, the number replaced, the output file path and
name, and the current status of the job.
"""

from __future__ import annotations

from django.db import models


class RenderJob(models.Model):
    """Represents a background document rendering job."""

    STATUS_PENDING = "pending"
    STATUS_RUNNING = "running"
    STATUS_COMPLETED = "completed"
    STATUS_FAILED = "failed"

    STATUS_CHOICES = [
        (STATUS_PENDING, "Pending"),
        (STATUS_RUNNING, "Running"),
        (STATUS_COMPLETED, "Completed"),
        (STATUS_FAILED, "Failed"),
    ]

    status = models.CharField(
        max_length=16, choices=STATUS_CHOICES, default=STATUS_PENDING
    )
    placeholders_total = models.PositiveIntegerField(default=0)
    placeholders_replaced = models.PositiveIntegerField(default=0)
    output_path = models.CharField(max_length=512, blank=True)
    output_name = models.CharField(max_length=255, blank=True)
    # Optional textual description of the error when the job fails.  This field
    # is populated only when ``status`` is set to ``failed``.
    error_message = models.TextField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self) -> str:
        return f"RenderJob #{self.pk} ({self.status})"