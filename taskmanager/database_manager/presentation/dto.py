from dataclasses import dataclass
from typing import Any, Dict, Mapping, Optional

from ..domain.normalize import canonicalize_value


@dataclass(frozen=True)
class RecordFieldDTO:
    type: str
    value: Any


@dataclass(frozen=True)
class RecordDTO:
    record_uid: str
    fields: Dict[str, RecordFieldDTO]


def legacy_record_to_dto(
    legacy_record: Mapping[str, Any],
    *,
    schema: Optional[Mapping[str, Mapping[str, Any]]] = None,
    canonicalize: bool = False,
) -> RecordDTO:
    """
    Convert a legacy record payload into the unified API v1 DTO.

    Expected legacy shape:
    {
      "id_to_connect": "legacy-row-id",
      "<param_id>": {"data_type": "...", "value": ...} | "<raw>"
    }
    """
    record_uid = str(legacy_record.get("id_to_connect", "") or "")
    fields: Dict[str, RecordFieldDTO] = {}
    for key, raw_field in legacy_record.items():
        if key in {"id_to_connect", "record_uid"}:
            continue
        schema_field = (schema or {}).get(str(key), {})
        if isinstance(raw_field, Mapping):
            field_type = str(raw_field.get("data_type", schema_field.get("type", "TXT")) or "TXT")
            field_value = raw_field.get("value", "")
        else:
            field_type = str(schema_field.get("type", "TXT") or "TXT")
            field_value = raw_field
        if canonicalize:
            field_value = canonicalize_value(
                field_type,
                field_value,
                array_separator=schema_field.get("array_separator"),
                date_format=schema_field.get("date_format"),
            )
        fields[str(key)] = RecordFieldDTO(type=field_type, value=field_value)
    return RecordDTO(record_uid=record_uid, fields=fields)


def serialise_record_dto(dto: RecordDTO) -> Dict[str, Any]:
    return {
        "record_uid": dto.record_uid,
        "fields": {
            str(param_id): {
                "type": field.type,
                "value": field.value,
            }
            for param_id, field in dto.fields.items()
        },
    }


def dto_to_legacy_record(dto: RecordDTO) -> Dict[str, Any]:
    legacy_payload: Dict[str, Any] = {"id_to_connect": dto.record_uid}
    for param_id, field in dto.fields.items():
        legacy_payload[str(param_id)] = {
            "data_type": field.type,
            "value": field.value,
        }
    return legacy_payload


def dto_to_legacy_parameters(dto: RecordDTO) -> Dict[str, Any]:
    return {
        str(param_id): {
            "data_type": field.type,
            "value": field.value,
        }
        for param_id, field in dto.fields.items()
    }


def build_v1_payload_from_legacy_record(
    legacy_record: Mapping[str, Any],
    *,
    include_deprecated_fields: bool = False,
) -> Dict[str, Any]:
    dto = legacy_record_to_dto(legacy_record)
    payload: Dict[str, Any] = {
        "api_version": "v1",
        "record": serialise_record_dto(dto),
    }
    if include_deprecated_fields:
        payload["parameters"] = dto_to_legacy_parameters(dto)
        payload["legacy_records"] = [dto_to_legacy_record(dto)]
    return payload