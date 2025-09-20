"""
Version management views (stubs).

The original application provided endpoints to snapshot, publish and
rollback versions of document templates.  In this simplified
implementation we do not maintain multiple versions, but we provide
placeholder views so that URL configuration referencing these
endpoints does not result in import errors.  Each view simply
responds with a JSON object indicating that the operation is not
implemented.
"""

from __future__ import annotations

from django.http import HttpRequest, JsonResponse
from django.views import View


class SnapshotVersionView(View):
    """Return a JSON response indicating snapshotting is not implemented."""

    def post(self, request: HttpRequest, pattern_id: int = None) -> JsonResponse:
        return JsonResponse({"status": "not implemented"})


class PublishVersionView(View):
    """Return a JSON response indicating publishing is not implemented."""

    def post(self, request: HttpRequest, pattern_id: int = None) -> JsonResponse:
        return JsonResponse({"status": "not implemented"})


class RollbackVersionView(View):
    """Return a JSON response indicating rollback is not implemented."""

    def post(self, request: HttpRequest, pattern_id: int = None) -> JsonResponse:
        return JsonResponse({"status": "not implemented"})