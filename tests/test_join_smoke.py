"""
Smoke tests for multi‑step joins in DataJoiner.

These tests verify that the joiner is able to locate a path across
multiple relations and that the path length matches expectations.  We
avoid loading any real database models by monkeypatching
``RelationRepository`` to return fake relation data.
"""

import pytest

from apps.documents.services.datajoiner import DataJoiner, JoinStep
from apps.documents.services.relations import RelationDTO, RelationRepository


@pytest.mark.django_db
def test_find_multihop_path(monkeypatch):
    """Ensure that a multi‑hop path is discovered correctly."""
    # Fake relations: A (1) -> B (2) -> C (3)
    fake_relations = [
        RelationDTO(child_object_id=1, parent_object_id=2, links={}),
        RelationDTO(child_object_id=2, parent_object_id=3, links={}),
    ]
    # Monkeypatch list_relations to return our fake data
    def fake_list(self):
        return fake_relations
    monkeypatch.setattr(RelationRepository, "list_relations", fake_list, raising=False)
    joiner = DataJoiner(RelationRepository())
    path = joiner._find_path(1, 3)
    assert path is not None, "Joiner did not find a path from 1 to 3"
    assert len(path) == 2, f"Expected two‑step path, got {len(path)}"