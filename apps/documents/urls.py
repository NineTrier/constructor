"""URL patterns for the documents app."""

from django.urls import path
from .views.generate import GenerateDocumentView

__all__ = [
    "urlpatterns",
]

urlpatterns = [
    path(
        "documents/<int:pattern_id>/generate/",
        GenerateDocumentView.as_view(),
        name="documents.generate",
    ),
]
