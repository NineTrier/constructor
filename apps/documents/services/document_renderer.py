"""Renderer for generating DOCX documents from templates.

This service takes a ``DocumentTemplate`` and a mapping of selected
record identifiers, resolves all placeholders within the template
structure using data from connected objects, and produces a `.docx`
file via the existing ``constructor.word_api.Document`` API.
"""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Dict, Optional

from django.http import FileResponse
from django.utils.encoding import iri_to_uri

from constructor.word_api import Document  # Provided by existing project
from constructor.models import Object as ObjectModel

from apps.documents.domain.document_template import DocumentTemplate, TemplateStructure
from apps.documents.domain.placeholders import replace_placeholders
from .data_object import DataObject

__all__ = [
    "DocumentRenderer",
]


class DocumentRenderer:
    """Service responsible for rendering templates into DOCX files."""

    def _build_resolver(self, template: DocumentTemplate, selected_ids: Dict[str, str]):
        """Create a resolver for placeholder substitution.

        The resolver uses the mapping ``selected_ids`` to look up the
        appropriate record in each connected data object. It supports
        both object names and numeric identifiers as keys, falling back
        to the first connected object when a placeholder lacks an
        explicit object prefix.
        """
        cache_by_id: dict[int, DataObject] = {}
        cache_by_name: dict[str, DataObject] = {}

        # Prepare DataObject instances for all connected objects
        for obj in template.connected_objects:
            try:
                orm_obj = ObjectModel.objects.get(id=obj["id"])
            except ObjectModel.DoesNotExist:
                continue
            data_obj = DataObject(orm=orm_obj)
            cache_by_id[obj["id"]] = data_obj
            cache_by_name[obj["name"]] = data_obj

        def resolver(object_key: Optional[str], field_key: str) -> str:
            data_obj: Optional[DataObject] = None
            record_id: Optional[str] = None

            # Look up DataObject by object name or numeric ID
            if object_key:
                data_obj = cache_by_name.get(object_key)
                record_id = selected_ids.get(object_key)
                if data_obj is None and object_key.isdigit():
                    data_obj = cache_by_id.get(int(object_key))
                    record_id = record_id or selected_ids.get(object_key)

            # Fallback: use the first object if none specified
            if data_obj is None and cache_by_id:
                data_obj = next(iter(cache_by_id.values()))
                if len(selected_ids) == 1:
                    record_id = next(iter(selected_ids.values()))

            if data_obj is None or not record_id:
                return ""

            record = data_obj.get_record(record_id)
            return str(record.get(field_key, ""))

        return resolver

    def generate_docx_file(self, template: DocumentTemplate, selected_ids: Dict[str, str]) -> Path:
        """Generate a DOCX file from a template and selected IDs.

        The method creates a deep copy of the template's JSON, replaces
        all placeholders with data from the selected records, and then
        delegates to ``constructor.word_api.Document`` to build and
        save the `.docx` file. The resulting file is stored in a
        temporary location.
        """
        json_copy = deepcopy(template.to_json())
        ts = TemplateStructure(json_copy)

        resolver = self._build_resolver(template, selected_ids)
        for path, text in ts.iter_text_nodes():
            replaced = replace_placeholders(text, resolver)
            if replaced != text:
                ts.replace_text_at_path(path, replaced)

        doc = Document(path=None)
        doc.from_json(json_copy)
        # Save into a temporary file. ``mkstemp`` returns an open file
        # descriptor and a path. We only need the path here.
        import tempfile

        fd, tmp_path = tempfile.mkstemp(suffix=".docx")
        Path(tmp_path).unlink(missing_ok=True)  # remove so Document.save can create
        doc.save(tmp_path)
        return Path(tmp_path)

    def as_file_response(self, template: DocumentTemplate, selected_ids: Dict[str, str]) -> FileResponse:
        """Return a Django ``FileResponse`` for the rendered document."""
        doc_path = self.generate_docx_file(template, selected_ids)
        filename = f"{getattr(template.model, 'name', 'document')}.docx"
        response = FileResponse(
            open(doc_path, "rb"),
            content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        )
        response["Content-Disposition"] = f'attachment; filename="{iri_to_uri(filename)}"'
        return response
