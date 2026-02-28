from __future__ import annotations

from datetime import date, datetime
from math import isfinite
from typing import Any, Dict, Iterable, Mapping, Optional

from django.utils.dateparse import parse_date, parse_datetime


EMPTY_TEXT_MARKERS = {"", "nan", "none", "<na>"}


def is_empty_like(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return value.strip().lower() in EMPTY_TEXT_MARKERS
    try:
        import pandas as pd  # local import; optional dependency for tooling/tests

        if pd.isna(value):  # type: ignore[arg-type]
            return True
    except Exception:
        pass
    return False


def canonicalize_value(
    data_type: str,
    value: Any,
    *,
    array_separator: Optional[str] = None,
    date_format: Optional[str] = None,
) -> Any:
    del date_format  # reserved for future strict format parsing rules
    normalised_type = str(data_type or "TXT").upper()

    if normalised_type == "ARRAY":
        return _canonicalize_array(value, array_separator=array_separator)
    if is_empty_like(value):
        return None
    if normalised_type in {"TXT", "TXTS"}:
        return str(value).strip()
    if normalised_type == "INT":
        return _canonicalize_int(value)
    if normalised_type == "DATE":
        return _canonicalize_date(value)
    return str(value).strip()


def canonicalize_record(
    record_v1: Mapping[str, Any],
    schema: Optional[Mapping[str, Mapping[str, Any]]] = None,
) -> Dict[str, Any]:
    fields_raw = record_v1.get("fields", {})
    fields_map = fields_raw if isinstance(fields_raw, Mapping) else {}
    schema_map = schema or {}

    if schema_map:
        field_ids = [str(param_id) for param_id in schema_map.keys()]
    else:
        field_ids = [str(param_id) for param_id in fields_map.keys()]

    canonical_fields: Dict[str, Dict[str, Any]] = {}
    for param_id in field_ids:
        schema_entry = schema_map.get(param_id, {})
        raw_field = fields_map.get(param_id, {})
        if isinstance(raw_field, Mapping):
            field_type = str(raw_field.get("type") or schema_entry.get("type") or "TXT")
            raw_value = raw_field.get("value")
        else:
            field_type = str(schema_entry.get("type") or "TXT")
            raw_value = raw_field
        canonical_fields[param_id] = {
            "type": field_type,
            "value": canonicalize_value(
                field_type,
                raw_value,
                array_separator=schema_entry.get("array_separator"),
                date_format=schema_entry.get("date_format"),
            ),
        }

    record_uid = str(record_v1.get("record_uid") or record_v1.get("id_to_connect") or "")
    return {
        "record_uid": record_uid,
        "fields": canonical_fields,
    }


def schema_from_parameters(parameters: Iterable[Any]) -> Dict[str, Dict[str, Any]]:
    schema: Dict[str, Dict[str, Any]] = {}
    for parameter in parameters:
        schema[str(parameter.id)] = {
            "type": str(getattr(parameter, "data_type", "TXT") or "TXT"),
            "array_separator": getattr(parameter, "array_separator", None),
            "date_format": getattr(parameter, "date_format", None),
            "name": str(getattr(parameter, "name", "") or ""),
        }
    return schema


def _canonicalize_array(value: Any, *, array_separator: Optional[str]) -> list[str]:
    if is_empty_like(value):
        return []
    if isinstance(value, list):
        items = value
    elif isinstance(value, tuple):
        items = list(value)
    elif isinstance(value, set):
        items = list(value)
    else:
        raw = str(value)
        separator = array_separator or " "
        if separator == " ":
            items = raw.split()
        else:
            items = raw.split(separator)
    cleaned: list[str] = []
    for item in items:
        if is_empty_like(item):
            continue
        cleaned.append(str(item).strip())
    return cleaned


def _canonicalize_int(value: Any) -> Optional[int]:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if not isfinite(value):
            return None
        return int(value) if value.is_integer() else None
    value_str = str(value).strip()
    if not value_str:
        return None
    try:
        return int(value_str)
    except (TypeError, ValueError):
        try:
            float_value = float(value_str)
        except (TypeError, ValueError):
            return None
        if not isfinite(float_value) or not float_value.is_integer():
            return None
        return int(float_value)


def _canonicalize_date(value: Any) -> Optional[str]:
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    value_str = str(value).strip()
    if not value_str:
        return None
    parsed_dt = parse_datetime(value_str)
    if parsed_dt is not None:
        return parsed_dt.date().isoformat()
    parsed_date = parse_date(value_str)
    if parsed_date is not None:
        return parsed_date.isoformat()
    try:
        from dateutil import parser as dateutil_parser

        parsed = dateutil_parser.parse(value_str, fuzzy=True)
        return parsed.date().isoformat()
    except Exception:
        return None
