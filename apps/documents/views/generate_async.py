"""
Asynchronous document generation view that reads templates from the database.

This view loads the template HTML from the ``DocumentsPattern.json`` field,
performs placeholder replacement based on the provided context, converts the
resulting text into a DOCX file using a built‑in fallback (no external
dependencies), and tracks the job status via the ``RenderJob`` model.

Clients should send a POST request to ``/documents/<pattern_id>/generate/async/``
with an optional ``context`` parameter (JSON) that maps placeholder
expressions to replacement values.  The response contains a ``job_id`` that
can be used to poll for status and download the generated file.
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
import zipfile
import xml.sax.saxutils as saxutils
import re
import html as html_module
from typing import Any, Dict, List

from django.http import JsonResponse, HttpRequest, HttpResponseBadRequest
from django.views import View

from ..models.progress import RenderJob
from ..services.placeholder_utils import extract_placeholders, replace_placeholders
from ..services.json_template import parse_document_json


try:
    # Attempt to import the DocumentsPattern model.  Adjust the import path
    # according to your project structure.
    from document.models import DocumentsPattern # type: ignore
except Exception:
    DocumentsPattern = None  # type: ignore


class GenerateDocumentAsyncView(View):
    """Handle POST requests to start an asynchronous document rendering job."""

    def post(self, request: HttpRequest, pattern_id: int = None) -> JsonResponse:
        # Ensure we have a pattern id to load the template.
        if pattern_id is None:
            return HttpResponseBadRequest("Missing pattern_id")

        # Load the template JSON from the database.
        if DocumentsPattern is None:
            return HttpResponseBadRequest("DocumentsPattern model is not available")
        try:
            pattern = DocumentsPattern.objects.get(pk=pattern_id)
        except Exception:
            return HttpResponseBadRequest("Invalid pattern_id")
        raw_json = pattern.json or {}

        # Deserialize paragraphs from the raw JSON structure.
        paragraphs = parse_document_json(raw_json)
        if not paragraphs:
            return HttpResponseBadRequest("Template is empty or could not be parsed")

        # Read placeholder values from the optional context JSON.
        context_json = request.POST.get("context") or "{}"
        try:
            context: Dict[str, Any] = json.loads(context_json)
        except json.JSONDecodeError:
            return HttpResponseBadRequest("Invalid JSON for context")

        # Create the job record with pending status.
        job = RenderJob.objects.create(status=RenderJob.STATUS_PENDING)

        # Launch a background thread to perform the rendering.
        thread = threading.Thread(
            target=self._render_job, args=(job.id, paragraphs, context), daemon=True
        )
        thread.start()

        # Return the job identifier to the client.
        return JsonResponse({"job_id": job.id})

    def _render_job(self, job_id: int, paragraphs: List[str], context: Dict[str, Any]) -> None:
        """Worker function to render the document and update the job status."""
        job = RenderJob.objects.get(id=job_id)
        job.status = RenderJob.STATUS_RUNNING
        job.save(update_fields=["status"])

        # Count placeholders across all paragraphs and replace them.
        all_placeholders: List[str] = []
        replaced_paragraphs: List[str] = []
        replaced_count = 0
        for para in paragraphs:
            placeholders = extract_placeholders(para)
            all_placeholders.extend(placeholders)
            replaced_para = replace_placeholders(para, context)
            # Count replaced placeholders: those present in context
            replaced_count += sum(1 for p in placeholders if p in context)
            replaced_paragraphs.append(replaced_para)
        job.placeholders_total = len(all_placeholders)
        job.placeholders_replaced = replaced_count
        job.save(update_fields=["placeholders_total", "placeholders_replaced"])

        try:
            # Write the replaced paragraphs into a DOCX file using fallback.
            out_dir = tempfile.mkdtemp()
            output_filename = f"document_{job_id}.docx"
            output_path = os.path.join(out_dir, output_filename)
            self._write_docx(replaced_paragraphs, output_path)

            job.output_path = output_path
            job.output_name = output_filename
            job.status = RenderJob.STATUS_COMPLETED
            job.error_message = ""
            job.save(update_fields=["output_path", "output_name", "status", "error_message"])
        except Exception as exc:
            job.status = RenderJob.STATUS_FAILED
            job.error_message = str(exc)
            job.save(update_fields=["status", "error_message"])

    def _write_docx(self, paragraphs: List[str], output_path: str) -> None:
        """
        Create a minimal DOCX file from a list of paragraphs.

        Each paragraph is written as a WordprocessingML paragraph.  This
        implementation avoids external dependencies and should work in
        constrained environments.
        """
        # Build XML for each paragraph.
        body_elements: List[str] = []
        for para in paragraphs:
            escaped = saxutils.escape(para)
            body_elements.append(
                f"<w:p><w:r><w:t xml:space=\"preserve\">{escaped}</w:t></w:r></w:p>"
            )
        body_elements.append(
            "<w:sectPr><w:pgSz w:w=\"12240\" w:h=\"15840\"/><w:pgMar w:top=\"1440\" w:right=\"1440\" w:bottom=\"1440\" w:left=\"1440\" "
            "w:header=\"720\" w:footer=\"720\" w:gutter=\"0\"/></w:sectPr>"
        )
        document_xml = (
            "<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?>"
            "<w:document xmlns:w=\"http://schemas.openxmlformats.org/wordprocessingml/2006/main\">"
            "<w:body>"
            + "".join(body_elements)
            + "</w:body></w:document>"
        )
        content_types = (
            "<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?>"
            "<Types xmlns=\"http://schemas.openxmlformats.org/package/2006/content-types\">"
            "<Default Extension=\"rels\" ContentType=\"application/vnd.openxmlformats-package.relationships+xml\"/>"
            "<Default Extension=\"xml\" ContentType=\"application/xml\"/>"
            "<Override PartName=\"/word/document.xml\" ContentType=\"application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml\"/>"
            "</Types>"
        )
        rels_xml = (
            "<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?>"
            "<Relationships xmlns=\"http://schemas.openxmlformats.org/package/2006/relationships\">"
            "<Relationship Id=\"rId1\" Type=\"http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument\" "
            "Target=\"word/document.xml\"/>"
            "</Relationships>"
        )
        with zipfile.ZipFile(output_path, "w") as zf:
            zf.writestr("[Content_Types].xml", content_types)
            zf.writestr("_rels/.rels", rels_xml)
            zf.writestr("word/document.xml", document_xml)