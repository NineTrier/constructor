"""
Synchronous document generation view.

This view performs the same placeholder replacement and Pandoc conversion as
``GenerateDocumentAsyncView``, but it returns the generated DOCX directly as
the HTTP response instead of creating a background job.  It can be used
where asynchronous processing is not required.
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from typing import Any, Dict

from django.http import FileResponse, HttpRequest, HttpResponseBadRequest
from django.utils.encoding import smart_str
from django.views import View

from ..services.placeholder_utils import extract_placeholders, replace_placeholders


class GenerateDocumentView(View):
    """Synchronously generate a DOCX document from an HTML template."""

    def post(self, request: HttpRequest, pattern_id: int = None) -> FileResponse:
        html = request.POST.get("html") or (request.body.decode() if request.body else None)
        if not html:
            return HttpResponseBadRequest("Missing HTML content")

        context_json = request.POST.get("context") or "{}"
        try:
            context: Dict[str, Any] = json.loads(context_json)
        except json.JSONDecodeError:
            return HttpResponseBadRequest("Invalid JSON for context")

        # Replace placeholders in the HTML.
        replaced_html = replace_placeholders(html, context)

        try:
            # Write to a temporary HTML file.
            with tempfile.NamedTemporaryFile("w", suffix=".html", delete=False) as tmp_html:
                tmp_html.write(replaced_html)
                tmp_html_path = tmp_html.name

            # Define output file path.
            out_dir = tempfile.mkdtemp()
            output_filename = "document.docx"
            output_path = os.path.join(out_dir, output_filename)

            # Convert to DOCX using pandoc.
            result = subprocess.run(
                ["pandoc", tmp_html_path, "-o", output_path],
                capture_output=True,
                text=True,
            )
            if result.returncode != 0:
                raise RuntimeError(result.stderr)

            # Return file as attachment.
            return FileResponse(
                open(output_path, "rb"),
                as_attachment=True,
                filename=smart_str(output_filename),
                content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            )
        except Exception as exc:
            return HttpResponseBadRequest(str(exc))
        finally:
            try:
                os.remove(tmp_html_path)
            except Exception:
                pass