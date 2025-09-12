"""
document_renderer.py
-----------------------

This module defines the ``DocumentRenderer``, a service class that
combines a document template with selected record identifiers to
produce a finished DOCX file.  It resolves placeholders within the
template using data from multiple objects, optionally traversing
multi‑step parent relations and applying filter pipelines.

The renderer is agnostic about how data is stored; it relies on
``DataObject`` to fetch row data and ``DataJoiner`` to determine
ancestor record identifiers when the placeholder references a parent
object without an explicit selection in ``selected_ids``.  Filters are
implemented via the ``apply_filter_chain`` helper.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from copy import deepcopy
from typing import Any, Dict, Optional, Callable

try:
    # Import Django components when available
    from django.http import FileResponse  # type: ignore
    from django.utils.encoding import iri_to_uri  # type: ignore
except Exception:
    FileResponse = None  # type: ignore
    iri_to_uri = None  # type: ignore

try:
    # Import the existing WordAPI class.  Adjust this import to match
    # your project if necessary.  Falling back to a dummy implementation
    # helps avoid import errors during static analysis or tests.
    from constructor.word_api import Document  # type: ignore
except Exception:
    class Document:  # type: ignore
        def __init__(self, path: Optional[str] = None) -> None:
            self.path = path
        def from_json(self, json_data: Dict[str, Any]) -> None:
            pass
        def save(self, out_path: str) -> None:
            Path(out_path).write_bytes(b"")

from ..domain.document_template import DocumentTemplate, TemplateStructure
from ..domain.placeholders import replace_placeholders
from ..domain.filters import apply_filter_chain
from ..services.data_object import DataObject
from ..services.repositories import ObjectRepository
from ..services.datajoiner import DataJoiner


class DocumentRenderer:
    """Generate DOCX files from templates and selected record identifiers.

    The renderer resolves placeholders in a template's JSON using
    values from ``DataObject`` instances.  If a placeholder refers to a
    parent object that does not have a selected record id in
    ``selected_ids``, the renderer will attempt to traverse parent
    relations to determine the appropriate record id via ``DataJoiner``.
    It also applies filter pipelines specified in placeholders.
    """

    def __init__(self, object_repo: Optional[ObjectRepository] = None) -> None:
        self.object_repo = object_repo or ObjectRepository()
        self.data_joiner = DataJoiner()

    def _preload_objects(self, template: DocumentTemplate) -> Dict[str, DataObject]:
        """Preload connected objects into a cache keyed by both id and name."""
        cache: Dict[str, DataObject] = {}
        for meta in template.connected_objects:
            try:
                from constructor.models import Object as ObjectModel  # type: ignore
                obj = ObjectModel.objects.get(id=meta["id"])  # type: ignore
                data_obj = DataObject(orm=obj)
                # Key by id (string) and by name
                cache[str(meta["id"])] = data_obj
                cache[meta["name"]] = data_obj
            except Exception:
                continue
        return cache

    def _build_resolver(self, template: DocumentTemplate, selected_ids: Dict[str, Any]) -> Callable[[Optional[str], str, list], str]:
        """Construct a resolver function for placeholder replacement."""
        object_cache = self._preload_objects(template)

        def resolver(obj_key: Optional[str], field_key: str, filters: list) -> str:
            # Determine the target DataObject for this placeholder
            target_data_obj: Optional[DataObject] = None
            target_obj_id: Optional[int] = None
            record_id: Optional[Any] = None

            # Identify target object by key (name or id)
            if obj_key:
                # direct lookup by name or id
                target_data_obj = object_cache.get(obj_key)
                if target_data_obj is None and obj_key.isdigit():
                    target_data_obj = object_cache.get(str(int(obj_key)))
                if target_data_obj is not None:
                    try:
                        target_obj_id = target_data_obj.orm.id  # type: ignore
                    except Exception:
                        pass
                    record_id = selected_ids.get(obj_key)
                    if record_id is None and obj_key.isdigit():
                        record_id = selected_ids.get(str(int(obj_key)))
            # Fallback if no object specified
            if target_data_obj is None:
                # Use first available data object and assume only one object
                if object_cache:
                    first_key = next(iter(object_cache.keys()))
                    target_data_obj = object_cache[first_key]
                    try:
                        target_obj_id = target_data_obj.orm.id  # type: ignore
                    except Exception:
                        pass
                    # If exactly one selected id is provided, use it
                    if len(selected_ids) == 1:
                        record_id = next(iter(selected_ids.values()))
            # If we still haven't identified the target object, give up
            if target_data_obj is None or target_obj_id is None:
                return ""
            # If a record_id is provided, use it; otherwise try to resolve via join
            resolved_record_id: Optional[Any] = None
            if record_id is not None:
                resolved_record_id = record_id
            else:
                # Attempt to derive the record id via joining from any selected object
                for sel_key, sel_record_id in selected_ids.items():
                    # Skip if this selection is the same as target
                    if sel_key == obj_key or sel_record_id is None:
                        continue
                    # Look up the DataObject for the selected source
                    sel_data_obj = object_cache.get(sel_key)
                    if sel_data_obj is None and sel_key.isdigit():
                        sel_data_obj = object_cache.get(str(int(sel_key)))
                    if sel_data_obj is None:
                        continue
                    parent_id = self.data_joiner.resolve_ancestor_record_id(
                        sel_data_obj, sel_record_id, target_obj_id
                    )
                    if parent_id is not None:
                        resolved_record_id = parent_id
                        break
            # If we still couldn't determine a record id, return empty
            if resolved_record_id is None:
                return ""
            # Fetch the record and apply filters
            try:
                values = target_data_obj.get_record(resolved_record_id)
            except Exception:
                return ""
            raw_value = values.get(field_key)
            if raw_value is None:
                return ""
            try:
                return apply_filter_chain(str(raw_value), filters)
            except Exception:
                return str(raw_value)
        return resolver

    def generate_docx_file(self, template: DocumentTemplate, selected_ids: Dict[str, Any]) -> Path:
        json_copy = deepcopy(template.to_json())
        structure = TemplateStructure(json_copy)
        resolver = self._build_resolver(template, selected_ids)

        for path, text in structure.iter_text_nodes():
            replaced = replace_placeholders(text, resolver)
            if replaced != text:
                structure.replace_text_at_path(path, replaced)

        doc = Document(path=None)
        doc.from_json(json_copy)

        # создаём временный файл и закрываем дескриптор
        fd, tmp_path = tempfile.mkstemp(suffix=".docx")
        os.close(fd)                  # закрываем дескриптор, а не Path
        doc.save(tmp_path)

        return Path(tmp_path)
    def as_file_response(self, template: DocumentTemplate, selected_ids: Dict[str, Any]):
        """Return a Django ``FileResponse`` containing the generated document."""
        if FileResponse is None or iri_to_uri is None:
            raise RuntimeError("Django environment is not available for FileResponse")
        doc_path = self.generate_docx_file(template, selected_ids)
        name = getattr(template.model, "name", "document")
        response = FileResponse(open(doc_path, "rb"), content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document")  # type: ignore
        response["Content-Disposition"] = f'attachment; filename="{iri_to_uri(name)}.docx"'  # type: ignore
        return response