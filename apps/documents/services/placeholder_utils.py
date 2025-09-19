"""
Utility functions to work with placeholders in templates.

This module defines a regular expression to find placeholders of the form
"{: placeholder_name :}". Unlike the original implementation, the regex is
lenient and captures any characters between the colons (including spaces,
unicode letters and punctuation) until the next colon or closing brace.

Functions:
    extract_placeholders(text): return a list of placeholder expressions.
    replace_placeholders(text, context): replace placeholders using a mapping.
"""

from __future__ import annotations

import re
from typing import Dict, List

# Match placeholders like "{: Some Placeholder :}".  We capture everything
# between the colons up to the closing brace.  The captured expression is
# stripped of leading/trailing whitespace before returning.
PLACEHOLDER_RE = re.compile(r"{:\s*([^:}]+)\s*:}")


def extract_placeholders(text: str) -> List[str]:
    """Return a list of placeholder expressions found in the given text.

    Args:
        text: The text in which to search for placeholders.

    Returns:
        A list of placeholder expressions without surrounding braces and colons.
    """
    return [match.strip() for match in PLACEHOLDER_RE.findall(text or "")]


def replace_placeholders(text: str, context: Dict[str, str]) -> str:
    """Replace placeholders in ``text`` with values from ``context``.

    Args:
        text: The text containing zero or more placeholders.
        context: A mapping from placeholder expressions (as returned by
            ``extract_placeholders``) to replacement strings.

    Returns:
        The text with all placeholders replaced.  Any placeholder not found
        in ``context`` will remain unchanged.
    """
    def repl(match: re.Match) -> str:
        expr = match.group(1).strip()
        return str(context.get(expr, match.group(0)))

    return PLACEHOLDER_RE.sub(repl, text or "")