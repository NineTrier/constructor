"""
relations.py
-----------------

This module defines simple data structures and a repository for representing
relationships between document data sources.  A *relation* connects a
child ``Object`` to its parent ``Object`` along with a mapping of
parameter identifiers indicating how rows in the child reference rows in
the parent.  This information is used by the ``DataJoiner`` to perform
multi‑step joins across a graph of data sources when resolving
placeholders that refer to fields of ancestor objects.

The repository attempts to import appropriate models from
``constructor.models``.  It is written defensively: if a particular model
cannot be imported or does not provide the expected fields, the
corresponding relations simply will not be discovered.  This keeps the
system resilient to changes in model names and schemas.

Note that the names ``Object_ParentObject`` and
``ObjectLink_identificators`` are drawn from the original codebase.  If
your project uses different model names, you should modify the
``RelationRepository`` accordingly.  The rest of the code relies only on
the high‑level contract of ``RelationDTO``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional


@dataclass
class RelationDTO:
    """A simple data transfer object describing a one‑step relation.

    Attributes
    ----------
    child_object_id: int
        The ID of the child ``Object`` (where the reference value lives).
    parent_object_id: int
        The ID of the parent ``Object`` that is referenced.
    links: Dict[int, int]
        A mapping from child parameter identifiers to parent parameter
        identifiers.  Each key/value pair denotes that the value in the
        child's parameter column (with id equal to the key) holds the
        identifier of the record in the parent object that should be
        joined on the parent parameter id (the value).  If ``links`` is
        empty, the join falls back to the child's primary key column.
    """

    child_object_id: int
    parent_object_id: int
    links: Dict[int, int]


class RelationRepository:
    """Discover relations between objects by inspecting Django models.

    This repository lazily attempts to import models defining parent/
    child relationships and parameter link mappings.  It exposes a single
    method, :py:meth:`list_relations`, which returns a list of
    ``RelationDTO`` instances.  If no relevant models can be imported,
    the returned list will be empty.
    """

    def __init__(self) -> None:
        # These attributes will be set during import.  They may remain
        # ``None`` if the corresponding models are not found.
        self.rel_model = None
        self.link_model = None

        # Try to import the relationship and link models.  We wrap each
        # import in a try/except so that failure to import one does not
        # prevent discovery of others.  Feel free to extend this logic
        # with your own model names if your project differs.
        try:
            from constructor.models import Object_ParentObject as RelModel  # type: ignore
            self.rel_model = RelModel
        except Exception:
            # No relation model found; leave as None
            pass
        try:
            # In the original code base this model was spelled
            # ``ObjectLink_identificators``.  Some projects may use a
            # slightly different name (e.g. ``ObjectLink_identifiers``).
            from constructor.models import ObjectLink_identificators as LinkModel  # type: ignore
            self.link_model = LinkModel
        except Exception:
            try:
                from constructor.models import ObjectLink_identifiers as LinkModel  # type: ignore
                self.link_model = LinkModel
            except Exception:
                # If neither import succeeds, linking information will be
                # unavailable.
                pass

    def list_relations(self) -> List[RelationDTO]:
        """Return all discovered relations between objects.

        For each entry in the relation model (if present), this method
        constructs a ``RelationDTO`` capturing the child and parent
        object IDs and any parameter link mappings defined in the link
        model.  If no relation model is available, an empty list is
        returned.  Likewise, if no link model is available, the
        ``links`` dictionaries on the returned DTOs will be empty.
        """
        relations: List[RelationDTO] = []
        RelModel = self.rel_model
        if RelModel is None:
            return relations

        # Fetch all relation entries.  We avoid eager loading of
        # related objects to keep this logic generic; attribute access
        # will attempt to find either ``child_object`` or
        # ``child_object_id`` on each entry.
        try:
            all_rels = list(RelModel.objects.all())  # type: ignore
        except Exception:
            # If the ORM query fails, return an empty list.
            return relations

        for rel in all_rels:
            try:
                # Extract child and parent object IDs.  Different
                # projects may name these fields differently, so we try
                # several variations.  The fallback to ``None`` will be
                # filtered out below.
                child_id = getattr(rel, "child_object_id", None)
                if child_id is None and hasattr(rel, "child_object"):
                    child_obj = getattr(rel, "child_object")
                    child_id = getattr(child_obj, "id", None)
                parent_id = getattr(rel, "parent_object_id", None)
                if parent_id is None and hasattr(rel, "parent_object"):
                    parent_obj = getattr(rel, "parent_object")
                    parent_id = getattr(parent_obj, "id", None)

                # Skip if we cannot determine either ID.
                if child_id is None or parent_id is None:
                    continue

                links: Dict[int, int] = {}
                LinkModel = self.link_model
                if LinkModel is not None:
                    # Fetch link entries matching this relation.
                    try:
                        # Try filtering by explicit object IDs.
                        link_qs = LinkModel.objects.filter(
                            child_object_id=child_id, parent_object_id=parent_id
                        )  # type: ignore
                    except Exception:
                        # Fall back to more generic filter names if
                        # available (e.g. ``child_object`` instead of
                        # ``child_object_id``).  If this also fails, no
                        # linking information will be available.
                        try:
                            link_qs = LinkModel.objects.filter(
                                child_object__id=child_id, parent_object__id=parent_id
                            )  # type: ignore
                        except Exception:
                            link_qs = []  # type: ignore
                    # Build the mapping from child parameter IDs to
                    # parent parameter IDs.  Again, try multiple field
                    # names for robustness.
                    for link in link_qs:
                        try:
                            cpid = getattr(link, "child_param_id", None)
                            if cpid is None:
                                cpid = getattr(link, "child_param", None)
                                if cpid is not None and hasattr(cpid, "id"):
                                    cpid = cpid.id
                            ppid = getattr(link, "parent_param_id", None)
                            if ppid is None:
                                ppid = getattr(link, "parent_param", None)
                                if ppid is not None and hasattr(ppid, "id"):
                                    ppid = ppid.id
                            if cpid is None or ppid is None:
                                continue
                            links[int(cpid)] = int(ppid)
                        except Exception:
                            continue

                relations.append(RelationDTO(
                    child_object_id=int(child_id),
                    parent_object_id=int(parent_id),
                    links=links,
                ))
            except Exception:
                # Silently ignore malformed relation entries
                continue

        return relations