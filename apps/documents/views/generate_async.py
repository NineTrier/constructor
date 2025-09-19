"""
Asynchronous document generation view.

This view accepts an HTML template and an optional JSON context, replaces
placeholders in the HTML using values from the context, converts the resulting
HTML into a DOCX using Pandoc, and returns a job identifier to the client.

The rendering happens in a background thread so that the HTTP request can
return immediately.  Progress can be polled via ``PollRenderJobView`` and the
resulting file downloaded via ``DownloadRenderJobView`` once the job has
completed.
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
import subprocess
from typing import Any, Dict
import zipfile
import xml.sax.saxutils as saxutils
import re
import html as html_module

from django.http import JsonResponse, HttpRequest, HttpResponseBadRequest
from django.views import View

from ..models.progress import RenderJob
from ..services.placeholder_utils import extract_placeholders, replace_placeholders


class GenerateDocumentAsyncView(View):
    """Handle POST requests to start an asynchronous document rendering job."""

    def post(self, request: HttpRequest, pattern_id: int = None) -> JsonResponse:
        # The raw HTML template is expected under the 'html' key of the POST
        # body.  It can be sent as form data or JSON encoded.
        html = request.POST.get("html") or (request.body.decode() if request.body else None)
        if not html:
            return HttpResponseBadRequest("Missing HTML content")

        # Optional context mapping placeholder expressions to replacement values.
        context_json = request.POST.get("context") or "{}"
        try:
            context: Dict[str, Any] = json.loads(context_json)
        except json.JSONDecodeError:
            return HttpResponseBadRequest("Invalid JSON for context")

        # Create the job record with pending status.
        job = RenderJob.objects.create(status=RenderJob.STATUS_PENDING)

        # Launch a background thread to perform the rendering.
        thread = threading.Thread(
            target=self._render_job, args=(job.id, html, context), daemon=True
        )
        thread.start()

        # Return the job identifier to the client.
        return JsonResponse({"job_id": job.id})

    def _render_job(self, job_id: int, html: str, context: Dict[str, Any]) -> None:
        """Worker function to render the document and update the job status."""
        # Fetch the job and update status to running.
        job = RenderJob.objects.get(id=job_id)
        job.status = RenderJob.STATUS_RUNNING
        job.save(update_fields=["status"])

        # Count placeholders in the original HTML.
        placeholders = extract_placeholders(html)
        job.placeholders_total = len(placeholders)

        # Replace placeholders using the provided context.
        replaced_html = replace_placeholders(html, context)
        job.placeholders_replaced = sum(1 for ph in placeholders if ph in context)
        job.save(update_fields=["placeholders_total", "placeholders_replaced"])

        try:
            # Write the rendered HTML to a temporary file.
            with tempfile.NamedTemporaryFile("w", suffix=".html", delete=False) as tmp_html:
                tmp_html.write(replaced_html)
                tmp_html_path = tmp_html.name

            # Define output directory and file name.
            out_dir = tempfile.mkdtemp()
            output_filename = f"document_{job_id}.docx"
            output_path = os.path.join(out_dir, output_filename)


            # Try to use pandoc to convert HTML to DOCX.  If pandoc is not
            # available (e.g., FileNotFoundError), fall back to a manual
            # conversion routine that creates a minimal DOCX with plain text.
            try:
                result = subprocess.run(
                    ["pandoc", tmp_html_path, "-o", output_path],
                    capture_output=True,
                    text=True,
                )
                if result.returncode != 0:
                    raise RuntimeError(result.stderr.strip() or "Pandoc conversion failed")
            except (FileNotFoundError, RuntimeError):
                # Fallback: manually build a simple DOCX package containing the
                # replaced HTML as plain paragraphs.  This does not attempt to
                # preserve styles but ensures Word can open the file.
                self._manual_html_to_docx(replaced_html, output_path)

            # Update job with completed status and output details.
            job.output_path = output_path
            job.output_name = output_filename
            job.status = RenderJob.STATUS_COMPLETED
            job.error_message = ""
            job.save(update_fields=["output_path", "output_name", "status", "error_message"])
        except Exception as exc:
            # Mark the job as failed and record the error message.
            job.status = RenderJob.STATUS_FAILED
            job.error_message = str(exc)
            job.save(update_fields=["status", "error_message"])
        finally:
            # Clean up the temporary HTML file.
            try:
                os.remove(tmp_html_path)
            except Exception:
                pass

    def _manual_html_to_docx(self, html_str: str, output_path: str) -> None:
        """
        Fallback method to convert an HTML string to a minimal DOCX file.

        This method extracts plain text from paragraph-level elements and
        constructs a basic WordprocessingML document.  It does not preserve
        formatting but ensures that the resulting file can be opened in Word.

        Args:
            html_str: The HTML content to convert.
            output_path: The destination path for the DOCX file.
        """
        # Extract plain text from <p> and <div> elements without relying on
        # BeautifulSoup.  Use a regular expression to locate paragraph tags
        # and strip any nested HTML tags.
        paragraphs = []
        for match in re.findall(r"<(?:p|div)[^>]*>(.*?)</(?:p|div)>", html_str or "", re.DOTALL | re.IGNORECASE):
            # Remove any nested tags within the paragraph/div
            text_with_tags = re.sub(r"<[^>]+>", "", match)
            text = html_module.unescape(text_with_tags).strip()
            if text:
                paragraphs.append(text)

        # Construct the document XML for Word.
        body_elements = []
        for para in paragraphs:
            escaped = saxutils.escape(para)
            body_elements.append(
                f"<w:p><w:r><w:t xml:space=\"preserve\">{escaped}</w:t></w:r></w:p>"
            )
        # Always include a section properties element to satisfy Word.
        body_elements.append(
            "<w:sectPr><w:pgSz w:w=\"12240\" w:h=\"15840\"/><w:pgMar w:top=\"1440\" w:right=\"1440\" "
            "w:bottom=\"1440\" w:left=\"1440\" w:header=\"720\" w:footer=\"720\" w:gutter=\"0\"/></w:sectPr>"
        )
        document_xml = (
            "<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?>"
            "<w:document xmlns:w=\"http://schemas.openxmlformats.org/wordprocessingml/2006/main\">"
            "<w:body>"
            + "".join(body_elements)
            + "</w:body></w:document>"
        )

        # Define other required parts.
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
            "<Relationship Id=\"rId1\" "
            "Type=\"http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument\" "
            "Target=\"word/document.xml\"/>"
            "</Relationships>"
        )

        # Write the ZIP structure for the DOCX file.
        with zipfile.ZipFile(output_path, "w") as zf:
            zf.writestr("[Content_Types].xml", content_types)
            zf.writestr("_rels/.rels", rels_xml)
            zf.writestr("word/document.xml", document_xml)