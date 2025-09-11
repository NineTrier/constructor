"""Smoke test for the document renderer.

This test verifies that the ``DocumentRenderer`` can be invoked to
produce a ``FileResponse`` using a simple stubbed template and data.
It does not require a working DOCX generator; instead, it monkeypatches
``generate_docx_file`` to return a dummy file. The test depends on
Django's test database to stub out the ORM interactions.
"""

import pytest

from apps.documents.domain.document_template import DocumentTemplate, TemplateStructure
from apps.documents.services.document_renderer import DocumentRenderer


@pytest.mark.django_db
def test_renderer_smoke(monkeypatch):
    # Prepare a trivial template structure with one placeholder
    raw = {"blocks": [{"type": "paragraph", "text": "Hello, {: user.name :}!"}]}

    # Stub the ORM model with just name and JSON fields
    class StubModel:
        id = 1
        name = "Dummy"
        json = raw

    def fake_load(pattern_id: int) -> DocumentTemplate:
        return DocumentTemplate(model=StubModel, structure=TemplateStructure(raw_json=raw))

    monkeypatch.setattr(DocumentTemplate, "load", staticmethod(fake_load))

    # Monkeypatch generate_docx_file to avoid docx generation
    def fake_generate_docx_file(self, template, selected_ids):
        from pathlib import Path
        p = Path("test.docx")
        p.write_bytes(b"dummy")
        return p

    monkeypatch.setattr(DocumentRenderer, "generate_docx_file", fake_generate_docx_file)

    renderer = DocumentRenderer()
    # Provide a single selected id mapping
    resp = renderer.as_file_response(DocumentTemplate.load(1), {"user": "42"})
    assert hasattr(resp, "streaming_content")
