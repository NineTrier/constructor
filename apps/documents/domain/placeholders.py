"""Utilities for working with template placeholders.

Placeholder syntax follows two formats:

* Legacy format: ``{: param :}`` — uses no explicit object prefix. The
  system will attempt to resolve the parameter against the first
  connected object when generating a document.
* Recommended format: ``{: ObjectName.paramName :}`` — prefixes the
  parameter with the name or identifier of the connected data
  object, enabling unambiguous resolution when multiple objects are
  attached to a template.

This module provides functions to find placeholders in a string and
replace them using a resolver callable. It also defines a dataclass
representing a parsed placeholder.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable, Iterable, Optional

__all__ = [
    "Placeholder",
    "find_placeholders_in_text",
    "replace_placeholders",
]

# Regular expression capturing both legacy and new placeholder formats.
# The internal group extracts the reference inside the placeholder
# delimiters. Leading/trailing whitespace inside the delimiters is
# tolerated.
_PLACEHOLDER_PATTERN = re.compile(r"\{:\s*([A-Za-z0-9_\-.]+)\s*:\}")


def _split_ref(ref: str) -> tuple[Optional[str], str]:
    """Split a placeholder reference into its object and field parts.

    Parameters
    ----------
    ref: str
        The raw reference captured from the placeholder. May include a
        ``.`` delimiter separating object and field names.

    Returns
    -------
    (object_key, field_key): tuple[Optional[str], str]
        A pair where ``object_key`` is the optional name of the data
        source and ``field_key`` is the name of the field. If no
        delimiter is present, ``object_key`` will be ``None``.
    """
    if "." in ref:
        obj, field = ref.split(".", 1)
        return obj.strip(), field.strip()
    return None, ref.strip()


@dataclass(frozen=True)
class Placeholder:
    """A single placeholder in a template text.

    Attributes
    ----------
    raw: str
        The raw placeholder text including delimiters (e.g. ``{: u.name :}``).
    object_key: Optional[str]
        The optional name or identifier of the data object. ``None`` if
        unspecified (legacy format).
    field_key: str
        The name of the field to resolve on the data object.
    start: int
        The start index of the placeholder within the original text.
    end: int
        The end index (exclusive) of the placeholder within the text.
    """

    raw: str
    object_key: Optional[str]
    field_key: str
    start: int
    end: int


def find_placeholders_in_text(text: str) -> Iterable[Placeholder]:
    """Locate all placeholders in a string.

    Parameters
    ----------
    text: str
        The string to search for placeholders. If ``None`` or empty, no
        placeholders are yielded.

    Yields
    ------
    Placeholder
        A dataclass instance describing each placeholder found.
    """
    if not text:
        return
    for match in _PLACEHOLDER_PATTERN.finditer(text):
        ref = match.group(1)
        obj_key, field_key = _split_ref(ref)
        yield Placeholder(
            raw=match.group(0),
            object_key=obj_key,
            field_key=field_key,
            start=match.start(),
            end=match.end(),
        )


def replace_placeholders(text: str, resolver: Callable[[Optional[str], str], str]) -> str:
    """Replace placeholders in a string by calling a resolver.

    Parameters
    ----------
    text: str
        The source string containing placeholders.
    resolver: Callable[[Optional[str], str], str]
        A function accepting ``(object_key, field_key)`` and returning
        the replacement string. If the resolver returns a falsy value,
        an empty string is substituted.

    Returns
    -------
    str
        The string with all placeholders replaced.
    """

    def _sub(match: re.Match[str]) -> str:
        ref = match.group(1)
        obj_key, field_key = _split_ref(ref)
        return str(resolver(obj_key, field_key) or "")

    return _PLACEHOLDER_PATTERN.sub(_sub, text or "")
