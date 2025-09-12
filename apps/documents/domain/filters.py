"""
filters.py
-------------

This module defines a registry of text filter strategies and helper
functions for applying a pipeline of filters to a string value.  A
filter is a pure transformation that takes a string and optional
arguments and returns a new string.  Filters can be composed in
pipelines via the ``apply_filter_chain`` function.

The registry is intentionally simple: keys are the names used in
placeholder definitions; values are instances implementing the
``TextFilter`` protocol.  To add a new filter, define a class with an
``apply`` method and insert it into ``FILTERS_REGISTRY``.

Example usage::

    >>> apply_filter_chain(" hello ", [("trim", []), ("upper", [])])
    'HELLO'

"""

from __future__ import annotations

from typing import Protocol, Dict, List, Tuple


class TextFilter(Protocol):
    """Protocol for all text filters.

    Filters should implement a single method ``apply`` that accepts a
    value and an arbitrary list of string arguments.  They must not
    mutate their input and should produce the same output for the same
    input and arguments.
    """

    def apply(self, value: str, *args: str) -> str:
        """Transform ``value`` into a new string using optional arguments."""
        ...  # pragma: no cover


class UpperCaseFilter:
    """Convert text to uppercase."""

    def apply(self, value: str, *args: str) -> str:
        return (value or "").upper()


class LowerCaseFilter:
    """Convert text to lowercase."""

    def apply(self, value: str, *args: str) -> str:
        return (value or "").lower()


class TitleCaseFilter:
    """Capitalize the first letter of each word (naïve title case)."""

    def apply(self, value: str, *args: str) -> str:
        return " ".join([w.capitalize() for w in (value or "").split()])


class TrimFilter:
    """Trim leading and trailing whitespace from the string."""

    def apply(self, value: str, *args: str) -> str:
        return (value or "").strip()


class ReplaceFilter:
    """Replace occurrences of a substring with another substring.

    The first argument is the substring to replace; the second argument
    is the replacement.  If either argument is missing, the value is
    returned unchanged.
    """

    def apply(self, value: str, *args: str) -> str:
        if len(args) >= 2:
            old, new = args[0], args[1]
            return (value or "").replace(old, new)
        return value or ""


class PrefixFilter:
    """Prefix the value with the given string (first argument)."""

    def apply(self, value: str, *args: str) -> str:
        if args:
            return args[0] + (value or "")
        return value or ""


class SuffixFilter:
    """Suffix the value with the given string (first argument)."""

    def apply(self, value: str, *args: str) -> str:
        if args:
            return (value or "") + args[0]
        return value or ""


class DateFormatFilter:
    """Format a date/time string using a strftime pattern.

    If the value cannot be parsed as a date, it is returned unchanged.
    The first argument should be a strftime pattern (e.g. ``%d.%m.%Y``).
    """

    def apply(self, value: str, *args: str) -> str:
        if not args:
            return value or ""
        fmt = args[0]
        import datetime as _dt  # delayed import to avoid cost if unused
        if not value:
            return ""
        try:
            # Try to parse ISO format first, then common Russian date format
            for parser in (lambda s: _dt.datetime.fromisoformat(s),
                           lambda s: _dt.datetime.strptime(s, "%d.%m.%Y")):
                try:
                    dt_obj = parser(value)
                    return dt_obj.strftime(fmt)
                except Exception:
                    continue
        except Exception:
            pass
        return value or ""


# Registry of available filters.  New filters can be added here.
FILTERS_REGISTRY: Dict[str, TextFilter] = {
    "upper": UpperCaseFilter(),
    "lower": LowerCaseFilter(),
    "title": TitleCaseFilter(),
    "trim": TrimFilter(),
    "replace": ReplaceFilter(),
    "prefix": PrefixFilter(),
    "suffix": SuffixFilter(),
    "date": DateFormatFilter(),
}


def apply_filter_chain(value: str, filters: List[Tuple[str, List[str]]]) -> str:
    """Apply a pipeline of filters to ``value``.

    ``filters`` should be a list of (name, args) tuples.  For each
    tuple, if a filter with the given name is registered, its ``apply``
    method is invoked with the current value and the provided args.
    Unknown filters are silently ignored.  The final transformed value
    is returned.
    """
    result = value or ""
    for name, args in filters:
        flt = FILTERS_REGISTRY.get(name)
        if flt is not None:
            try:
                result = flt.apply(result, *args)
            except Exception:
                # Swallow filter exceptions to avoid breaking generation
                continue
    return result