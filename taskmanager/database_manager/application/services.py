import json
import uuid
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Tuple

from django.conf import settings
from django.db.models import Q

from ..domain.normalize import canonicalize_record, canonicalize_value, schema_from_parameters
from ..infrastructure.repositories import FileRecordRepository, SqlRecordRepository
from ..models import Object, ObjectLink_identificators, Object_ParentObject, Parameter, RecordLink
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
    ) -> Dict[str, Any]:
        safe_limit = max(int(limit or 50), 0)
        safe_offset = max(int(offset or 0), 0)
        safe_order = (order or "updated_at").strip().lower()
        query = (q or "").strip().lower()
        can_fallback = self._file_fallback_read_enabled()
        params_list = list(parameters)
        ident_param = next((param for param in params_list if param.identificator), None)

        if getattr(settings, "DBM_READ_FROM_SQL", False):
            try:
                sql_result = self._list_records_v1_from_sql(
                    obj=obj,
                    parameters=params_list,
                    ident_param=ident_param,
                    limit=safe_limit,
                    offset=safe_offset,
                    order=safe_order,
                    query=query,
                )
                if sql_result["total"] > 0:
                    return sql_result
                self._log_event("sql_miss", object_id=obj.id, op="list_records_v1")
                if not can_fallback:
                    return {"records": [], "total": 0, "source": "sql"}
            except Exception as exc:
                self._log_event("sql_miss", object_id=obj.id, op="list_records_v1", exc=str(exc))
                if not can_fallback:
                    return {"records": [], "total": 0, "source": "sql"}
        return self._list_records_v1_from_file(
            obj=obj,
            parameters=params_list,
            ident_param=ident_param,
            limit=safe_limit,
            offset=safe_offset,
            order=safe_order,
            query=query,
        )

    def _list_records_v1_from_sql(
        self,
        *,
        obj: Object,
        parameters: List[Parameter],
        ident_param: Optional[Parameter],
        limit: int,
        offset: int,
        order: str,
        query: str,
    ) -> Dict[str, Any]:
        order_by = "-updated_at"
        if order == "record_uid":
            order_by = "record_uid"
        schema_map = self._schema_from_parameters(parameters)
        queryset = list(self.sql_repo.list_records(obj, order_by=order_by))
        prepared: List[Tuple[str, Dict[str, Any]]] = []
        for record in queryset:
            legacy_record = self.sql_repo.serialise_record_to_legacy(record, parameters)
            if query and not self._record_matches_query(legacy_record, parameters, ident_param, query):
                continue
            dto_payload = serialise_record_dto(
                legacy_record_to_dto(legacy_record, schema=schema_map, canonicalize=True)
            )
            ident_value = self._extract_ident_value(legacy_record, ident_param)
            prepared.append((ident_value, dto_payload))
        if order == "identificator":
            prepared = sorted(prepared, key=lambda item: item[0])
        total = len(prepared)
        paged = prepared[offset: offset + limit] if limit else prepared[offset:]
        return {
            "records": [payload for _, payload in paged],
            "total": total,
            "source": "sql",
        }

    def _list_records_v1_from_file(
        self,
        *,
        obj: Object,
        parameters: List[Parameter],
        ident_param: Optional[Parameter],
        limit: int,
        offset: int,
        order: str,
        query: str,
    ) -> Dict[str, Any]:
        rows, warnings = self.file_repo.list_raw_rows(obj, allow_empty=True, ensure_record_uid=True, persist=False)
        if warnings:
            self._log_event("file_load_warning", object_id=obj.id, warnings=warnings)
        schema_map = self._schema_from_parameters(parameters)
        prepared: List[Tuple[str, Dict[str, Any]]] = []
        for row in rows:
            record_uid = str(row.get("record_uid") or row.get("id_to_connect") or "").strip()
            if not record_uid:
                continue
            legacy_record: Dict[str, Any] = {"id_to_connect": record_uid}
            for parameter in parameters:
                column_key = str(parameter.id)
                raw_value = row.get(column_key, "")
                legacy_record[column_key] = {
                    "data_type": parameter.data_type,
                    "value": self._normalise_field_value(parameter, raw_value),
                }
            if query and not self._record_matches_query(legacy_record, parameters, ident_param, query):
                continue
            dto_payload = serialise_record_dto(
                legacy_record_to_dto(legacy_record, schema=schema_map, canonicalize=True)
            )
            ident_value = self._extract_ident_value(legacy_record, ident_param)
            prepared.append((ident_value, dto_payload))
        if order == "identificator":
            prepared = sorted(prepared, key=lambda item: item[0])
        elif order == "record_uid":
            prepared = sorted(prepared, key=lambda item: item[1].get("record_uid", ""))
        else:
            prepared = list(reversed(prepared))
        total = len(prepared)
        paged = prepared[offset: offset + limit] if limit else prepared[offset:]
        return {
            "records": [payload for _, payload in paged],
            "total": total,
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

    @staticmethod
    def _record_matches_query(
        legacy_record: Mapping[str, Any],
        parameters: Iterable[Parameter],
        ident_param: Optional[Parameter],
        query: str,
    ) -> bool:
        if not query:
            return True
        ident_value = ObjectDataService._extract_ident_value(legacy_record, ident_param).lower()
        if query in ident_value:
            return True
        for parameter in parameters:
            key = str(parameter.id)
            raw = legacy_record.get(key, {})
            value = raw.get("value", "") if isinstance(raw, Mapping) else raw
            if isinstance(value, list):
                candidate = " ".join(str(item) for item in value).lower()
            else:
                candidate = str(value or "").lower()
            if query in candidate:
                return True
        return False


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
            link, created = Object_ParentObject.objects.get_or_create(parent_object=parent_object, object=child_obj)
            created_param = None
            if created:
                data_type = "ARRAY" if link.link_type == "multiple" else "TXTS"
                created_param = Parameter.objects.create(
                    object=parent_object,
                    name=f"Связь с {child_obj.name}",
                    data_type=data_type,
                    linked_object=child_obj,
                    order=0,
                    category=None,
                )
                self._ensure_parent_dataframe_column(parent_object, created_param.id)
            payload = {"id": link.id, "child_name": link.object.name}
            if created_param is not None:
                payload["param"] = {
                    "id": created_param.id,
                    "name": created_param.name,
                    "data_type": created_param.data_type,
                    "linked_object_id": created_param.linked_object_id,
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
            relations = Object_ParentObject.objects.filter(parent_object=parent_obj).select_related("object")
            for relation in relations:
                row_links = ObjectLink_identificators.objects.filter(
                    Q(object_link=relation)
                    & Q(parent_object_identificator__in=list(parent_ident_candidates))
                )
                for row_link in row_links:
                    child_identifier = str(row_link.object_identificator)
                    child_uid = self.data_service.resolve_record_uid_from_identifier(
                        obj=relation.object,
                        identifier=child_identifier,
                    )
                    child_record = self.sql_repo.upsert_record(
                        obj=relation.object,
                        record_uid=child_uid,
                        fields={},
                        legacy_id_to_connect=child_identifier,
                    )
                    self.sql_repo.upsert_link(relation, parent_record, child_record)
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
        for link in Object_ParentObject.objects.filter(parent_object=parent_obj):
            child_ids = list(
                ObjectLink_identificators.objects.filter(
                    object_link=link,
                    parent_object_identificator__in=list(parent_identifiers),
                ).values_list("object_identificator", flat=True)
            )
            payload.append(
                {
                    "child_object_id": link.object.id,
                    "child_object_name": link.object.name,
                    "link_type": link.link_type,
                    "child_ident_ids": child_ids,
                    "link_id": link.id,
                }
            )
        return payload
