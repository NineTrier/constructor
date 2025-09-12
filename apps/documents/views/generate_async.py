"""Asynchronous‑style document generation view.

This view emulates asynchronous document generation by creating a
render job, performing the rendering synchronously within the
request, logging progress events, and returning a job identifier.  A
separate polling endpoint can be used to retrieve the status and
events of the job.

In a production environment this logic could be offloaded to a
background worker (e.g. Celery, RQ) instead of running in the HTTP
request thread.  The current implementation is synchronous for
simplicity.
"""

from __future__ import annotations

import json
from typing import Dict, Any

from django.http import HttpRequest, JsonResponse, HttpResponseBadRequest
from django.views import View

from apps.documents.domain.document_template import DocumentTemplate
from apps.documents.services.document_renderer import DocumentRenderer
from apps.documents.services.progress import ProgressLogger


class GenerateDocumentAsyncView(View):
    """Start a render job and return its identifier."""

    renderer = DocumentRenderer()
    logger = ProgressLogger()

    def post(self, request: HttpRequest, pattern_id: int):
        # Parse selected ids from JSON body
        try:
            payload = json.loads(request.body or "{}")
            selected_ids = payload.get("selected_ids", {}) or {}
            if not isinstance(selected_ids, dict):
                return HttpResponseBadRequest("selected_ids must be an object")
        except json.JSONDecodeError:
            return HttpResponseBadRequest("Invalid JSON in request body")
        # Load template and start job
        template = DocumentTemplate.load(pattern_id)
        job = self.logger.start_job(template.model)
        # Save meta for reference
        job.meta = {"selected_ids": selected_ids}
        job.save(update_fields=["meta"])
        try:
            # Log start and mark running
            self.logger.log(job, "Job started")
            self.logger.mark_running(job)
            # Count placeholders
            total_placeholders = sum(1 for _ in template.iter_placeholders())
            self.logger.log(job, f"Detected {total_placeholders} placeholder(s)")
            # Perform rendering
            doc_path = self.renderer.generate_docx_file(template, selected_ids)
            job.output_path = str(doc_path)
            job.output_name = f"{template.model.name or 'document'}_{job.id}.docx"
            # All placeholders replaced (as we do direct substitution)
            self.logger.set_placeholder_counts(job, total_placeholders, total_placeholders)
            self.logger.log(job, "Rendering completed")
            # Mark completion and store path in job
            # Use relative path name for FileField if defined; for now save as string path
            job.output_file.name = str(doc_path)
            self.logger.mark_completed(job, output_file=str(doc_path))
        except Exception as exc:
            self.logger.log(job, f"Rendering failed: {exc}")
            self.logger.mark_failed(job)
        # Return job id
        return JsonResponse({"job_id": job.id})
