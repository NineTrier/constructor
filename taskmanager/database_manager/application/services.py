import json
import re
import uuid
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Tuple

from django.conf import settings
from django.db.models import Q, CharField, Max, OuterRef, Subquery, Value
from django.db.models.functions import Cast, Coalesce, Lower

from ..domain.normalize import canonicalize_record, canonicalize_value, schema_from_parameters
from ..infrastructure.repositories import FileRecordRepository, SqlRecordRepository
from ..models import (
    Object,
    ObjectLinkMeta,
    ObjectLink_identificators,
    Object_ParentObject,
    ObjectRecord,
    Parameter,
    ParameterValue,
    RecordLink,
)
from ..presentation.dto import legacy_record_to_dto, serialise_record_dto


def _category_name(parameter: Parameter) -> str:
    category = getattr(parameter, "category", None)
    if category is None:
        return ""
    return str(getattr(category, "name", "") or "")


def _looks_like_uid(value: str) -> bool:
    value = (value or "").strip()
    if not value:
        return False
    if len(value) == 32:
        try:
            int(value, 16)
            return True
        except ValueError:
            return False
    try:
        uuid.UUID(value)
        return True
    except ValueError:
        return False


def _legacy_linked_params_disabled() -> bool:
    return bool(getattr(settings, "DBM_DISABLE_LEGACY_LINKED_PARAMS", False))


def _legacy_link_meta_bridge_enabled() -> bool:
    return bool(getattr(settings, "DBM_UI_LEGACY_FALLBACK", False))


class ObjectDataService:
    """
    Application service for record reads and dual-write sync.
    """

    def __init__(
        self,
        *,
        sql_repo: Optional[SqlRecordRepository] = None,
        file_repo: Optional[FileRecordRepository] = None,
        logger=None,
    ):
        self.sql_repo = sql_repo or SqlRecordRepository()
        self.file_repo = file_repo or FileRecordRepository()
        self.logger = logger

    @staticmethod
    def _sql_source_of_truth_enabled() -> bool:
        return bool(getattr(settings, "DBM_SQL_SOURCE_OF_TRUTH", False))

    @staticmethod
    def _sql_write_enabled() -> bool:
        if getattr(settings, "DBM_SQL_SOURCE_OF_TRUTH", False):
            return True
        return bool(getattr(settings, "DBM_DUAL_WRITE", False))

    @staticmethod
    def _file_fallback_read_enabled() -> bool:
        return bool(getattr(settings, "DBM_FILE_FALLBACK_READ", True))

    @staticmethod
    def _schema_from_parameters(parameters: Iterable[Parameter]) -> Dict[str, Dict[str, Any]]:
        return schema_from_parameters(parameters)

    def build_record_response(
        self,
        obj: Object,
        legacy_record: Mapping[str, Any],
        parameters: Iterable[Parameter],
        *,
        api_version: str = "legacy",
    ) -> Tuple[Dict[str, Any], bool]:
        schema_map = self._schema_from_parameters(parameters)
        dto = legacy_record_to_dto(legacy_record, schema=schema_map, canonicalize=True)
        if api_version == "v1":
            schema_parameters: Dict[str, Dict[str, Any]] = {}
            for parameter in parameters:
                schema_parameters[str(parameter.id)] = {
                    "type": parameter.data_type,
                    "name": parameter.name,
                    "order": parameter.order,
                    "category": _category_name(parameter),
                    "array_separator": parameter.array_separator,
                    "date_format": parameter.date_format,
                }
            return {
                "api_version": "v1",
                "record": serialise_record_dto(dto),
                "schema": {
                    "object_id": obj.id,
                    "parameters": schema_parameters,
                },
            }, False
        return {"records": [dict(legacy_record)]}, True

    def read_record_with_policy(
        self,
        obj: Object,
        identifier: str,
        parameters: Iterable[Parameter],
        *,
        build_file_record: Callable[[], Optional[Dict[str, Any]]],
    ) -> Optional[Dict[str, Any]]:
        can_fallback = self._file_fallback_read_enabled()
        if getattr(settings, "DBM_READ_FROM_SQL", False):
            sql_record = self._get_legacy_record_from_sql(obj, identifier, parameters)
            if sql_record is None:
                self._log_event("sql_miss", object_id=obj.id, record_uid=str(identifier))
                return build_file_record() if can_fallback else None
            if not can_fallback:
                return sql_record
            file_record = build_file_record()
            if file_record is not None:
                self._log_shadow_diff(obj=obj, identifier=identifier, sql_record=sql_record, file_record=file_record)
            return sql_record
        return build_file_record()

    def dual_write_upsert(
        self,
        *,
        obj: Object,
        record_identifier: str,
        row_data: Mapping[str, Any],
        parameters: Iterable[Parameter],
        op: str,
        record_uid: Optional[str] = None,
        legacy_id_to_connect: Optional[str] = None,
    ) -> None:
        if not self._sql_write_enabled():
            return
        strict_mode = getattr(settings, "DBM_DUAL_WRITE_STRICT_FOR_TESTS", False)
        sql_primary = self._sql_source_of_truth_enabled()
        try:
            if legacy_id_to_connect is None:
                legacy_id = str(row_data.get("id_to_connect") or record_identifier)
            else:
                legacy_id = str(legacy_id_to_connect)
            if record_uid is None:
                record_uid = str(row_data.get("record_uid") or "")
            if not record_uid:
                record_uid = self.resolve_record_uid(obj=obj, legacy_id=legacy_id)
            fields: Dict[str, Dict[str, Any]] = {}
            for parameter in parameters:
                column_key = str(parameter.id)
                raw_value = row_data.get(column_key, "")
                fields[column_key] = {
                    "type": parameter.data_type,
                    "value": self._normalise_field_value(parameter, raw_value),
                }
            self.sql_repo.upsert_record(
                obj=obj,
                record_uid=record_uid,
                fields=fields,
                legacy_id_to_connect=legacy_id,
            )
        except Exception as exc:
            event = "sql_write_primary_failed" if sql_primary else "dual_write_failed"
            self._log_event(
                event,
                object_id=obj.id,
                record_uid=str(record_uid or record_identifier),
                op=op,
                exc=str(exc),
            )
            if strict_mode or sql_primary:
                raise

    def dual_write_delete(self, *, obj: Object, record_identifier: str) -> None:
        if not self._sql_write_enabled():
            return
        strict_mode = getattr(settings, "DBM_DUAL_WRITE_STRICT_FOR_TESTS", False)
        sql_primary = self._sql_source_of_truth_enabled()
        try:
            sql_record = self.sql_repo.get_record_by_uid_or_legacy(obj, str(record_identifier))
            if sql_record is None:
                return
            self.sql_repo.delete_links_for_parent_record(sql_record)
            self.sql_repo.delete_record(obj, sql_record.record_uid)
        except Exception as exc:
            self._log_event(
                "sql_write_primary_failed" if sql_primary else "dual_write_failed",
                object_id=obj.id,
                record_uid=str(record_identifier),
                op="delete",
                exc=str(exc),
            )
            if strict_mode or sql_primary:
                raise

    def run_secondary_file_write(
        self,
        *,
        write_callback: Optional[Callable[[], None]],
        object_id: int,
        record_uid: str,
        op: str,
    ) -> None:
        if not getattr(settings, "DBM_SQL_WRITE_FILE_SECONDARY", True):
            return
        if write_callback is None:
            return
        try:
            write_callback()
        except Exception as exc:
            self._log_event(
                "file_write_secondary_failed",
                object_id=object_id,
                record_uid=str(record_uid),
                op=op,
                exc=str(exc),
            )

    def resolve_record_uid(self, *, obj: Object, legacy_id: str) -> str:
        existing = self.sql_repo.get_record_by_legacy_id(obj, str(legacy_id))
        if existing is not None:
            return existing.record_uid
        return uuid.uuid5(uuid.NAMESPACE_URL, f"dbm:{obj.id}:{legacy_id}").hex

    def resolve_record_uid_from_identifier(self, *, obj: Object, identifier: str) -> str:
        existing = self.sql_repo.get_record_by_uid_or_legacy(obj, str(identifier))
        if existing is not None:
            return existing.record_uid
        identifier_str = str(identifier)
        if _looks_like_uid(identifier_str):
            return identifier_str
        return self.resolve_record_uid(obj=obj, legacy_id=identifier_str)

    def list_records(
        self,
        obj: Object,
        parameters: Iterable[Parameter],
        *,
        filters: Optional[Dict[str, Any]] = None,
        order_by: Optional[str] = None,
        limit: Optional[int] = None,
        offset: Optional[int] = None,
        file_loader: Optional[Callable[[], List[Dict[str, Any]]]] = None,
    ) -> List[Dict[str, Any]]:
        filters = filters or {}
        offset = max(int(offset or 0), 0)
        can_fallback = self._file_fallback_read_enabled()
        if getattr(settings, "DBM_READ_FROM_SQL", False):
            sql_limit = limit
            sql_offset = offset
            if order_by in {"identificator", "-identificator"}:
                sql_limit = None
                sql_offset = 0
            records = self._list_records_from_sql(
                obj=obj,
                parameters=parameters,
                filters=filters,
                order_by=order_by,
                limit=sql_limit,
                offset=sql_offset,
            )
            if records:
                if order_by in {"identificator", "-identificator"}:
                    if offset:
                        records = records[offset:]
                    if limit is not None:
                        records = records[: max(int(limit), 0)]
                return records
            self._log_event("sql_miss", object_id=obj.id, op="list_records")
            if not can_fallback:
                return []
        if file_loader is None:
            return []
        records = file_loader()
        if not records:
            return []
        if order_by == "identificator":
            records = sorted(records, key=lambda x: str(x.get("param_ident", "")))
        elif order_by == "-identificator":
            records = sorted(records, key=lambda x: str(x.get("param_ident", "")), reverse=True)
        else:
            records = list(records)
        if offset:
            records = records[offset:]
        if limit is not None:
            records = records[: max(int(limit), 0)]
        return records

    def list_records_v1(
        self,
        *,
        obj: Object,
        parameters: Iterable[Parameter],
        limit: int = 50,
        offset: int = 0,
        order: str = "updated_at",
        q: str = "",
        include_total: bool = False,
    ) -> Dict[str, Any]:
        safe_limit = max(int(limit or 50), 0)
        safe_offset = max(int(offset or 0), 0)
        safe_order = (order or "updated_at").strip().lower()
        query = (q or "").strip()
        can_fallback = self._file_fallback_read_enabled()
        params_list = list(parameters)
        ident_param = next((param for param in params_list if param.identificator), None)

        if getattr(settings, "DBM_READ_FROM_SQL", False):
            try:
                sql_result = self._list_records_v1_from_sql(
                    obj=obj,
                    ident_param=ident_param,
                    limit=safe_limit,
                    offset=safe_offset,
                    order=safe_order,
                    query=query,
                    include_total=include_total,
                )
                sql_rows_total = self.sql_repo.count_records(obj)
                if sql_rows_total > 0:
                    return sql_result
                self._log_event("sql_miss", object_id=obj.id, op="list_records_v1")
                if not can_fallback:
                    return {"records": [], "total": 0 if include_total else None, "has_more": False, "source": "sql"}
            except Exception as exc:
                self._log_event("sql_miss", object_id=obj.id, op="list_records_v1", exc=str(exc))
                if not can_fallback:
                    return {"records": [], "total": 0 if include_total else None, "has_more": False, "source": "sql"}
        return self._list_records_v1_from_file(
            obj=obj,
            ident_param=ident_param,
            limit=safe_limit,
            offset=safe_offset,
            order=safe_order,
            query=query,
            include_total=include_total,
        )

    def _list_records_v1_from_sql(
        self,
        *,
        obj: Object,
        ident_param: Optional[Parameter],
        limit: int,
        offset: int,
        order: str,
        query: str,
        include_total: bool,
    ) -> Dict[str, Any]:
        queryset = ObjectRecord.objects.filter(object=obj)
        if ident_param is not None:
            ident_subquery = (
                ParameterValue.objects.filter(record_id=OuterRef("pk"), parameter=ident_param)
                .annotate(
                    ident_text=Coalesce(
                        Cast("value_text", CharField()),
                        Cast("value_int", CharField()),
                        Cast("value_datetime", CharField()),
                        Value(""),
                        output_field=CharField(),
                    )
                )
                .values("ident_text")[:1]
            )
            queryset = queryset.annotate(
                _identificator=Coalesce(Subquery(ident_subquery), Value(""), output_field=CharField()),
            )
        else:
            queryset = queryset.annotate(_identificator=Value("", output_field=CharField()))

        if query:
            # TODO: add pg_trgm index to speed up ILIKE by identificator for large datasets.
            queryset = queryset.filter(_identificator__icontains=query)

        if order == "identificator":
            queryset = queryset.order_by(Lower("_identificator"), "record_uid")
        elif order == "record_uid":
            queryset = queryset.order_by("record_uid")
        else:
            queryset = queryset.order_by("-updated_at", "record_uid")

        total: Optional[int] = None
        if include_total:
            total = int(queryset.count())

        if limit <= 0:
            rows = []
            has_more = False
        else:
            window = list(queryset.values("record_uid", "_identificator")[offset : offset + limit + 1])
            has_more = len(window) > limit
            rows = window[:limit]

        return {
            "records": [
                {
                    "record_uid": str(row.get("record_uid") or ""),
                    "identificator": str(row.get("_identificator") or ""),
                }
                for row in rows
            ],
            "total": total,
            "has_more": has_more,
            "source": "sql",
        }

    def _list_records_v1_from_file(
        self,
        *,
        obj: Object,
        ident_param: Optional[Parameter],
        limit: int,
        offset: int,
        order: str,
        query: str,
        include_total: bool,
    ) -> Dict[str, Any]:
        rows, warnings = self.file_repo.list_raw_rows(obj, allow_empty=True, ensure_record_uid=True, persist=False)
        if warnings:
            self._log_event("file_load_warning", object_id=obj.id, warnings=warnings)
        ident_key = str(ident_param.id) if ident_param is not None else None
        prepared: List[Tuple[int, str, Dict[str, Any]]] = []
        lowered_query = query.lower()
        for index, row in enumerate(rows):
            record_uid = str(row.get("record_uid") or row.get("id_to_connect") or "").strip()
            if not record_uid:
                continue
            identificator = ""
            if ident_key is not None:
                raw_ident = row.get(ident_key, "")
                canonical_ident = canonicalize_value(
                    ident_param.data_type,
                    raw_ident,
                    array_separator=ident_param.array_separator,
                    date_format=ident_param.date_format,
                )
                identificator = "" if canonical_ident in (None, []) else str(canonical_ident)
            if lowered_query and lowered_query not in identificator.lower():
                continue
            prepared.append(
                (
                    index,
                    identificator,
                    {
                        "record_uid": record_uid,
                        "identificator": identificator,
                    },
                )
            )

        if order == "identificator":
            prepared = sorted(prepared, key=lambda item: (item[1].lower(), item[2]["record_uid"]))
        elif order == "record_uid":
            prepared = sorted(prepared, key=lambda item: item[2]["record_uid"])
        else:
            prepared = sorted(prepared, key=lambda item: (-item[0], item[2]["record_uid"]))

        total: Optional[int] = int(len(prepared)) if include_total else None
        if limit <= 0:
            rows_page: List[Dict[str, Any]] = []
            has_more = False
        else:
            window = prepared[offset : offset + limit + 1]
            has_more = len(window) > limit
            rows_page = [item[2] for item in window[:limit]]

        return {
            "records": rows_page,
            "total": total,
            "has_more": has_more,
            "source": "file",
        }

    def _get_legacy_record_from_sql(
        self,
        obj: Object,
        identifier: str,
        parameters: Iterable[Parameter],
    ) -> Optional[Dict[str, Any]]:
        record = self.sql_repo.get_record_by_uid_or_legacy(obj, str(identifier))
        if record is None:
            return None
        link_map: Dict[int, List[str]] = {}
        object_links = Object_ParentObject.objects.filter(parent_object=obj).select_related("object")
        for relation in object_links:
            param = Parameter.objects.filter(object=obj, linked_object=relation.object).first()
            if param is None:
                continue
            link_qs = RecordLink.objects.filter(object_link=relation, parent_record=record).select_related("child_record")
            child_values = []
            for link in link_qs:
                child_values.append(link.child_record.record_uid)
            link_map[param.id] = child_values
        return self.sql_repo.serialise_record_to_legacy(record, parameters, link_map=link_map)

    def _list_records_from_sql(
        self,
        *,
        obj: Object,
        parameters: Iterable[Parameter],
        filters: Dict[str, Any],
        order_by: Optional[str],
        limit: Optional[int],
        offset: int,
    ) -> List[Dict[str, Any]]:
        sql_order = order_by
        if order_by in {"identificator", "-identificator"}:
            sql_order = "-updated_at"
        queryset = self.sql_repo.list_records(
            obj,
            filters=filters,
            order_by=sql_order,
            limit=limit,
            offset=offset,
        )
        ident_param = next((param for param in parameters if param.identificator), None)
        payload: List[Dict[str, Any]] = []
        for record in queryset:
            row = self.sql_repo.serialise_record_to_legacy(record, parameters)
            ident_value = ""
            if ident_param is not None:
                field_payload = row.get(str(ident_param.id), {})
                if isinstance(field_payload, Mapping):
                    ident_value = str(field_payload.get("value", "") or "")
            payload.append(
                {
                    "id": record.record_uid,
                    "legacy_id": record.legacy_id_to_connect or record.record_uid,
                    "record_uid": record.record_uid,
                    "param_ident": ident_value,
                }
            )
        if order_by == "identificator":
            payload = sorted(payload, key=lambda item: str(item.get("param_ident", "")))
        elif order_by == "-identificator":
            payload = sorted(payload, key=lambda item: str(item.get("param_ident", "")), reverse=True)
        return payload

    def _log_shadow_diff(
        self,
        *,
        obj: Object,
        identifier: str,
        sql_record: Mapping[str, Any],
        file_record: Mapping[str, Any],
    ) -> None:
        sql_norm = self._normalise_for_compare(sql_record)
        file_norm = self._normalise_for_compare(file_record)
        if sql_norm == file_norm:
            return
        self._log_event(
            "dual_read_diff",
            object_id=obj.id,
            record_uid=str(identifier),
            sql=sql_norm,
            file=file_norm,
        )

    @staticmethod
    def _normalise_field_value(parameter: Parameter, raw_value: Any) -> Any:
        canonical = canonicalize_value(
            parameter.data_type,
            raw_value,
            array_separator=parameter.array_separator,
            date_format=parameter.date_format,
        )
        if parameter.data_type == "ARRAY":
            return canonical if isinstance(canonical, list) else []
        if canonical is None:
            return ""
        return canonical

    @staticmethod
    def _normalise_for_compare(
        record: Mapping[str, Any],
        *,
        schema: Optional[Mapping[str, Mapping[str, Any]]] = None,
    ) -> Dict[str, Any]:
        schema_map = dict(schema or {})
        dto = legacy_record_to_dto(record, schema=schema_map, canonicalize=True)
        return canonicalize_record(serialise_record_dto(dto), schema_map)

    def _log_event(self, event: str, **payload: Any) -> None:
        if self.logger is None:
            return
        self.logger.warning("%s %s", event, json.dumps(payload, ensure_ascii=False, default=str))

    @staticmethod
    def _extract_ident_value(legacy_record: Mapping[str, Any], ident_param: Optional[Parameter]) -> str:
        if ident_param is None:
            return ""
        raw = legacy_record.get(str(ident_param.id), {})
        if isinstance(raw, Mapping):
            value = raw.get("value", "")
        else:
            value = raw
        return "" if value is None else str(value)

class ObjectSchemaService:
    """
    Application service for object schema and relation management.
    """

    def __init__(
        self,
        *,
        data_service: Optional[ObjectDataService] = None,
        file_repo: Optional[FileRecordRepository] = None,
    ):
        self.data_service = data_service or ObjectDataService()
        self.file_repo = file_repo or self.data_service.file_repo

    @staticmethod
    def build_schema(obj: Object, parameters: Iterable[Parameter]) -> Dict[str, Any]:
        payload: Dict[str, Any] = {}
        for parameter in parameters:
            category_name = ""
            if parameter.category is not None:
                category_name = str(parameter.category.name or "")
            payload[str(parameter.id)] = {
                "type": parameter.data_type,
                "name": parameter.name,
                "order": parameter.order,
                "category": category_name,
                "identificator": bool(parameter.identificator),
                "linked_object_id": parameter.linked_object_id,
                "array_separator": parameter.array_separator,
                "date_format": parameter.date_format,
            }
        return {
            "object_id": obj.id,
            "parameters": payload,
        }

    def get_object_overview(
        self,
        *,
        obj: Object,
        limit: Optional[int] = None,
        offset: int = 0,
        order_by: str = "-updated_at",
    ) -> Dict[str, Any]:
        from document.models import DocumentPattern_Objects

        parameters = list(Parameter.objects.filter(object=obj).select_related("category").order_by("id"))
        records = self.data_service.list_records(
            obj=obj,
            parameters=parameters,
            order_by=order_by,
            limit=limit,
            offset=offset,
            file_loader=lambda: self._load_idents_from_file(rows_obj=obj, parameters=parameters),
        )
        child_params: Dict[int, Dict[int, str]] = {}
        for link in Object_ParentObject.objects.filter(parent_object=obj):
            child_obj = link.object
            if child_obj.id in child_params:
                continue
            child_params[child_obj.id] = {
                parameter.id: parameter.name
                for parameter in Parameter.objects.filter(object=child_obj)
            }
        return {
            "object": obj,
            "schema": self.build_schema(obj, parameters),
            "parameters": parameters,
            "idents": records,
            "documents": [doc.document for doc in DocumentPattern_Objects.objects.filter(object=obj)],
            "documents_json": [
                {doc.document.id: doc.document.name}
                for doc in DocumentPattern_Objects.objects.filter(object=obj)
            ],
            "child_params": child_params,
        }

    @staticmethod
    def _normalise_meta_code(raw_code: str) -> str:
        code = str(raw_code or "").strip().upper()
        code = re.sub(r"[^A-Z0-9_]+", "_", code)
        code = re.sub(r"_+", "_", code).strip("_")
        return code or "LINK"

    @staticmethod
    def _next_unique_code(*, parent_object: Object, base_code: str, exclude_meta_id: Optional[int] = None) -> str:
        existing_qs = ObjectLinkMeta.objects.filter(parent_object=parent_object)
        if exclude_meta_id is not None:
            existing_qs = existing_qs.exclude(id=exclude_meta_id)
        existing_codes = set(existing_qs.values_list("code", flat=True))
        candidate = base_code
        suffix = 2
        while candidate in existing_codes:
            candidate = f"{base_code}_{suffix}"
            suffix += 1
        return candidate

    @staticmethod
    def _next_unique_display_name(
        *,
        parent_object: Object,
        base_display: str,
        exclude_meta_id: Optional[int] = None,
    ) -> str:
        existing_qs = ObjectLinkMeta.objects.filter(parent_object=parent_object)
        if exclude_meta_id is not None:
            existing_qs = existing_qs.exclude(id=exclude_meta_id)
        existing_display = set(existing_qs.values_list("display_name", flat=True))
        candidate = str(base_display or "").strip() or "Связь"
        suffix = 2
        while candidate in existing_display:
            candidate = f"{base_display} ({suffix})"
            suffix += 1
        return candidate

    @staticmethod
    def _build_relation(parent_object: Object, child_object: Object, *, link_type: str) -> Object_ParentObject:
        relation, relation_created = Object_ParentObject.objects.get_or_create(
            parent_object=parent_object,
            object=child_object,
            defaults={"link_type": link_type},
        )
        if not relation_created and relation.link_type != link_type:
            relation.link_type = link_type
            relation.save(update_fields=["link_type"])
        return relation

    @staticmethod
    def _link_parameter_name(display_name: str) -> str:
        cleaned = str(display_name or "").strip()
        return f"Связь: {cleaned or 'Связь'}"

    @staticmethod
    def _link_parameter_type(link_type: str) -> str:
        return "ARRAY" if str(link_type or "single").strip().lower() == "multiple" else "TXTS"

    @staticmethod
    def _normalise_text(value: Any) -> str:
        return str(value or "").strip().lower()

    def _find_legacy_link_parameter_candidate(
        self,
        *,
        parent_object: Object,
        child_object: Object,
        display_name: str,
        exclude_link_meta_id: Optional[int] = None,
    ) -> Optional[Parameter]:
        if _legacy_linked_params_disabled():
            return None
        queryset = Parameter.objects.filter(
            object=parent_object,
            link_meta__isnull=True,
        )
        if exclude_link_meta_id is not None:
            queryset = queryset.exclude(link_meta_id=exclude_link_meta_id)
        by_linked_object = list(queryset.filter(linked_object=child_object).order_by("id"))
        if len(by_linked_object) == 1:
            return by_linked_object[0]
        child_name_norm = self._normalise_text(getattr(child_object, "name", ""))
        display_norm = self._normalise_text(display_name)
        heuristic: List[Parameter] = []
        for parameter in queryset.order_by("id"):
            name_norm = self._normalise_text(parameter.name)
            if not name_norm:
                continue
            is_legacy_link = name_norm.startswith("связь с ")
            if name_norm == display_norm:
                heuristic.append(parameter)
                continue
            if name_norm == self._normalise_text(self._link_parameter_name(display_name)):
                heuristic.append(parameter)
                continue
            if child_name_norm and child_name_norm in name_norm and (is_legacy_link or "связ" in name_norm):
                heuristic.append(parameter)
        if len(heuristic) == 1:
            return heuristic[0]
        return None

    def _parameter_has_multiple_values(self, *, parameter: Parameter) -> bool:
        for value_json in ParameterValue.objects.filter(parameter=parameter).values_list("value_json", flat=True):
            if isinstance(value_json, list) and len([item for item in value_json if str(item).strip()]) > 1:
                return True
        rows, _warnings = self.file_repo.list_raw_rows(parameter.object, allow_empty=True, ensure_record_uid=True, persist=False)
        key = str(parameter.id)
        separator = parameter.array_separator or " "
        for row in rows:
            raw_value = row.get(key, "")
            if isinstance(raw_value, list):
                if len([item for item in raw_value if str(item).strip()]) > 1:
                    return True
                continue
            text_value = str(raw_value or "").strip()
            if not text_value:
                continue
            parts = [item.strip() for item in text_value.split(separator) if item and item.strip()]
            if len(parts) > 1:
                return True
        return False

    def _sync_sql_parameter_values_for_type(self, *, parameter: Parameter, old_type: str, new_type: str) -> None:
        old_type_norm = str(old_type or "").strip().upper()
        new_type_norm = str(new_type or "").strip().upper()
        if old_type_norm == new_type_norm:
            return
        values_qs = ParameterValue.objects.filter(parameter=parameter)
        if old_type_norm == "TXTS" and new_type_norm == "ARRAY":
            for value in values_qs:
                raw_text = str(value.value_text or "").strip()
                value.value_json = [raw_text] if raw_text else []
                value.value_text = None
                value.value_int = None
                value.value_datetime = None
                value.save(update_fields=["value_json", "value_text", "value_int", "value_datetime"])
            return
        if old_type_norm == "ARRAY" and new_type_norm == "TXTS":
            for value in values_qs:
                data = value.value_json
                if isinstance(data, list):
                    first = ""
                    for item in data:
                        text = str(item or "").strip()
                        if text:
                            first = text
                            break
                    value.value_text = first
                else:
                    value.value_text = ""
                value.value_json = None
                value.value_int = None
                value.value_datetime = None
                value.save(update_fields=["value_json", "value_text", "value_int", "value_datetime"])

    def _ensure_link_parameter_for_meta(
        self,
        *,
        meta: ObjectLinkMeta,
    ) -> Parameter:
        managed = (
            Parameter.objects.filter(link_meta=meta)
            .order_by("-is_managed_link_param", "id")
            .first()
        )
        was_bound_to_meta = managed is not None
        desired_name = self._link_parameter_name(meta.display_name)
        desired_type = self._link_parameter_type(meta.link_type)
        if managed is None:
            managed = self._find_legacy_link_parameter_candidate(
                parent_object=meta.parent_object,
                child_object=meta.child_object,
                display_name=meta.display_name,
                exclude_link_meta_id=meta.id,
            )
            if managed is None:
                max_order = (
                    Parameter.objects.filter(object=meta.parent_object)
                    .aggregate(max_order=Max("order"))
                    .get("max_order")
                ) or 0
                managed = Parameter.objects.create(
                    object=meta.parent_object,
                    name=desired_name,
                    data_type=desired_type,
                    identificator=False,
                    array_separator=" ",
                    linked_object=meta.child_object,
                    order=int(max_order) + 1,
                    category=None,
                    link_meta=meta,
                    is_managed_link_param=True,
                )
                self._ensure_parent_dataframe_column(meta.parent_object, managed.id)
                return managed

        if was_bound_to_meta and not managed.is_managed_link_param:
            managed.link_meta = meta
            managed.linked_object = meta.child_object
            managed.save(update_fields=["link_meta", "linked_object"])
            self._ensure_parent_dataframe_column(meta.parent_object, managed.id)
            return managed

        old_type = managed.data_type
        managed.link_meta = meta
        managed.linked_object = meta.child_object
        managed.is_managed_link_param = True
        managed.data_type = desired_type
        managed.array_separator = managed.array_separator or " "
        managed.name = desired_name
        managed.save(
            update_fields=[
                "link_meta",
                "linked_object",
                "is_managed_link_param",
                "data_type",
                "array_separator",
                "name",
            ]
        )
        self._ensure_parent_dataframe_column(meta.parent_object, managed.id)
        self._sync_sql_parameter_values_for_type(
            parameter=managed,
            old_type=old_type,
            new_type=managed.data_type,
        )
        return managed

    def _graph_has_cycle(
        self,
        *,
        parent_object_id: int,
        child_object_id: int,
        exclude_meta_id: Optional[int] = None,
    ) -> bool:
        if parent_object_id == child_object_id:
            return True
        adjacency: Dict[int, set[int]] = {}
        for from_id, to_id in Object_ParentObject.objects.values_list("parent_object_id", "object_id"):
            adjacency.setdefault(int(from_id), set()).add(int(to_id))
        meta_qs = ObjectLinkMeta.objects.all()
        if exclude_meta_id is not None:
            meta_qs = meta_qs.exclude(id=exclude_meta_id)
        for from_id, to_id in meta_qs.values_list("parent_object_id", "child_object_id"):
            adjacency.setdefault(int(from_id), set()).add(int(to_id))

        visited: set[int] = set()
        stack: List[int] = [int(child_object_id)]
        target = int(parent_object_id)
        while stack:
            node = stack.pop()
            if node == target:
                return True
            if node in visited:
                continue
            visited.add(node)
            for next_node in adjacency.get(node, set()):
                if next_node not in visited:
                    stack.append(next_node)
        return False

    def list_link_meta(self, *, parent_object: Object) -> List[ObjectLinkMeta]:
        return list(
            ObjectLinkMeta.objects.filter(parent_object=parent_object)
            .select_related("child_object", "object_link")
            .order_by("order", "id")
        )

    def create_link_meta(
        self,
        *,
        parent_object: Object,
        child_object_id: int,
        code: str,
        display_name: str,
        link_type: str = "single",
        order: int = 0,
    ) -> ObjectLinkMeta:
        child_object = Object.objects.filter(id=int(child_object_id)).first()
        if child_object is None:
            raise ValueError("Child object not found.")
        link_type_value = str(link_type or "single").strip().lower()
        if link_type_value not in {"single", "multiple"}:
            raise ValueError("Invalid link_type. Allowed values: single, multiple.")
        if self._graph_has_cycle(
            parent_object_id=parent_object.id,
            child_object_id=child_object.id,
        ):
            raise ValueError("Schema cycle detected for the requested link.")

        normalised_code = self._normalise_meta_code(code)
        normalised_code = self._next_unique_code(parent_object=parent_object, base_code=normalised_code)
        normalised_display = self._next_unique_display_name(
            parent_object=parent_object,
            base_display=str(display_name or "").strip() or f"Связь с {child_object.name}",
        )
        relation = self._build_relation(parent_object, child_object, link_type=link_type_value)
        meta = ObjectLinkMeta.objects.create(
            parent_object=parent_object,
            child_object=child_object,
            object_link=relation,
            code=normalised_code,
            display_name=normalised_display,
            link_type=link_type_value,
            order=int(order or 0),
        )
        self._ensure_link_parameter_for_meta(meta=meta)
        return meta

    def update_link_meta(
        self,
        *,
        parent_object: Object,
        meta: ObjectLinkMeta,
        code: Optional[str] = None,
        display_name: Optional[str] = None,
        child_object_id: Optional[int] = None,
        link_type: Optional[str] = None,
        order: Optional[int] = None,
    ) -> ObjectLinkMeta:
        if meta.parent_object_id != parent_object.id:
            raise ValueError("Link meta does not belong to this parent object.")
        if child_object_id is not None:
            child_object = Object.objects.filter(id=int(child_object_id)).first()
            if child_object is None:
                raise ValueError("Child object not found.")
        else:
            child_object = meta.child_object

        link_type_value = (link_type or meta.link_type or "single").strip().lower()
        if link_type_value not in {"single", "multiple"}:
            raise ValueError("Invalid link_type. Allowed values: single, multiple.")
        if self._graph_has_cycle(
            parent_object_id=parent_object.id,
            child_object_id=child_object.id,
            exclude_meta_id=meta.id,
        ):
            raise ValueError("Schema cycle detected for the requested link.")

        next_code = meta.code
        if code is not None:
            next_code = self._next_unique_code(
                parent_object=parent_object,
                base_code=self._normalise_meta_code(code),
                exclude_meta_id=meta.id,
            )
        next_display_name = meta.display_name
        if display_name is not None:
            next_display_name = self._next_unique_display_name(
                parent_object=parent_object,
                base_display=str(display_name or "").strip() or meta.display_name,
                exclude_meta_id=meta.id,
            )
        if meta.link_type == "multiple" and link_type_value == "single":
            parameter = Parameter.objects.filter(link_meta=meta, is_managed_link_param=True).first()
            if parameter is not None and self._parameter_has_multiple_values(parameter=parameter):
                raise ValueError(
                    "Нельзя изменить тип связи multiple->single: найдены записи с несколькими значениями."
                )
        relation = self._build_relation(parent_object, child_object, link_type=link_type_value)
        meta.child_object = child_object
        meta.object_link = relation
        meta.code = next_code
        meta.display_name = next_display_name
        meta.link_type = link_type_value
        if order is not None:
            meta.order = int(order)
        meta.save(
            update_fields=[
                "child_object",
                "object_link",
                "code",
                "display_name",
                "link_type",
                "order",
                "updated_at",
            ]
        )
        self._ensure_link_parameter_for_meta(meta=meta)
        return meta

    @staticmethod
    def delete_link_meta(*, parent_object: Object, meta: ObjectLinkMeta) -> Dict[str, Any]:
        if meta.parent_object_id != parent_object.id:
            raise ValueError("Link meta does not belong to this parent object.")
        usage_count_legacy = ObjectLink_identificators.objects.filter(object_link_meta=meta).count()
        usage_count_sql = RecordLink.objects.filter(object_link_meta=meta).count()
        usage_count = int(usage_count_legacy + usage_count_sql)
        Parameter.objects.filter(link_meta=meta).update(
            link_meta=None,
            is_managed_link_param=False,
        )
        ObjectLink_identificators.objects.filter(object_link_meta=meta).delete()
        RecordLink.objects.filter(object_link_meta=meta).delete()
        deleted, _ = ObjectLinkMeta.objects.filter(id=meta.id, parent_object=parent_object).delete()
        return {
            "deleted": int(deleted),
            "usage_count": usage_count,
            "cleaned_legacy_links": int(usage_count_legacy),
            "cleaned_sql_links": int(usage_count_sql),
        }

    def add_object_links(self, *, parent_object: Object, child_ids: Iterable[str]) -> Dict[str, Any]:
        links_payload: List[Dict[str, Any]] = []
        for child_id_str in child_ids:
            try:
                child_id = int(child_id_str)
            except (TypeError, ValueError):
                continue
            if child_id == parent_object.id:
                continue
            child_obj = Object.objects.filter(id=child_id).first()
            if child_obj is None:
                continue
            if self._graph_has_cycle(parent_object_id=parent_object.id, child_object_id=child_obj.id):
                continue
            link, created = Object_ParentObject.objects.get_or_create(parent_object=parent_object, object=child_obj)
            created_param = None
            default_code = self._next_unique_code(
                parent_object=parent_object,
                base_code=self._normalise_meta_code(f"LINK_{child_obj.id}"),
            )
            default_display = self._next_unique_display_name(
                parent_object=parent_object,
                base_display=f"Связь с {child_obj.name}",
            )
            link_meta = (
                ObjectLinkMeta.objects.filter(
                    parent_object=parent_object,
                    object_link=link,
                    child_object=child_obj,
                )
                .order_by("order", "id")
                .first()
            )
            if link_meta is None:
                link_meta = ObjectLinkMeta.objects.create(
                    parent_object=parent_object,
                    object_link=link,
                    child_object=child_obj,
                    code=default_code,
                    display_name=default_display,
                    link_type=link.link_type,
                    order=0,
                )
            link_param = self._ensure_link_parameter_for_meta(meta=link_meta)
            if created:
                created_param = link_param
            payload = {
                "id": link.id,
                "link_meta_id": link_meta.id,
                "link_meta_code": link_meta.code,
                "link_meta_display_name": link_meta.display_name,
                "child_name": link.object.name,
                "link_parameter_id": link_param.id,
            }
            if link_param is not None:
                payload["param"] = {
                    "id": link_param.id,
                    "name": link_param.name,
                    "data_type": link_param.data_type,
                    "linked_object_id": link_param.linked_object_id,
                    "link_meta_id": link_param.link_meta_id,
                    "is_managed_link_param": bool(link_param.is_managed_link_param),
                }
            links_payload.append(payload)
        return {"links": links_payload}

    def _ensure_parent_dataframe_column(self, parent_object: Object, parameter_id: int) -> None:
        df, _ = self.file_repo.load_dataframe(parent_object, allow_empty=True)
        if df is None:
            import pandas as pd  # local import to avoid heavy import at module load
            df = pd.DataFrame({"id_to_connect": [], "record_uid": []})
        df, _ = self.file_repo._legacy_views._ensure_record_uid_column(parent_object, df, persist=False)
        if str(parameter_id) not in df.columns and parameter_id not in df.columns:
            df[str(parameter_id)] = ""
        self.file_repo.save_dataframe(parent_object, df)

    def _load_idents_from_file(self, *, rows_obj: Object, parameters: List[Parameter]) -> List[Dict[str, Any]]:
        rows, _ = self.file_repo.list_raw_rows(rows_obj, allow_empty=True, ensure_record_uid=True, persist=False)
        ident_param = next((parameter for parameter in parameters if parameter.identificator), None)
        if ident_param is None:
            return []
        ident_key = str(ident_param.id)
        payload: List[Dict[str, Any]] = []
        for row in rows:
            row_id = str(row.get("record_uid") or row.get("id_to_connect") or "").strip()
            if not row_id:
                continue
            ident_value = str(row.get(ident_key) or "").strip()
            payload.append({"id": row_id, "param_ident": ident_value})
        return list(reversed(payload))


class ObjectLinkService:
    """
    Service for keeping legacy row links and SQL row links in sync.
    """

    def __init__(self, *, data_service: Optional[ObjectDataService] = None):
        self.data_service = data_service or ObjectDataService()
        self.sql_repo = self.data_service.sql_repo

    @staticmethod
    def _default_meta_for_relation(relation: Object_ParentObject) -> Optional[ObjectLinkMeta]:
        return (
            ObjectLinkMeta.objects.filter(object_link=relation)
            .order_by("order", "id")
            .first()
        )

    def _ensure_meta_for_relation(self, relation: Object_ParentObject) -> ObjectLinkMeta:
        existing = self._default_meta_for_relation(relation)
        if existing is not None:
            ObjectSchemaService(data_service=self.data_service)._ensure_link_parameter_for_meta(meta=existing)
            return existing
        if not _legacy_link_meta_bridge_enabled():
            self.data_service._log_event(
                "legacy_link_meta_bridge_disabled",
                parent_object_id=relation.parent_object_id,
                child_object_id=relation.object_id,
                relation_id=relation.id,
            )
            raise ValueError("Legacy relation->meta bridge disabled by DBM_UI_LEGACY_FALLBACK=0.")
        # TODO(dbm-cutover): remove legacy auto-bootstrap once all relations
        # are managed only through explicit ObjectLinkMeta CRUD.
        self.data_service._log_event(
            "legacy_link_meta_fallback_hit",
            parent_object_id=relation.parent_object_id,
            child_object_id=relation.object_id,
            relation_id=relation.id,
        )
        base_code = f"LINK_{relation.object_id}"
        code = base_code
        suffix = 2
        existing_codes = set(
            ObjectLinkMeta.objects.filter(parent_object_id=relation.parent_object_id).values_list("code", flat=True)
        )
        while code in existing_codes:
            code = f"{base_code}_{suffix}"
            suffix += 1
        base_display = f"Связь с {relation.object.name}"
        display_name = base_display
        suffix = 2
        existing_display = set(
            ObjectLinkMeta.objects.filter(parent_object_id=relation.parent_object_id).values_list("display_name", flat=True)
        )
        while display_name in existing_display:
            display_name = f"{base_display} ({suffix})"
            suffix += 1
        created = ObjectLinkMeta.objects.create(
            parent_object=relation.parent_object,
            child_object=relation.object,
            object_link=relation,
            code=code,
            display_name=display_name,
            link_type=relation.link_type,
            order=0,
        )
        ObjectSchemaService(data_service=self.data_service)._ensure_link_parameter_for_meta(meta=created)
        return created

    def _resolve_link_meta_for_parent(self, *, parent_obj: Object, link_meta_id: int) -> ObjectLinkMeta:
        meta = (
            ObjectLinkMeta.objects.filter(id=int(link_meta_id))
            .select_related("child_object", "object_link", "parent_object")
            .first()
        )
        if meta is None:
            raise ValueError("Связь-мета не найдена.")
        if meta.parent_object_id != parent_obj.id:
            raise ValueError("Указанная связь-мета не принадлежит текущему родительскому объекту.")
        return meta

    def create_row_link(
        self,
        *,
        parent_obj: Object,
        parent_identifier: str,
        link_meta_id: int,
        child_identifier: str,
    ) -> Dict[str, Any]:
        meta = self._resolve_link_meta_for_parent(parent_obj=parent_obj, link_meta_id=int(link_meta_id))
        parent_uid = self.data_service.resolve_record_uid_from_identifier(
            obj=parent_obj,
            identifier=str(parent_identifier),
        )
        child_uid = self.data_service.resolve_record_uid_from_identifier(
            obj=meta.child_object,
            identifier=str(child_identifier),
        )
        if meta.link_type == "single":
            ObjectLink_identificators.objects.update_or_create(
                object_link=meta.object_link,
                object_link_meta=meta,
                parent_object_identificator=parent_uid,
                defaults={"object_identificator": child_uid},
            )
        else:
            ObjectLink_identificators.objects.update_or_create(
                object_link=meta.object_link,
                object_link_meta=meta,
                parent_object_identificator=parent_uid,
                object_identificator=child_uid,
            )
        self.sync_parent_row_links(parent_obj=parent_obj, parent_identifier=parent_uid)
        return {
            "parent_uid": parent_uid,
            "child_uid": child_uid,
            "link_meta_id": meta.id,
        }

    def delete_row_link(
        self,
        *,
        parent_obj: Object,
        parent_identifier: str,
        link_meta_id: int,
        child_identifier: str,
    ) -> Dict[str, Any]:
        meta = self._resolve_link_meta_for_parent(parent_obj=parent_obj, link_meta_id=int(link_meta_id))
        parent_uid = self.data_service.resolve_record_uid_from_identifier(
            obj=parent_obj,
            identifier=str(parent_identifier),
        )
        child_uid = self.data_service.resolve_record_uid_from_identifier(
            obj=meta.child_object,
            identifier=str(child_identifier),
        )
        deleted, _ = ObjectLink_identificators.objects.filter(
            object_link=meta.object_link,
            object_link_meta=meta,
            parent_object_identificator=parent_uid,
            object_identificator=child_uid,
        ).delete()
        self.sync_parent_row_links(parent_obj=parent_obj, parent_identifier=parent_uid)
        return {
            "deleted": int(deleted),
            "parent_uid": parent_uid,
            "child_uid": child_uid,
            "link_meta_id": meta.id,
        }

    def _row_links_for_meta(
        self,
        *,
        meta: ObjectLinkMeta,
        parent_ident_candidates: Iterable[str],
    ):
        candidate_list = [str(item) for item in parent_ident_candidates]
        queryset = ObjectLink_identificators.objects.filter(
            object_link=meta.object_link,
            parent_object_identificator__in=candidate_list,
        )
        default_meta = self._default_meta_for_relation(meta.object_link)
        if default_meta is not None and default_meta.id == meta.id:
            return queryset.filter(Q(object_link_meta=meta) | Q(object_link_meta__isnull=True))
        return queryset.filter(object_link_meta=meta)

    def sync_parent_row_links(self, *, parent_obj: Object, parent_identifier: str) -> None:
        if not self.data_service._sql_write_enabled():
            return
        strict_mode = getattr(settings, "DBM_DUAL_WRITE_STRICT_FOR_TESTS", False)
        sql_primary = self.data_service._sql_source_of_truth_enabled()
        try:
            parent_identifier_str = str(parent_identifier)
            parent_uid = self.data_service.resolve_record_uid_from_identifier(
                obj=parent_obj,
                identifier=parent_identifier_str,
            )
            parent_record = self.sql_repo.upsert_record(
                obj=parent_obj,
                record_uid=parent_uid,
                fields={},
                legacy_id_to_connect=parent_identifier_str,
            )
            parent_ident_candidates = {parent_identifier_str, parent_uid}
            if parent_record.legacy_id_to_connect:
                parent_ident_candidates.add(str(parent_record.legacy_id_to_connect))
            self.sql_repo.delete_links_for_parent_record(parent_record)
            metas = (
                ObjectLinkMeta.objects.filter(parent_object=parent_obj)
                .select_related("child_object", "object_link")
                .order_by("order", "id")
            )
            if not metas.exists() and _legacy_link_meta_bridge_enabled():
                relations = Object_ParentObject.objects.filter(parent_object=parent_obj).select_related("object")
                for relation in relations:
                    try:
                        self._ensure_meta_for_relation(relation)
                    except ValueError:
                        continue
                metas = (
                    ObjectLinkMeta.objects.filter(parent_object=parent_obj)
                    .select_related("child_object", "object_link")
                    .order_by("order", "id")
                )
            for meta in metas:
                row_links = self._row_links_for_meta(
                    meta=meta,
                    parent_ident_candidates=parent_ident_candidates,
                )
                for row_link in row_links:
                    child_identifier = str(row_link.object_identificator)
                    child_uid = self.data_service.resolve_record_uid_from_identifier(
                        obj=meta.child_object,
                        identifier=child_identifier,
                    )
                    child_record = self.sql_repo.upsert_record(
                        obj=meta.child_object,
                        record_uid=child_uid,
                        fields={},
                        legacy_id_to_connect=child_identifier,
                    )
                    self.sql_repo.upsert_link(
                        meta.object_link,
                        parent_record,
                        child_record,
                        object_link_meta=meta,
                    )
        except Exception as exc:
            self.data_service._log_event(
                "sql_write_primary_failed" if sql_primary else "dual_write_failed",
                object_id=parent_obj.id,
                record_uid=str(parent_identifier),
                op="sync_links",
                exc=str(exc),
            )
            if strict_mode or sql_primary:
                raise

    def delete_object_link(self, relation: Object_ParentObject) -> None:
        if not self.data_service._sql_write_enabled():
            return
        self.sql_repo.delete_links_for_object_link(relation)

    def get_row_links(self, *, parent_obj: Object, parent_identifier: str) -> List[Dict[str, Any]]:
        parent_uid = self.data_service.resolve_record_uid_from_identifier(
            obj=parent_obj,
            identifier=str(parent_identifier),
        )
        parent_record = self.sql_repo.get_record_by_uid_or_legacy(parent_obj, str(parent_identifier))
        parent_identifiers = {str(parent_identifier), parent_uid}
        if parent_record is not None and parent_record.legacy_id_to_connect:
            parent_identifiers.add(str(parent_record.legacy_id_to_connect))
        payload: List[Dict[str, Any]] = []
        metas = (
            ObjectLinkMeta.objects.filter(parent_object=parent_obj)
            .select_related("child_object", "object_link")
            .order_by("order", "id")
        )
        if not metas.exists() and _legacy_link_meta_bridge_enabled():
            relations = Object_ParentObject.objects.filter(parent_object=parent_obj).select_related("object")
            for relation in relations:
                try:
                    self._ensure_meta_for_relation(relation)
                except ValueError:
                    continue
            metas = (
                ObjectLinkMeta.objects.filter(parent_object=parent_obj)
                .select_related("child_object", "object_link")
                .order_by("order", "id")
            )
        for meta in metas:
            child_ids = sorted(set(str(item) for item in list(
                self._row_links_for_meta(
                    meta=meta,
                    parent_ident_candidates=parent_identifiers,
                ).values_list("object_identificator", flat=True)
            )))
            payload.append(
                {
                    "child_object_id": meta.child_object.id,
                    "child_object_name": meta.child_object.name,
                    "link_type": meta.link_type,
                    "child_ident_ids": child_ids,
                    "link_id": meta.id,
                    "link_code": meta.code,
                    "link_display_name": meta.display_name,
                }
            )
        return payload
