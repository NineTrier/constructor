"""Simple text filter strategies used in templates.

Filters can be applied to placeholder values when rendering a document.
Each filter implements a common interface by providing an ``apply``
method that accepts a string and returns a transformed string. New
filters can be added to ``FILTERS_REGISTRY`` to extend functionality.
"""

from __future__ import annotations

from typing import Protocol, Dict, Callable

__all__ = [
    "TextFilter",
    "UpperCaseFilter",
    "LowerCaseFilter",
    "FILTERS_REGISTRY",
]


class TextFilter(Protocol):
    """A protocol describing a text filter strategy.

    Implementations must provide an ``apply`` method that accepts a
    string and returns a new, transformed string. Filters should be
    stateless and not mutate their input.
    """

    def apply(self, value: str) -> str:
        """Apply the filter to ``value`` and return the result."""
        ...


class UpperCaseFilter:
    """Transform text to uppercase."""

    def apply(self, value: str) -> str:
        return (value or "").upper()


class LowerCaseFilter:
    """Transform text to lowercase."""

    def apply(self, value: str) -> str:
        return (value or "").lower()


# A registry mapping filter names to their implementations.
# Additional filters can be registered here or via application
# configuration.
FILTERS_REGISTRY: Dict[str, TextFilter] = {
    "upper": UpperCaseFilter(),
    "lower": LowerCaseFilter(),
}
