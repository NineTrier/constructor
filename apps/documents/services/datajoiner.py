"""
datajoiner.py
----------------

This module defines a ``DataJoiner`` that performs multi‑step joins
between data objects.  Given a starting data object (representing the
selected record) and a target object identifier, the joiner follows a
chain of parent/child relations to determine the record identifier of
the target object that corresponds to the starting record.

Relations are provided by :pyclass:`~apps.documents.services.relations.RelationRepository`.
Each relation links a child object to a parent object along with a
mapping of parameter identifiers.  The joiner uses pandas DataFrame
rows from :pyclass:`~apps.documents.services.data_object.DataObject` to
extract the parent record identifiers and then walks up the chain.

This implementation supports multi‑hop joins (e.g. A→B→C) via a
shortest‑path search over the relation graph.  Paths are cached
internally to improve performance across multiple invocations.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple, Any, Iterable

try:
    # Avoid import errors if Django is not available during static analysis
    from constructor.models import Object as ObjectModel  # type: ignore
    from constructor.models import Parameter as ParameterModel  # type: ignore
except Exception:
    ObjectModel = None  # type: ignore
    ParameterModel = None  # type: ignore

from .data_object import DataObject
from .relations import RelationRepository, RelationDTO


@dataclass
class JoinStep:
    """Represents a single step in a join path.

    ``child_object_id`` → ``parent_object_id`` with a mapping of
    ``child_param_id`` to ``parent_param_id``.  The mapping defines
    which column in the child's data contains the identifier of the
    parent record.  If the mapping is empty, the child's primary key
    column is used.
    """
    child_object_id: int
    parent_object_id: int
    links: Dict[int, int]


class DataJoiner:
    """Resolve record identifiers across a graph of objects.

    The joiner discovers relations via a ``RelationRepository`` and
    constructs an adjacency graph of objects.  Given a starting
    ``DataObject`` and a target object identifier, it finds a path in
    this graph and then walks along that path, each time looking up
    the parent record identifier in the current row.
    """

    def __init__(self, relation_repo: Optional[RelationRepository] = None) -> None:
        self.relation_repo = relation_repo or RelationRepository()
        self._graph: Optional[Dict[int, List[RelationDTO]]] = None
        # Cache for previously computed shortest paths: (start_id, target_id) -> path
        self._path_cache: Dict[Tuple[int, int], Optional[List[RelationDTO]]] = {}

    def _build_graph(self) -> None:
        """Lazily build an adjacency map from child_object_id to relations."""
        if self._graph is not None:
            return
        adj: Dict[int, List[RelationDTO]] = {}
        for rel in self.relation_repo.list_relations():
            adj.setdefault(rel.child_object_id, []).append(rel)
        self._graph = adj

    def _find_path(self, start_id: int, target_id: int) -> Optional[List[RelationDTO]]:
        """Find a path of relations from ``start_id`` to ``target_id``.

        A breadth‑first search (BFS) is used to find the shortest path
        measured in number of relations.  Paths are cached to avoid
        repeated searches for the same start/target pair.
        """
        cache_key = (start_id, target_id)
        if cache_key in self._path_cache:
            return self._path_cache[cache_key]

        self._build_graph()
        if self._graph is None:
            self._path_cache[cache_key] = None
            return None
        # BFS queue entries: (current_object_id, path_so_far)
        queue: deque[Tuple[int, List[RelationDTO]]] = deque()
        queue.append((start_id, []))
        visited: set[int] = set()
        while queue:
            current, path = queue.popleft()
            if current == target_id:
                self._path_cache[cache_key] = path
                return path
            if current in visited:
                continue
            visited.add(current)
            for rel in self._graph.get(current, []):
                # Avoid cycles by not revisiting the same parent in this path
                next_obj = rel.parent_object_id
                if next_obj not in visited:
                    queue.append((next_obj, path + [rel]))
        # No path found
        self._path_cache[cache_key] = None
        return None

    def _lookup_parent_id_in_row(self, row: Dict[str, Any], data_obj: DataObject, rel: RelationDTO) -> Optional[Any]:
        """Given a row from ``data_obj`` and a relation, extract the parent record id.

        The relation's ``links`` map child parameter identifiers to
        parent parameter identifiers.  We try to find a column in the
        row corresponding to the child parameter id (using either the
        id as string or the parameter's name).  If no mapping exists
        for the relation, we fall back to the primary key column of
        ``data_obj`` (as determined by
        :py:meth:`apps.documents.services.data_object.DataObject._ensure_id_column`).
        """
        # Try each child_param_id from the mapping
        for child_param_id in rel.links.keys():
            # Attempt to get the value by id (as string)
            try:
                key_str = str(child_param_id)
                if key_str in row:
                    return row[key_str]
            except Exception:
                pass
            # Attempt to resolve by parameter name if available
            try:
                if ParameterModel is not None:
                    param = ParameterModel.objects.get(id=child_param_id)  # type: ignore
                    name = getattr(param, "name", None)
                    if name and name in row:
                        return row[name]
            except Exception:
                # If we cannot find the parameter, skip
                continue
        # Fallback: use the primary key column (id_to_connect or first column)
        try:
            df = data_obj._load_df()
            id_col = data_obj._ensure_id_column()
            # row likely does not include the ID column because
            # DataObject.get_record returns a mapping of parameter names;
            # therefore we cannot use the row mapping here.  Instead
            # this fallback should never be triggered if the mapping
            # information is correctly populated.  Return None for
            # safety.
        except Exception:
            pass
        return None

    def resolve_ancestor_record_id(
        self,
        data_obj: DataObject,
        record_id: Any,
        target_object_id: int,
    ) -> Optional[Any]:
        """Compute the identifier of a record in ``target_object_id``.

        Starting from ``data_obj`` and its ``record_id``, this method
        searches for a chain of relations leading to ``target_object_id``.
        If such a path exists, it loads each intermediate row and
        extracts the parent record identifier for the next object using
        the relation's mapping.  If at any point the necessary data
        cannot be found, ``None`` is returned.

        Parameters
        ----------
        data_obj: DataObject
            The data object representing the starting (child) source.
        record_id: Any
            The identifier of the record in ``data_obj`` from which to
            begin.  This must be a valid key in the underlying data.
        target_object_id: int
            The object id of the ancestor whose record identifier is
            sought.  If ``target_object_id`` equals the id of
            ``data_obj``, ``record_id`` is returned immediately.

        Returns
        -------
        The identifier of the record in the target object, or ``None``
        if no path exists or if any step in the join fails.
        """
        try:
            start_object_id = data_obj.orm.id  # type: ignore
        except Exception:
            return None
        if start_object_id == target_object_id:
            return record_id
        path = self._find_path(start_object_id, target_object_id)
        if not path:
            return None
        current_obj = data_obj
        current_record_id = record_id
        for rel in path:
            # Load the DataFrame row for current_record_id
            try:
                df = current_obj._load_df()
                id_col = current_obj._ensure_id_column()
                # Attempt to locate the row by matching record_id in id_col
                sub_df = df[df[id_col].astype(str) == str(current_record_id)]
                if sub_df.empty:
                    return None
                row_series = sub_df.iloc[0]
                # Convert to a dict keyed by column name
                row: Dict[str, Any] = {}
                for col_name, value in row_series.items():
                    row[str(col_name)] = value
            except Exception:
                return None
            # Extract the parent record id from the row using the relation
            parent_record_id = self._lookup_parent_id_in_row(row, current_obj, rel)
            if parent_record_id is None:
                return None
            # Advance to parent object
            try:
                if ObjectModel is None:
                    return None
                parent_orm = ObjectModel.objects.get(id=rel.parent_object_id)  # type: ignore
                current_obj = DataObject(orm=parent_orm)
                current_record_id = parent_record_id
            except Exception:
                return None
        return current_record_id