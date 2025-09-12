"""API endpoint to validate document templates.

This view exposes a JSON endpoint that runs the placeholder
validation on a template and returns any issues found.  It is useful
for template editors to check their work before attempting to
generate documents.

Example request::

    GET /documents/123/validate/

The response is a JSON object with a single key ``issues`` that
contains a list of issue dictionaries. Each issue has the fields
``code``, ``message``, ``path`` (JSON path as a list of keys) and
``placeholder`` (the raw placeholder text).
"""

from __future__ import annotations

import json
from django.http import JsonResponse, HttpRequest, HttpResponseBadRequest
from django.views import View

from ..domain.document_template import DocumentTemplate
from ..services.validator import validate_template


class ValidateTemplateView(View):
    """Handle GET requests to validate a document template."""

    def get(self, request: HttpRequest, pattern_id: int):
        try:
            # Load the template by id. If not found, raise a 404 via the ORM.
            template = DocumentTemplate.load(pattern_id)
        except Exception:
            return HttpResponseBadRequest("Invalid template id")
        issues = validate_template(template)
        return JsonResponse({"issues": issues}, json_dumps_params={"ensure_ascii": False})