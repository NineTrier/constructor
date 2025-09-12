"""Tests for the placeholder filter pipeline.

These tests exercise the placeholder parsing and filter application logic
introduced in iteration 3. They ensure that filters are applied in
sequence and with arguments as expected.
"""

from __future__ import annotations

import pytest

from apps.documents.domain.placeholders import replace_placeholders


def test_upper_filter() -> None:
    def resolver(obj_key, field_key):
        return "john"

    text = "Hello {: user.name | upper :}"
    result = replace_placeholders(text, resolver)
    assert result == "Hello JOHN"


def test_lower_title_chain() -> None:
    def resolver(obj_key, field_key):
        return "ALPHA BETA"

    text = "Name: {: user.name | lower | title :}"
    result = replace_placeholders(text, resolver)
    assert result == "Name: Alpha Beta"


def test_trim_prefix_suffix() -> None:
    def resolver(obj_key, field_key):
        return "  world  "

    text = "Greeting: {: obj.field | trim | prefix:Hello | suffix:! :}"
    # Prefix and suffix are concatenated with the trimmed value
    expected = "Greeting: Hello world !"
    result = replace_placeholders(text, resolver)
    assert result == expected


def test_replace_filter() -> None:
    def resolver(obj_key, field_key):
        return "a_b_c"

    text = "Replaced: {: user.code | replace:_,/ :}"
    result = replace_placeholders(text, resolver)
    assert result == "Replaced: a/b/c"


def test_date_filter() -> None:
    def resolver(obj_key, field_key):
        # ISO date format
        return "2024-07-15"

    text = "Date: {: user.birth | date:%d.%m.%Y :}"
    result = replace_placeholders(text, resolver)
    assert result == "Date: 15.07.2024"