"""HTTP view for generating documents on-demand."""

from __future__ import annotations

import json

from django.http import HttpRequest, HttpResponseBadRequest
from django.views import View

from apps.documents.domain.document_template import DocumentTemplate
from apps.documents.services.document_renderer import DocumentRenderer

__all__ = [
    "GenerateDocumentView",
]


class GenerateDocumentView(View):
    """Handle POST requests to render and download a document.

    The expected payload is a JSON object containing a `selected_ids`
    dictionary mapping object identifiers (names or numeric IDs) to
    record IDs. See ``DocumentRenderer`` for details on how these IDs
    are used.
    """

    renderer = DocumentRenderer()

    def post(self, request: HttpRequest, pattern_id: int):
        try:
            payload = json.loads(request.body or "{}")
        except json.JSONDecodeError:
            return HttpResponseBadRequest("invalid json")
        selected_ids = payload.get("selected_ids", {}) or {}
        if not isinstance(selected_ids, dict):
            return HttpResponseBadRequest("selected_ids must be an object")
        template = DocumentTemplate.load(pattern_id)
        return self.renderer.as_file_response(template, selected_ids)
