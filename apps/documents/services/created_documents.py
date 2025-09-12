"""Service for storing generated documents history.

This module defines a service that records newly generated documents in
the ``CreatedDocument`` model. A data class encapsulates metadata
about each generated document, such as the pattern ID, optional user
identifier and the selected IDs used for generation. Saving to the
``CreatedDocument`` model is centralized here to isolate database
operations from the rest of the application logic.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional

from django.utils import timezone

from constructor.models import CreatedDocument, DocumentsPattern

__all__ = [
    "CreatedDocMeta",
    "CreatedDocumentsService",
]


@dataclass
class CreatedDocMeta:
    """Metadata describing a generated document for history purposes."""

    pattern_id: int
    generated_by: Optional[int] = None
    selected_ids: Optional[Dict[str, str]] = None


class CreatedDocumentsService:
    """Persist generated documents to the ``CreatedDocument`` model."""

    def save(self, file_path: Path, meta: CreatedDocMeta) -> CreatedDocument:
        pattern = DocumentsPattern.objects.get(id=meta.pattern_id)
        created = CreatedDocument(pattern=pattern)
        # Assign timestamp if model has created_at or similar
        if hasattr(created, "created_at"):
            created.created_at = timezone.now()

        # Save the file if there is a FileField attribute
        if hasattr(created, "file"):
            filename = f"{pattern.id}_{int(timezone.now().timestamp())}.docx"
            with open(file_path, "rb") as fh:
                created.file.save(filename, fh, save=False)

        # Store selected IDs if model defines a params or metadata field
        if hasattr(created, "params") and meta.selected_ids:
            try:
                created.params = meta.selected_ids
            except Exception:
                pass

        # Store author if present on model and provided
        if meta.generated_by and hasattr(created, "author_id"):
            created.author_id = meta.generated_by

        created.save()
        return created
