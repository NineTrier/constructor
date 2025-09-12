"""Service to log progress events for document rendering.

The :class:`ProgressLogger` wraps interactions with the
:class:`~apps.documents.models.progress.RenderJob` and
:class:`~apps.documents.models.progress.RenderEvent` models.  It
simplifies the creation of a new render job, recording of events
during rendering, and marking completion or failure.

This implementation is intentionally minimal: in real deployments you
may want to push events to a message broker or websocket, include
structured metadata in events, or integrate with a celery task.  Here
we simply write messages to the database for polling via HTTP.
"""

from __future__ import annotations

from typing import Dict, Optional, Any
from django.db import transaction

from ..models.progress import RenderJob, RenderEvent


class ProgressLogger:
    """Helper for recording rendering progress and events."""

    def start_job(self, pattern) -> RenderJob:
        job = RenderJob.objects.create(pattern=pattern, status="queued")
        return job

    def log(self, job: RenderJob, message: str) -> RenderEvent:
        return job.log_event(message)

    def mark_running(self, job: RenderJob) -> None:
        job.mark_running()

    def mark_completed(self, job: RenderJob, output_file: Optional[str] = None) -> None:
        job.mark_completed(output_file)

    def mark_failed(self, job: RenderJob) -> None:
        job.mark_failed()

    def set_placeholder_counts(self, job: RenderJob, total: int, replaced: int) -> None:
        job.placeholders_total = total
        job.placeholders_replaced = replaced
        job.save(update_fields=["placeholders_total", "placeholders_replaced"])
