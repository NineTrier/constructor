from __future__ import annotations
from copy import deepcopy
import tempfile
from pathlib import Path
from typing import Any, Dict, Optional

from django.http import FileResponse
from django.utils.encoding import iri_to_uri
from django.utils import timezone

# Existing Word API (unchanged)
from constructor.word_api import Document

from apps.documents.domain.document_template import DocumentTemplate, TemplateStructure
from apps.documents.domain.placeholders import replace_placeholders
from constructor.models import Object as ObjectModel, CreatedDocument
from .data_object import DataObject


class DocumentRenderer:
    """Builds a DOCX from a template and selected records without mutating the template."""

    def _build_resolver(self, template: DocumentTemplate, selected_ids: Dict[str, str]):
        cache_by_id: dict[int, DataObject] = {}
        cache_by_name: dict[str, DataObject] = {}

        for o in template.connected_objects:
            try:
                orm = ObjectModel.objects.get(id=o["id"])
            except ObjectModel.DoesNotExist:
                continue
            data_obj = DataObject(orm=orm)
            cache_by_id[o["id"]] = data_obj
            cache_by_name[o["name"]] = data_obj

        def resolver(object_key: Optional[str], field_key: str) -> str:
            data_obj: Optional[DataObject] = None
            record_id: Optional[str] = None

            if object_key:
                data_obj = cache_by_name.get(object_key)
                record_id = selected_ids.get(object_key)
                if data_obj is None and object_key.isdigit():
                    data_obj = cache_by_id.get(int(object_key))
                    record_id = record_id or selected_ids.get(object_key)

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
        json_copy = deepcopy(template.to_json())
        ts = TemplateStructure(json_copy)

        resolver = self._build_resolver(template, selected_ids)
        for path, text in ts.iter_text_nodes():
            replaced = replace_placeholders(text, resolver)
            if replaced != text:
                ts.replace_text_at_path(path, replaced)

        doc = Document(path=None)
        doc.from_json(json_copy)
        tmp = Path(tempfile.mkstemp(suffix=".docx")[1])
        doc.save(str(tmp))
        return tmp

    def persist_generation(self, template: DocumentTemplate, file_path: Path, meta: dict | None = None) -> CreatedDocument:
        """Persist generated file metadata to CreatedDocument (history)."""
        cd = CreatedDocument(
            pattern=template.model,
            created_at=timezone.now() if hasattr(timezone, "now") else None,
        )
        # Try to save file into the FileField if present; otherwise, keep path if model supports it.
        if hasattr(cd, "file") and callable(getattr(cd.file, "save", None)):
            with open(file_path, "rb") as f:
                cd.file.save(file_path.name, f, save=False)
        elif hasattr(cd, "path") :
            cd.path = str(file_path)
        if meta and hasattr(cd, "meta") :
            import json as _json
            cd.meta = _json.dumps(meta, ensure_ascii=False)
        cd.save()
        return cd

    def as_file_response(self, template: DocumentTemplate, selected_ids: Dict[str, str]) -> FileResponse:
        path = self.generate_docx_file(template, selected_ids)
        # best-effort history write; any error should not block file delivery
        try:
            self.persist_generation(template, path, meta={"selected_ids": selected_ids})
        except Exception:
            pass
        filename = f"{getattr(template.model, 'name', 'document')}.docx"
        resp = FileResponse(open(path, "rb"),
                            content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document")
        resp["Content-Disposition"] = f'attachment; filename="{iri_to_uri(filename)}"'
        return resp
