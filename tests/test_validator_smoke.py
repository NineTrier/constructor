import pytest

from apps.documents.domain.document_template import DocumentTemplate, TemplateStructure
from apps.documents.domain.placeholders import Placeholder


def test_validate_template_reports_unknowns(monkeypatch):
    """Validator should report unknown object, field, and filter issues."""
    # Import inside test to allow monkeypatching of ParameterModel
    from apps.documents.services.validator import validate_template, ParameterModel

    # Create a dummy template with two placeholders:
    # 1) Unknown object 'Unknown'
    # 2) Known object 'User' but unknown field and unknown filter
    json_data = {
        "blocks": [
            {
                "type": "paragraph",
                "text": "Hello {: Unknown.name :} and {: User.nonexistent | nonfilter :}"
            }
        ]
    }

    # Create a dummy DocumentTemplate. We'll monkeypatch iter_placeholders
    # and connected_objects rather than parsing JSON for simplicity.
    class DummyModel:
        id = 1
        name = "Dummy"

    template = DocumentTemplate(model=DummyModel(), structure=TemplateStructure(json_data))

    # Monkeypatch connected_objects to return only 'User' with id 1
    monkeypatch.setattr(DocumentTemplate, "connected_objects", property(lambda self: [{"id": 1, "name": "User"}]))

    # Build placeholder objects manually using the real Placeholder class
    ph_unknown_obj = Placeholder(
        raw="{: Unknown.name :}",
        object_key="Unknown",
        field_key="name",
        filters=[],
        start=0,
        end=0,
    )
    ph_unknown_field_filter = Placeholder(
        raw="{: User.nonexistent | nonfilter :}",
        object_key="User",
        field_key="nonexistent",
        filters=[("nonfilter", [])],
        start=0,
        end=0,
    )
    # Monkeypatch iter_placeholders to yield our placeholders
    monkeypatch.setattr(DocumentTemplate, "iter_placeholders", lambda self: [([], ph_unknown_obj), ([], ph_unknown_field_filter)])

    # Monkeypatch ParameterModel.objects.filter so that fields only exist for 'User.id'
    class DummyQueryset:
        def __init__(self, exists: bool):
            self._exists = exists
        def exists(self):
            return self._exists
    class DummyManager:
        def filter(self, **kwargs):
            # kwargs can have name or id, and object_id
            # Only field 'id' exists on object 1
            if kwargs.get("object_id") == 1 and kwargs.get("name") == "id":
                return DummyQueryset(True)
            if kwargs.get("object_id") == 1 and kwargs.get("id") == 1:
                return DummyQueryset(True)
            return DummyQueryset(False)
        def get(self, **kwargs):  # pragma: no cover
            raise AttributeError
    monkeypatch.setattr(ParameterModel, "objects", DummyManager())

    # Run validation
    issues = validate_template(template)
    # Extract issue codes
    codes = {issue["code"] for issue in issues}
    assert "unknown_object" in codes
    assert "unknown_field" in codes
    assert "unknown_filter" in codes