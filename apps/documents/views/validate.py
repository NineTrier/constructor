"""
Template validation view.

This view inspects a saved document template and returns the list of
placeholder expressions it contains.  It deserializes the raw JSON
structure stored in the ``DocumentsPattern.json`` field, extracts plain
text paragraphs and uses the same placeholder detection logic as the
document generator.

Clients can call ``/documents/<pattern_id>/validate/`` to receive a
JSON payload with the placeholders found in the template.  A 400
response is returned if the pattern id does not exist.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List

from django.http import HttpRequest, HttpResponseBadRequest, JsonResponse
from django.views import View

try:
    # Attempt to import the DocumentsPattern model from the local app.
    from document.models import DocumentsPattern  # type: ignore
except Exception:
    DocumentsPattern = None  # type: ignore

from ..services.json_template import parse_document_json
from ..services.placeholder_utils import extract_placeholders


class ValidateTemplateView(View):
    """Return a list of placeholders present in a saved template."""

    def get(self, request: HttpRequest, pattern_id: int = None) -> JsonResponse:
        if pattern_id is None:
            return HttpResponseBadRequest("Missing pattern_id")
        if DocumentsPattern is None:
            return HttpResponseBadRequest("DocumentsPattern model is not available")
        try:
            pattern = DocumentsPattern.objects.get(pk=pattern_id)
        except Exception:
            return HttpResponseBadRequest("Invalid pattern_id")
        raw_json: Dict[str, Any] = pattern.json or {}
        paragraphs: List[str] = parse_document_json(raw_json)
        placeholders: List[str] = []
        for para in paragraphs:
            placeholders.extend(extract_placeholders(para))
        # Return unique placeholders preserving order.
        seen = set()
        uniq: List[str] = []
        for ph in placeholders:
            if ph not in seen:
                seen.add(ph)
                uniq.append(ph)
        return JsonResponse({"placeholders": uniq})