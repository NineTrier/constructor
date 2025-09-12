"""URL configuration for the documents application.

This module declares the URL patterns that map to the document
generation and validation views.  It should be included in the
project's main ``urlpatterns`` to expose these endpoints.
"""

from __future__ import annotations

from django.urls import path

from .views.generate import GenerateDocumentView
from .views.generate_async import GenerateDocumentAsyncView
from .views.jobs import PollRenderJobView, DownloadRenderJobView
from .views.versions import (
    SnapshotVersionView,
    PublishVersionView,
    RollbackVersionView,
)
from .views.validate import ValidateTemplateView


urlpatterns = [
    # Endpoint to generate a DOCX document from a template and selected records
    path(
        "documents/<int:pattern_id>/generate/",
        GenerateDocumentView.as_view(),
        name="documents.generate",
    ),
    # Async generate endpoint that returns a job id for polling
    path(
        "documents/<int:pattern_id>/generate/async/",
        GenerateDocumentAsyncView.as_view(),
        name="documents.generate_async",
    ),
    # Poll a render job for status and events
    path(
        "documents/jobs/<int:job_id>/poll/",
        PollRenderJobView.as_view(),
        name="documents.jobs.poll",
    ),
    # Versioning endpoints
    path(
        "documents/<int:pattern_id>/versions/snapshot/",
        SnapshotVersionView.as_view(),
        name="documents.versions.snapshot",
    ),
    path(
        "documents/<int:pattern_id>/versions/publish/",
        PublishVersionView.as_view(),
        name="documents.versions.publish",
    ),
    path(
        "documents/<int:pattern_id>/versions/rollback/",
        RollbackVersionView.as_view(),
        name="documents.versions.rollback",
    ),
    # Validate placeholders within a template
    path(
        "documents/<int:pattern_id>/validate/",
        ValidateTemplateView.as_view(),
        name="documents.validate",
    ),
    path("documents/jobs/<uuid:job_id>/download/", 
         DownloadRenderJobView.as_view(), 
         name="job_download"),
]