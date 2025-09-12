"""View to synchronously generate a DOCX document.

This view accepts a POST request with a JSON body specifying the
selected record identifiers for each connected data source. It uses
``DocumentRenderer`` to produce a DOCX file from the template and
returns it as a file response.  This is a blocking operation and is
appropriate for small documents or environments where async processing
is unnecessary.
"""

from __future__ import annotations

import json
from typing import Dict, Any

from django.http import HttpRequest, HttpResponseBadRequest
from django.views import View

from apps.documents.domain.document_template import DocumentTemplate
from apps.documents.services.document_renderer import DocumentRenderer


class GenerateDocumentView(View):
    """Handle POST requests to generate a document synchronously."""

    renderer = DocumentRenderer()

    def post(self, request: HttpRequest, pattern_id: int):
        try:
            payload = json.loads(request.body or "{}")
            selected_ids = payload.get("selected_ids", {}) or {}
            if not isinstance(selected_ids, dict):
                return HttpResponseBadRequest("selected_ids must be an object")
        except json.JSONDecodeError:
            return HttpResponseBadRequest("Invalid JSON in request body")

        template = DocumentTemplate.load(pattern_id)
        return self.renderer.as_file_response(template, selected_ids)
