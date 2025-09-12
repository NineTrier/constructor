"""Views to manage template versions.

These views expose endpoints to snapshot, publish, and rollback
``DocumentsPattern`` templates.  They are thin wrappers around
``VersioningService`` and mostly handle request/response formatting.
"""

from __future__ import annotations

import json
from typing import Optional

from django.http import JsonResponse, HttpResponseBadRequest, Http404, HttpRequest
from django.views import View

from constructor.models import DocumentsPattern
from apps.documents.models.versioning import DocumentPatternVersion
from apps.documents.services.versioning import VersioningService


class SnapshotVersionView(View):
    """Create a new snapshot version for a template."""

    service = VersioningService()

    def post(self, request: HttpRequest, pattern_id: int):
        try:
            pattern = DocumentsPattern.objects.get(id=pattern_id)
        except DocumentsPattern.DoesNotExist:
            raise Http404("Template not found")
        label = None
        if request.body:
            try:
                payload = json.loads(request.body)
                label = payload.get("label")
            except json.JSONDecodeError:
                return HttpResponseBadRequest("Invalid JSON in request body")
        version = self.service.snapshot(pattern, label)
        return JsonResponse({"version_id": version.id, "version_number": version.version_number, "label": version.label})


class PublishVersionView(View):
    """Mark a version (or latest) as published for a template."""

    service = VersioningService()

    def post(self, request: HttpRequest, pattern_id: int):
        try:
            pattern = DocumentsPattern.objects.get(id=pattern_id)
        except DocumentsPattern.DoesNotExist:
            raise Http404("Template not found")
        version_id: Optional[int] = None
        if request.body:
            try:
                payload = json.loads(request.body)
                version_id = payload.get("version_id")
            except json.JSONDecodeError:
                return HttpResponseBadRequest("Invalid JSON in request body")
        version = None
        if version_id is not None:
            try:
                version = DocumentPatternVersion.objects.get(id=version_id, pattern=pattern)
            except DocumentPatternVersion.DoesNotExist:
                return HttpResponseBadRequest("Version not found for this template")
        version = self.service.publish(pattern, version)
        return JsonResponse({"version_id": version.id, "version_number": version.version_number, "label": version.label, "published": version.is_published})


class RollbackVersionView(View):
    """Rollback a template to a specific version's JSON."""

    service = VersioningService()

    def post(self, request: HttpRequest, pattern_id: int):
        try:
            pattern = DocumentsPattern.objects.get(id=pattern_id)
        except DocumentsPattern.DoesNotExist:
            raise Http404("Template not found")
        try:
            payload = json.loads(request.body or "{}")
            version_id = payload.get("version_id")
            if version_id is None:
                return HttpResponseBadRequest("version_id must be provided")
        except json.JSONDecodeError:
            return HttpResponseBadRequest("Invalid JSON in request body")
        try:
            version = DocumentPatternVersion.objects.get(id=version_id, pattern=pattern)
        except DocumentPatternVersion.DoesNotExist:
            return HttpResponseBadRequest("Version not found for this template")
        self.service.rollback(pattern, version)
        return JsonResponse({"rolled_back_to": version.version_number})
