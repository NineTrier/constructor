"""Domain object representing a document template.

The ``DocumentTemplate`` class encapsulates all logic related to a
document template, including loading from the database, iterating
placeholders, and saving changes. It provides a layer of abstraction
over the underlying ORM model to reduce coupling between business
logic and data access.

``TemplateStructure`` is a simple wrapper around the raw JSON
representation of a template. It exposes helper methods to iterate
through text nodes and mutate them. Keeping this separate from
``DocumentTemplate`` allows for flexibility in how template data is
represented in the future.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any, Dict, Iterable, List, Optional

from django.utils.functional import cached_property

from constructor.models import DocumentsPattern, DocumentPattern_Objects

from .placeholders import Placeholder, find_placeholders_in_text

__all__ = [
    "TemplateStructure",
    "DocumentTemplate",
]


@dataclass
class TemplateStructure:
    """A thin wrapper around the raw JSON of a template.

    The template is stored in the database as a JSON object. This
    wrapper exposes helper methods to iterate through its text nodes and
    modify values at arbitrary paths. Storing JSON in the raw form
    rather than immediately converting it to a specialized structure
    keeps the implementation simple and backwards compatible.
    """

    raw_json: Dict[str, Any]

    def iter_text_nodes(self) -> Iterable[tuple[List[str], str]]:
        """Yield the paths and values of all text nodes.

        A text node is any dictionary entry with the key ``"text"`` and
        a string value. Paths are lists of keys/indices representing
        navigation through the JSON structure.
        """
        def walk(path: List[str], node: Any):
            if isinstance(node, dict):
                for key, value in node.items():
                    if key == "text" and isinstance(value, str):
                        yield path + [key], value
                    else:
                        yield from walk(path + [key], value)
            elif isinstance(node, list):
                for index, value in enumerate(node):
                    yield from walk(path + [str(index)], value)

        yield from walk([], self.raw_json)

    def replace_text_at_path(self, path: List[str], new_value: str) -> None:
        """Replace the value of a text node at the specified path.

        Parameters
        ----------
        path: List[str]
            The sequence of keys/indices identifying the text node.
        new_value: str
            The new string value to assign.
        """
        ref = self.raw_json
        for p in path[:-1]:
            ref = ref[int(p)] if p.isdigit() else ref[p]
        last = path[-1]
        if last.isdigit():
            ref[int(last)] = new_value
        else:
            ref[last] = new_value


@dataclass
class DocumentTemplate:
    """Domain object representing a document template.

    The template encapsulates its associated ``DocumentsPattern``
    instance and a ``TemplateStructure`` representing its JSON body. Use
    the class methods to load templates from the database and iterate
    over placeholders.
    """

    model: DocumentsPattern
    structure: TemplateStructure

    @classmethod
    def load(cls, pattern_id: int) -> "DocumentTemplate":
        """Load a template from the database.

        Parameters
        ----------
        pattern_id: int
            The primary key of the associated ``DocumentsPattern``.

        Returns
        -------
        DocumentTemplate
            A domain object wrapping the model and its JSON data.
        """
        model = DocumentsPattern.objects.get(id=pattern_id)
        print(model.json)
        struct = TemplateStructure(raw_json=model.json or {})
        return cls(model=model, structure=struct)

    def save(self) -> None:
        """Persist changes to the underlying model.

        Only the ``json`` field of the ``DocumentsPattern`` model is
        updated. Additional fields such as timestamps should be handled
        externally if necessary.
        """
        self.model.json = self.structure.raw_json
        # update only the JSON field; calling ``save`` on the model
        # triggers the appropriate ORM-level hooks.
        self.model.save(update_fields=["json"])

    @cached_property
    def connected_objects(self) -> List[dict]:
        """Return a list of connected objects (data sources).

        The list contains dictionaries with the keys ``id`` and
        ``name`` for each ``Object`` related to this template via
        ``DocumentPattern_Objects``. The results are cached for the
        lifetime of the instance to avoid repeated database queries.
        """
        relations = (
            DocumentPattern_Objects.objects
            .filter(document=self.model)
            .select_related("object")
        )
        return [
            {"id": relation.object.id, "name": relation.object.name}
            for relation in relations
        ]

    def iter_placeholders(self) -> Iterable[tuple[List[str], Placeholder]]:
        """Iterate over all placeholders in the template.

        Yields pairs ``(path, Placeholder)`` where ``path`` is the
        navigation path to the text node in the JSON structure, and
        ``Placeholder`` is the parsed placeholder instance.
        """
        for path, text in self.structure.iter_text_nodes():
            for placeholder in find_placeholders_in_text(text):
                yield path, placeholder

    def to_json(self) -> Dict[str, Any]:
        """Return the raw JSON data for the template."""
        return self.structure.raw_json
