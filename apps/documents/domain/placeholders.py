"""
placeholders.py
------------------

This module implements parsing and replacement of placeholders in the
JSON representation of document templates.  Placeholders have the
general form::

    {: object.field | filter1[:arg1[,arg2]] | filter2 :}

The ``object.field`` part specifies the data source and parameter to
inject; the optional filter pipeline applies a sequence of
transformations to the resulting value.  Filters are separated by the
pipe symbol ``|`` and may include a colon to delimit arguments.

Legacy placeholders of the form ``{: field :}`` (without an object
name) are also supported and are interpreted as referring to the
first available data source.

Parsing is performed using a simple regular expression to capture the
contents between ``{:`` and ``:}``.  The body is then split on
``|`` to separate the reference from the filter specifications.  This
implementation is intentionally permissive and will ignore malformed
filter parts rather than raising exceptions.

Replacement is handled via the ``replace_placeholders`` function,
which accepts a resolver callable.  The resolver is invoked with
``object_key``, ``field_key`` and a list of filter specifications.

Note: Placeholders can appear anywhere in the template's JSON, but
this module operates only on plain text values.  Higher‑level logic
decides where to call ``replace_placeholders``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable, Iterable, List, Optional, Tuple


@dataclass(frozen=True)
class Placeholder:
    """Represents a parsed placeholder in a text string."""

    raw: str
    object_key: Optional[str]
    field_key: str
    filters: List[Tuple[str, List[str]]]
    start: int
    end: int


# Regular expression to capture the body of a placeholder.  The body is
# everything between ``{:`` and ``:}``, non‑greedy.
_BODY_PATTERN = re.compile(r'{:\\s*([^:}]+)\\s*:}')


def _parse_body(body: str) -> Tuple[Optional[str], str, List[Tuple[str, List[str]]]]:
    """Parse the interior of a placeholder into its components.

    ``body`` includes the object/field reference and optional filters
    separated by the pipe character.  The first segment (before the
    first ``|``) is the reference; subsequent segments are filter
    specifications.  Each filter specification may have a colon
    separating the filter name from its comma‑separated arguments.
    Returns a tuple ``(object_key, field_key, filters)``.
    """
    parts = [p.strip() for p in body.split("|")]
    if not parts:
        return None, "", []
    ref = parts[0]
    # Determine object and field
    obj_key: Optional[str]
    field_key: str
    if "." in ref:
        obj_key, field_key = [s.strip() for s in ref.split(".", 1)]
        if not obj_key:
            obj_key = None
    else:
        obj_key = None
        field_key = ref.strip()
    # Parse filters
    filters: List[Tuple[str, List[str]]] = []
    for segment in parts[1:]:
        if not segment:
            continue
        # Split on the first colon to separate name and arg string
        if ":" in segment:
            name, arg_str = segment.split(":", 1)
            args = [a.strip() for a in arg_str.split(",") if a.strip()]
        else:
            name = segment
            args = []
        name = name.strip()
        if name:
            filters.append((name, args))
    return obj_key, field_key, filters


def find_placeholders_in_text(text: str) -> Iterable[Placeholder]:
    """Yield ``Placeholder`` instances for each placeholder in ``text``.

    The returned placeholders include their position within the text and
    the parsed components (object key, field key and filters).  If
    ``text`` is falsy, this generator yields nothing.
    """
    if not text:
        return
    for match in _BODY_PATTERN.finditer(text):
        body = match.group(1)
        obj_key, field_key, filters = _parse_body(body)
        yield Placeholder(
            raw=match.group(0),
            object_key=obj_key,
            field_key=field_key,
            filters=filters,
            start=match.start(),
            end=match.end(),
        )


def replace_placeholders(
    text: str,
    resolver: Callable[[Optional[str], str, List[Tuple[str, List[str]]]], str],
) -> str:
    """Replace all placeholders in ``text`` using a resolver.

    The ``resolver`` callable should accept ``object_key``, ``field_key``
    and ``filters`` (a list of (name, args) tuples) and return a
    string replacement.  Unknown placeholders are replaced with an
    empty string.  If ``text`` is falsy, it is returned unchanged.
    """
    if not text:
        return text or ""
    def _sub(match: re.Match) -> str:
        body = match.group(1)
        obj_key, field_key, filters = _parse_body(body)
        try:
            return str(resolver(obj_key, field_key, filters) or "")
        except Exception:
            return ""
    return _BODY_PATTERN.sub(_sub, text)