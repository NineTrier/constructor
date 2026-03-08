from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Tuple

from django.db import transaction
import pandas as pd

from ..models import (
    Object,
    ObjectLinkMeta,
    ObjectRecord,
    Parameter,
    ParameterValue,
    RecordLink,
    Object_ParentObject,
)


class SqlRecordRepository:
    """
    SQL repository for object records/values/links.
    """

    def get_record(self, obj: Object, record_uid: str) -> Optional[ObjectRecord]:
        return (
            ObjectRecord.objects.filter(object=obj, record_uid=str(record_uid))
            .select_related("object")
            .first()
        )

    def get_record_by_legacy_id(self, obj: Object, legacy_id: str) -> Optional[ObjectRecord]:
        return (
            ObjectRecord.objects.filter(object=obj, legacy_id_to_connect=str(legacy_id))
            .select_related("object")
            .first()
        )

    def get_record_by_uid_or_legacy(self, obj: Object, identifier: str) -> Optional[ObjectRecord]:
        record = self.get_record(obj, identifier)
        if record is not None:
            return record
        return self.get_record_by_legacy_id(obj, identifier)

    def _build_records_queryset(
        self,
        obj: Object,
        *,
        filters: Optional[Dict[str, Any]] = None,
        order_by: Optional[str] = None,
    ):
        queryset = ObjectRecord.objects.filter(object=obj)
        filters = filters or {}
        record_uid = filters.get("record_uid")
        if record_uid:
            queryset = queryset.filter(record_uid=str(record_uid))
        legacy_id = filters.get("legacy_id_to_connect")
        if legacy_id:
            queryset = queryset.filter(legacy_id_to_connect=str(legacy_id))
        order = (order_by or "").strip()
        if order == "-updated_at":
            queryset = queryset.order_by("-updated_at", "-id")
        elif order == "updated_at":
            queryset = queryset.order_by("updated_at", "id")
        elif order == "record_uid":
            queryset = queryset.order_by("record_uid", "id")
        elif order == "-record_uid":
            queryset = queryset.order_by("-record_uid", "-id")
        else:
            queryset = queryset.order_by("id")
        return queryset

    def list_records(
        self,
        obj: Object,
        *,
        filters: Optional[Dict[str, Any]] = None,
        order_by: Optional[str] = None,
        limit: Optional[int] = None,
        offset: Optional[int] = None,
    ):
        queryset = self._build_records_queryset(
            obj,
            filters=filters,
            order_by=order_by,
        )
        if offset:
            queryset = queryset[int(offset):]
        if limit is not None:
            queryset = queryset[: max(int(limit), 0)]
        return queryset

    def count_records(self, obj: Object, *, filters: Optional[Dict[str, Any]] = None) -> int:
        queryset = self._build_records_queryset(obj, filters=filters, order_by=None)
        return int(queryset.count())

    @transaction.atomic
    def upsert_record(
        self,
        obj: Object,
        record_uid: str,
        fields: Dict[str, Dict[str, Any]],
        *,
        legacy_id_to_connect: Optional[str] = None,
    ) -> ObjectRecord:
        defaults: Dict[str, Any] = {}
        if legacy_id_to_connect is not None:
            defaults["legacy_id_to_connect"] = legacy_id_to_connect
        record, _ = ObjectRecord.objects.update_or_create(
            object=obj,
            record_uid=str(record_uid),
            defaults=defaults,
        )
        for raw_param_id, raw_field in fields.items():
            try:
                param_id = int(raw_param_id)
            except (TypeError, ValueError):
                continue
            parameter = Parameter.objects.filter(id=param_id, object=obj).first()
            if parameter is None:
                continue
            field_type = str(raw_field.get("type", parameter.data_type) or parameter.data_type)
            field_value = raw_field.get("value")
            typed_values = self._typed_values(field_type, field_value)
            ParameterValue.objects.update_or_create(
                record=record,
                parameter=parameter,
                defaults=typed_values,
            )
        return record

    @transaction.atomic
    def delete_record(self, obj: Object, record_uid: str) -> int:
        deleted, _ = ObjectRecord.objects.filter(object=obj, record_uid=str(record_uid)).delete()
        return int(deleted)

    @transaction.atomic
    def upsert_link(
        self,
        object_link: Object_ParentObject,
        parent_record: ObjectRecord,
        child_record: ObjectRecord,
        *,
        object_link_meta: Optional[ObjectLinkMeta] = None,
    ) -> RecordLink:
        lookup: Dict[str, Any] = {
            "parent_record": parent_record,
            "child_record": child_record,
        }
        defaults: Dict[str, Any] = {
            "object_link": object_link,
            "object_link_meta": object_link_meta,
        }
        if object_link_meta is None:
            lookup["object_link"] = object_link
            lookup["object_link_meta"] = None
        else:
            lookup["object_link_meta"] = object_link_meta
        link, _ = RecordLink.objects.update_or_create(
            **lookup,
            defaults=defaults,
        )
        return link

    @transaction.atomic
    def delete_links_for_object_link(self, object_link: Object_ParentObject) -> int:
        deleted, _ = RecordLink.objects.filter(object_link=object_link).delete()
        return int(deleted)

    @transaction.atomic
    def delete_links_for_parent_record(self, parent_record: ObjectRecord) -> int:
        deleted, _ = RecordLink.objects.filter(parent_record=parent_record).delete()
        return int(deleted)

    def get_parameter_values_map(self, record: ObjectRecord) -> Dict[int, ParameterValue]:
        values = ParameterValue.objects.filter(record=record).select_related("parameter")
        return {value.parameter_id: value for value in values}

    def serialise_record_to_legacy(
        self,
        record: ObjectRecord,
        parameters: Iterable[Parameter],
        *,
        link_map: Optional[Dict[int, List[str]]] = None,
    ) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "id_to_connect": record.record_uid,
        }
        values_map = self.get_parameter_values_map(record)
        link_map = link_map or {}
        for parameter in parameters:
            if parameter.id in link_map:
                linked_values = link_map.get(parameter.id, [])
                if parameter.data_type == "ARRAY":
                    value = linked_values
                else:
                    value = linked_values[0] if linked_values else ""
                payload[str(parameter.id)] = {
                    "data_type": parameter.data_type,
                    "value": value,
                }
                continue
            value_obj = values_map.get(parameter.id)
            value: Any = ""
            if value_obj is not None:
                value = self._value_from_parameter_value(value_obj, parameter)
            payload[str(parameter.id)] = {
                "data_type": parameter.data_type,
                "value": value,
            }
        return payload

    @staticmethod
    def _typed_values(field_type: str, value: Any) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "value_text": None,
            "value_int": None,
            "value_datetime": None,
            "value_json": None,
        }
        normalised_type = str(field_type).upper()
        if normalised_type in {"TXT", "TXTS"}:
            payload["value_text"] = "" if value is None else str(value)
        elif normalised_type == "INT":
            try:
                payload["value_int"] = int(value) if value not in (None, "") else None
            except (TypeError, ValueError):
                payload["value_int"] = None
                payload["value_text"] = "" if value is None else str(value)
        elif normalised_type == "DATE":
            # Parsing/localisation is handled by application/presentation layers.
            payload["value_text"] = "" if value is None else str(value)
        elif normalised_type == "ARRAY":
            if value is None:
                payload["value_json"] = []
            elif isinstance(value, list):
                payload["value_json"] = value
            else:
                payload["value_json"] = [value]
        else:
            payload["value_text"] = "" if value is None else str(value)
        return payload

    @staticmethod
    def _value_from_parameter_value(value_obj: ParameterValue, parameter: Parameter) -> Any:
        data_type = (parameter.data_type or "").upper()
        if data_type in {"TXT", "TXTS", "DATE"}:
            if value_obj.value_text is not None:
                return value_obj.value_text
            if value_obj.value_datetime is not None:
                return value_obj.value_datetime.isoformat()
            return ""
        if data_type == "INT":
            if value_obj.value_int is not None:
                return value_obj.value_int
            if value_obj.value_text is not None:
                return value_obj.value_text
            return ""
        if data_type == "ARRAY":
            if isinstance(value_obj.value_json, list):
                return [str(item) for item in value_obj.value_json if item is not None]
            if value_obj.value_text:
                separator = parameter.array_separator or " "
                return [item.strip() for item in str(value_obj.value_text).split(separator) if item.strip()]
            return []
        if value_obj.value_text is not None:
            return value_obj.value_text
        if value_obj.value_json is not None:
            return value_obj.value_json
        if value_obj.value_int is not None:
            return value_obj.value_int
        if value_obj.value_datetime is not None:
            return value_obj.value_datetime.isoformat()
        return ""


class FileRecordRepository:
    """
    Legacy adapter over dataframe-file storage.
    """

    def __init__(self):
        from .. import views as legacy_views

        self._legacy_views = legacy_views

    def load_dataframe(self, obj: Object, *, allow_empty: bool = False):
        return self._legacy_views._safe_load_dataframe(
            obj.data,
            object_id=obj.pk,
            object_instance=obj,
            allow_empty=allow_empty,
        )

    def save_dataframe(self, obj: Object, df) -> str:
        return self._legacy_views._write_dataframe(
            obj.data,
            df,
            object_instance=obj,
        )

    def get_raw_record(self, obj: Object, record_uid: str) -> Tuple[Optional[Dict[str, Any]], list[str]]:
        df, warnings = self.load_dataframe(obj, allow_empty=False)
        if df is None:
            return None, warnings
        if "record_uid" in df.columns:
            rows = df[df["record_uid"].astype(str) == str(record_uid)]
            if not rows.empty:
                return rows.iloc[0].to_dict(), warnings
        if "id_to_connect" not in df.columns:
            return None, warnings
        rows = df[df["id_to_connect"].astype(str) == str(record_uid)]
        if rows.empty:
            return None, warnings
        return rows.iloc[0].to_dict(), warnings

    def list_raw_rows(
        self,
        obj: Object,
        *,
        allow_empty: bool = True,
        ensure_record_uid: bool = True,
        persist: bool = False,
    ) -> Tuple[List[Dict[str, Any]], List[str]]:
        df, warnings = self.load_dataframe(obj, allow_empty=allow_empty)
        if df is None:
            return [], warnings
        if ensure_record_uid:
            df, changed = self._legacy_views._ensure_record_uid_column(obj, df, persist=False)
            if changed and persist:
                self.save_dataframe(obj, df)
        if "id_to_connect" not in df.columns:
            df["id_to_connect"] = pd.NA
        rows = [row.to_dict() for _, row in df.iterrows()]
        return rows, warnings
