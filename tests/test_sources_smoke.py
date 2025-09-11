import pytest
from django.urls import reverse

@pytest.mark.django_db
def test_sources_view_smoke(client, monkeypatch):
    from apps.documents.domain.document_template import DocumentTemplate, TemplateStructure

    class DummyModel: pass

    def fake_load(pattern_id: int):
        dm = DummyModel()
        dm.id = pattern_id
        dm.name = "Demo"
        return DocumentTemplate(model=dm, structure=TemplateStructure(raw_json={"blocks": []}))

    monkeypatch.setattr(DocumentTemplate, "load", staticmethod(fake_load))

    from apps.documents.services.repositories import ObjectRepository, ObjectDTO, ParameterDTO
    def fake_get(self, oid):
        return ObjectDTO(id=oid, name=f"Obj{oid}", parameters=[
            ParameterDTO(1, "id", "TXT", True), ParameterDTO(2, "name", "TXT", False)
        ])
    monkeypatch.setattr(ObjectRepository, "get", fake_get)

    def fake_connected(self):
        return [{"id": 1, "name": "Org"}]
    monkeypatch.setattr(DocumentTemplate, "connected_objects", property(fake_connected))

    url = reverse("documents.sources", kwargs={"pattern_id": 42})
    resp = client.get(url)
    assert resp.status_code == 200
    payload = resp.json()
    assert "objects" in payload and len(payload["objects"]) == 1
    assert payload["objects"][0]["name"] == "Obj1"
