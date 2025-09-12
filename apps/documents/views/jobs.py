"""View to poll the status and events of a render job.

The ``PollRenderJobView`` provides a simple JSON API for clients to
retrieve the current status and log messages associated with a
rendering job.  Frontends can use this endpoint to implement a
progress indicator for long‑running document generation tasks.
"""

from __future__ import annotations

from django.http import JsonResponse, Http404, HttpRequest
from django.views import View
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404
from apps.documents.models.progress import RenderJob
from django.urls import reverse
import os

from apps.documents.models.progress import RenderJob


class PollRenderJobView(View):
    """Return the current status and events of a render job."""

    def get(self, request: HttpRequest, job_id: int):
        try:
            job = RenderJob.objects.select_related("pattern").get(id=job_id)
        except RenderJob.DoesNotExist:
            raise Http404("Render job not found")
        events = [
            {"timestamp": e.timestamp.isoformat(), "message": e.message}
            for e in job.events.all()
        ]
        download_url = None
        if job.status == RenderJob.status == "completed" and job.output_path:
            download_url = reverse("job_download", args=[str(job.id)])
        data = {
            "status": job.status,
            "placeholders_total": job.placeholders_total,
            "placeholders_replaced": job.placeholders_replaced,
            "events": events,
            "output_file": job.output_file.url if job.output_file else None,
            "download_url": download_url,
        }
        return JsonResponse(data)

class DownloadRenderJobView(View):
    """Отдаёт результирующий DOCX независимо от MEDIA_ROOT."""
    def get(self, request, job_id):
        job = get_object_or_404(RenderJob, pk=job_id)
        if job.status != RenderJob.status == "completed" or not job.output_path:
            raise Http404("Result is not ready")
        if not os.path.exists(job.output_path):
            raise Http404("File not found")
        filename = job.output_name or f"document_{job.pattern_id}.docx"
        return FileResponse(
            open(job.output_path, "rb"),
            as_attachment=True,
            filename=smart_str(filename),
            content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        )