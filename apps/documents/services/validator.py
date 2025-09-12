"""Validator for document templates.

This module provides utilities to validate placeholders within a
``DocumentTemplate``. It detects issues such as missing or unknown
objects, unknown fields, and unknown filters. The goal is to help
template authors identify problems early, before attempting to
generate documents.

Example usage:

    from apps.documents.domain.document_template import DocumentTemplate
    from apps.documents.services.validator import validate_template

    template = DocumentTemplate.load(pattern_id)
    issues = validate_template(template)
    for issue in issues:
        print(issue["message"])
"""

from __future__ import annotations

from typing import List, Dict, Any, Optional

try:
    # Import Django models when available
    from constructor.models import Parameter as ParameterModel  # type: ignore
    from constructor.models import Object as ObjectModel  # type: ignore
except Exception:
    ParameterModel = None  # type: ignore
    ObjectModel = None  # type: ignore

from ..domain.document_template import DocumentTemplate
from ..domain.placeholders import Placeholder
from ..domain.filters import FILTERS_REGISTRY


def _resolve_object_id(
    obj_key: Optional[str], connected: Dict[str, int]
) -> Optional[int]:
    """Resolve the object identifier from a key.

    ``connected`` maps both object names and stringified ids to the
    object's integer id. Keys may be case sensitive depending on
    ``connected_objects``.
    """
    if obj_key is None:
        return None
    # direct lookup by name
    if obj_key in connected:
        return connected[obj_key]
    # try numeric id
    try:
        obj_id = int(obj_key)
        return connected.get(str(obj_id))
    except Exception:
        return None


def _field_exists(object_id: int, field_key: str) -> bool:
    """Return True if ``field_key`` corresponds to a parameter of the object.

    The key may be either the parameter name or the parameter id
    represented as a string.
    """
    if ParameterModel is None:
        return False
    # Check by name
    try:
        if ParameterModel.objects.filter(object_id=object_id, name=field_key).exists():  # type: ignore
            return True
    except Exception:
        pass
    # Check by id
    try:
        param_id = int(field_key)
        return ParameterModel.objects.filter(id=param_id, object_id=object_id).exists()  # type: ignore
    except Exception:
        return False


def _validate_placeholder(
    placeholder: Placeholder,
    path: List[str],
    connected: Dict[str, int],
) -> List[Dict[str, Any]]:
    """Validate a single placeholder and return a list of issues.

    Parameters
    ----------
    placeholder: Placeholder
        The parsed placeholder to validate.
    path: List[str]
        The navigation path within the template JSON where this
        placeholder appears.
    connected: Dict[str, int]
        Mapping from object names and ids (as strings) to object ids
        for all objects connected to the template.

    Returns
    -------
    List[Dict[str, Any]]
        A list of issue dictionaries. Each issue has keys
        ``code`` (machine‑readable string), ``message`` (human‑readable
        explanation), ``path`` (location in the template), and
        ``placeholder`` (the raw placeholder text).
    """
    issues: List[Dict[str, Any]] = []
    # Determine object id
    obj_id = _resolve_object_id(placeholder.object_key, connected)
    # If object_key is None and more than one object is connected → ambiguous
    if placeholder.object_key is None:
        if len(set(connected.values())) > 1:
            issues.append({
                "code": "ambiguous_object",
                "message": "Placeholder without object key but multiple objects connected",
                "path": path,
                "placeholder": placeholder.raw,
            })
            return issues
        # If only one object, take its id implicitly
        if len(set(connected.values())) == 1:
            obj_id = next(iter(connected.values()))
    # Unknown object
    if obj_id is None:
        if placeholder.object_key is not None:
            issues.append({
                "code": "unknown_object",
                "message": f"Object '{placeholder.object_key}' is not connected to this template",
                "path": path,
                "placeholder": placeholder.raw,
            })
        return issues
    # Unknown field on known object
    if not _field_exists(obj_id, placeholder.field_key):
        issues.append({
            "code": "unknown_field",
            "message": f"Field '{placeholder.field_key}' not found on object with id {obj_id}",
            "path": path,
            "placeholder": placeholder.raw,
        })
    # Unknown filters
    for name, _args in placeholder.filters:
        if name.lower() not in FILTERS_REGISTRY:
            issues.append({
                "code": "unknown_filter",
                "message": f"Unknown filter '{name}'",
                "path": path,
                "placeholder": placeholder.raw,
            })
    return issues


def validate_template(template: DocumentTemplate) -> List[Dict[str, Any]]:
    """Validate all placeholders in the given template.

    The function iterates through all placeholders found in the
    template's JSON structure and collects any issues.

    Returns a list of dictionaries describing problems. An empty list
    indicates that no validation errors were found.
    """
    # Build mapping of connected objects by both name and id (string)
    connected: Dict[str, int] = {}
    for meta in template.connected_objects:
        connected[meta["name"]] = meta["id"]
        connected[str(meta["id"])] = meta["id"]
    issues: List[Dict[str, Any]] = []
    # Iterate through placeholders with paths
    for path, placeholder in template.iter_placeholders():
        issues.extend(_validate_placeholder(placeholder, path, connected))
    return issues