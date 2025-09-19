"""
Views to poll and download render jobs with corrected status checks and imports.

This module defines the ``PollRenderJobView`` and ``DownloadRenderJobView`` used to
monitor asynchronous document generation tasks and download the resulting files
once rendering has completed.  It fixes several issues from the original
implementation: duplicate imports, incorrect status comparisons and a missing
``smart_str`` import.
"""

from __future__ import annotations

import os
from django.http import JsonResponse, Http404, HttpRequest, FileResponse
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.utils.encoding import smart_str
from django.views import View

from apps.documents.models.progress import RenderJob


class PollRenderJobView(View):
    """
    Return the current status and events of a render job.

    This view returns a JSON payload containing the job status, placeholder
    counts, generation events, and a download URL once the job has completed.
    """

    def get(self, request: HttpRequest, job_id: int):
        """Handle GET requests to retrieve job status and event logs."""
        # Attempt to load the render job. A 404 response is returned if the job
        # does not exist.
        try:
            # Fetch the job without any related object; the simplified
            # ``RenderJob`` model does not define a ``pattern`` foreign key.
            job = RenderJob.objects.get(id=job_id)
        except RenderJob.DoesNotExist:
            raise Http404("Render job not found")

        # The simplified ``RenderJob`` model does not store individual
        # generation events, so return an empty list for compatibility.
        events = []

        # Only expose the download URL once the job has finished successfully
        # and an output path has been recorded. Comparing against the literal
        # string "completed" avoids the previous bug where the class itself
        # was compared to a string.
        download_url = None
        if job.status == "completed" and job.output_path:
            download_url = reverse("job_download", args=[str(job.id)])

        # Build the JSON response. Only include a download_url when it is
        # available. The output_file field from the original implementation has
        # been removed to prevent the front‑end from constructing a link into
        # MEDIA_ROOT; instead clients should rely solely on ``download_url``.
        data = {
            "status": job.status,
            "placeholders_total": job.placeholders_total,
            "placeholders_replaced": job.placeholders_replaced,
            "events": events,
        }
        # Include a human‑readable error message if the job has failed.
        if job.status == "failed" and job.error_message:
            data["error_message"] = job.error_message

        if download_url:
            data["download_url"] = download_url
        return JsonResponse(data)


class DownloadRenderJobView(View):
    """
    Serve the generated DOCX independent of ``MEDIA_ROOT``.

    This view retrieves the generated document from its output path and returns
    it as a file response. If the job has not completed successfully or the
    output file does not exist, a 404 response is returned.
    """

    def get(self, request: HttpRequest, job_id: int) -> FileResponse:
        """Handle GET requests to download the generated document."""
        job = get_object_or_404(RenderJob, pk=job_id)

        # If the job hasn't completed or there is no output path recorded, the
        # result is not yet ready for download. Compare against the literal
        # string "completed" instead of the class to avoid false negatives.
        if job.status != "completed" or not job.output_path:
            raise Http404("Result is not ready")

        # Validate that the file actually exists on disk.
        if not os.path.exists(job.output_path):
            raise Http404("File not found")

        filename = job.output_name or f"document_{job.pattern_id}.docx"
        return FileResponse(
            open(job.output_path, "rb"),
            as_attachment=True,
            filename=smart_str(filename),
            content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        )