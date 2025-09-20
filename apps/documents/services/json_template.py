"""
Utilities for parsing ``DocumentsPattern.json`` structure.

The original application stores each element and its child runs as JSON
strings inside the ``json`` field of the ``DocumentsPattern`` model.
This module provides a helper to deserialize those strings into Python
structures and extract plain-text paragraphs for further processing.
"""

from __future__ import annotations

import json
from typing import Any, List, Dict


def parse_document_json(raw_json: Dict[str, Any]) -> List[str]:
    """Extract paragraph texts from a raw template JSON.

    Args:
        raw_json: The ``json`` field from ``DocumentsPattern``.  Expected to
            contain an ``elements`` list where each element can be either a
            dictionary or a JSON string.  Each element dictionary has a
            ``childs`` list containing run dictionaries or JSON strings.

    Returns:
        A list of strings, each representing the concatenated text of a
        paragraph.
    """
    paragraphs: List[str] = []
    elements = raw_json.get("elements", [])
    for elem in elements:
        # Decode element if it is stored as a JSON string.
        if isinstance(elem, str):
            try:
                elem_obj = json.loads(elem)
            except Exception:
                continue
        elif isinstance(elem, dict):
            elem_obj = elem
        else:
            continue

        child_list = elem_obj.get("childs", [])
        texts: List[str] = []
        for child in child_list:
            # Decode child if necessary.
            if isinstance(child, str):
                try:
                    child_obj = json.loads(child)
                except Exception:
                    continue
            elif isinstance(child, dict):
                child_obj = child
            else:
                continue
            text = child_obj.get("data-invis", "")
            texts.append(text)
        paragraph = "".join(texts)
        paragraphs.append(paragraph)
    return paragraphs