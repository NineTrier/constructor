from __future__ import annotations
from dataclasses import asdict
from django.http import JsonResponse, HttpRequest
from django.views import View

from apps.documents.domain.document_template import DocumentTemplate
from apps.documents.services.repositories import ObjectRepository

class GetSourcesView(View):
    """Return connected Objects and their Parameters for a template."""
    repo = ObjectRepository()

    def get(self, request: HttpRequest, pattern_id: int):
        tpl = DocumentTemplate.load(pattern_id)
        result = {"objects": []}
        for o in tpl.connected_objects:
            dto = self.repo.get(o["id"])  # raises if object missing -> 500 is OK for now
            result["objects"].append({
                "id": dto.id,
                "name": dto.name,
                "parameters": [asdict(p) for p in dto.parameters],
            })
        return JsonResponse(result, json_dumps_params={"ensure_ascii": False})
