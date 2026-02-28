import logging
import contextlib

from django.shortcuts import render, get_object_or_404, redirect
from django.contrib import messages
from django.views.generic import CreateView
from django.contrib.auth import views, models  # noqa: F401  # imported for side effects
from django.contrib.auth.decorators import login_required, permission_required
from django.core.exceptions import PermissionDenied
from django.http import (
    HttpResponse,
    JsonResponse,
    StreamingHttpResponse,
    FileResponse,
    Http404,
    HttpResponseNotModified,
    HttpResponseForbidden,
    HttpResponseBadRequest,
)
from django.views.decorators.http import require_http_methods
from django.urls import reverse_lazy, reverse
from django.conf import settings
from django.db import connection
from django.db.models import Q, Max

from .models import Object, Parameter, Object_ParentObject, ObjectLink_identificators, ParameterCategory
from document.models import DocumentPattern_Objects
from user_manager.models import Profile, Organisation  # noqa: F401  # imported for side effects

import os
import uuid
import json
import pickle
from pathlib import Path
from functools import lru_cache

import pandas as pd
from sqlalchemy.engine import create_engine  # noqa: F401  # imported for side effects
from sqlalchemy import inspect, text  # noqa: F401  # imported for side effects

try:  # Optional encryption support
    from cryptography.fernet import Fernet, InvalidToken
except ImportError:  # pragma: no cover - dependency may be absent
    Fernet = InvalidToken = None

try:  # POSIX file locking
    import fcntl  # type: ignore[attr-defined]
except ImportError:  # pragma: no cover - non-POSIX systems
    fcntl = None  # type: ignore[assignment]

from typing import Any, Dict, List, Optional, Tuple, Union

from .application.services import ObjectDataService, ObjectLinkService, ObjectSchemaService
from .presentation.dto import legacy_record_to_dto, serialise_record_dto

logger = logging.getLogger(__name__)
object_data_service = ObjectDataService(logger=logger)
object_link_service = ObjectLinkService(data_service=object_data_service)
object_schema_service = ObjectSchemaService(data_service=object_data_service)


"""
Views for the database_manager application.

This module reimplements the original views from the upstream project, but
introduces several important improvements around linking objects and rows. In
particular, the old implementation suffered from the following issues:

* Object‑level links were created without checking for duplicates. This could
  lead to multiple `Object_ParentObject` entries representing the same
  relationship.
* Row‑level links between a parent row and a child row were not created at all;
  a placeholder model `ObjectLink_identificators` existed but was never
  populated.
* The variable names around linking were confusing (`child_object_idents` held
  object primary keys, not row identifiers).

The updated views in this module address these shortcomings by:

* Using `get_or_create` when creating object‑level links to avoid duplicates.
* Introducing a new view `save_row_link` that allows clients to create or
  update row‑level links. It uses `update_or_create` on
  `ObjectLink_identificators` to either insert a new mapping or overwrite an
  existing one for the same parent row within the same object link.
* Clarifying parameter names in the linking views.

Aside from these changes, the remainder of the file largely mirrors the
upstream implementation, preserving existing behaviour for uploading CSV files,
editing objects, and managing rows.
"""

# -----------------------------------------------------------------------------
# Helper functions used by multiple views
# -----------------------------------------------------------------------------

_DATASTORE_JSON_MAGIC = b'DFJSON1|'
_DATASTORE_ENCRYPTED_MAGIC = b'DFENC1|'
_DATASTORE_FORMAT_VERSION = 1
_DATASTORE_SUBDIR = Path('dataframes')
_RECORD_UID_COLUMN = 'record_uid'
_LOCKING_SUPPORTED = fcntl is not None

if not _LOCKING_SUPPORTED:
    logger.warning(
        "System-level file locking (fcntl) is unavailable; dataframe writes will not be synchronised across workers."
    )


@contextlib.contextmanager
def _file_lock(path: Path, *, shared: bool):
    """
    Cooperative advisory lock around dataframe files to prevent concurrent writers
    from clobbering data. Falls back to a no-op on platforms without fcntl.
    """
    if not _LOCKING_SUPPORTED:
        yield
        return
    lock_path = path.with_suffix(path.suffix + '.lock')
    try:
        lock_path.parent.mkdir(parents=True, exist_ok=True)
    except FileExistsError:
        pass
    with lock_path.open('a') as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_SH if shared else fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


@lru_cache(maxsize=1)
def _get_fernet():
    key = getattr(settings, 'DATASTORE_ENCRYPTION_KEY', None)
    if not key:
        return None
    if Fernet is None:
        logger.warning(
            "DATASTORE_ENCRYPTION_KEY is set, but the 'cryptography' package is not installed. "
            "Falling back to plaintext JSON."
        )
        return None
    try:
        return Fernet(key)
    except Exception:
        logger.warning(
            "Failed to initialise Fernet with the provided DATASTORE_ENCRYPTION_KEY. "
            "Falling back to plaintext JSON."
        )
        return None


def _get_backup_limit() -> int:
    try:
        limit = int(getattr(settings, 'DATASTORE_BACKUP_LIMIT', 10))
    except (TypeError, ValueError):
        limit = 10
    return max(limit, 0)


def _normalise_storage_name(name: str) -> str:
    if not name:
        return ''
    return name.replace('\\', '/').lstrip('/')


def _ensure_storage_name(file_field, desired_suffix: str = '.json'):
    current = _normalise_storage_name(getattr(file_field, 'name', '') or '')
    changed = False
    if not current:
        current = (_DATASTORE_SUBDIR / f"{uuid.uuid4().hex}{desired_suffix}").as_posix()
        changed = True
    else:
        path_obj = Path(current)
        if path_obj.suffix.lower() != desired_suffix:
            current = path_obj.with_suffix(desired_suffix).as_posix()
            changed = True
    file_field.name = current
    return current, changed


def _build_absolute_path(relative_name: str) -> Path:
    relative_name = _normalise_storage_name(relative_name)
    if not relative_name:
        raise ValueError("Cannot resolve absolute path without a relative file name.")
    return Path(settings.MEDIA_ROOT).joinpath(Path(relative_name))


def _rotate_backups(path: Path) -> None:
    limit = _get_backup_limit()
    if limit <= 0:
        return
    for idx in range(limit, 0, -1):
        src = path.with_suffix(path.suffix + f'.old{idx}')
        dst = path.with_suffix(path.suffix + f'.old{idx + 1}')
        if src.exists():
            if idx == limit:
                src.unlink()
            else:
                src.rename(dst)
    if path.exists():
        path.rename(path.with_suffix(path.suffix + '.old1'))


def _serialise_dataframe(df: pd.DataFrame) -> dict:
    if df is None:
        df = pd.DataFrame()
    serialisable = df.copy()
    serialisable = serialisable.replace({pd.NA: None})
    serialisable = serialisable.where(pd.notnull(serialisable), None)
    serialisable.columns = [str(col) for col in serialisable.columns]
    if serialisable.columns.duplicated().any():
        duplicated_cols = serialisable.columns[serialisable.columns.duplicated()].tolist()
        logger.warning(
            "Duplicate dataframe columns detected during serialisation; keeping first occurrence for: %s",
            ', '.join(duplicated_cols),
        )
        serialisable = serialisable.loc[:, ~serialisable.columns.duplicated()]
    columns = list(serialisable.columns)
    records = []
    for row in serialisable.to_dict(orient='records'):
        records.append({str(k): v for k, v in row.items()})
    return {
        'version': _DATASTORE_FORMAT_VERSION,
        'columns': columns,
        'records': records,
    }


def _serialise_dataframe_to_bytes(df: pd.DataFrame) -> bytes:
    payload = _serialise_dataframe(df)
    json_bytes = json.dumps(payload, ensure_ascii=False, default=str).encode('utf-8')
    fernet = _get_fernet()
    if fernet:
        try:
            encrypted = fernet.encrypt(json_bytes)
        except Exception:
            logger.exception("Failed to encrypt DataFrame payload; writing plaintext JSON instead.")
        else:
            return _DATASTORE_ENCRYPTED_MAGIC + encrypted
    return _DATASTORE_JSON_MAGIC + json_bytes


def _deserialise_dataframe_from_payload(payload: dict) -> pd.DataFrame:
    if not isinstance(payload, dict):
        raise ValueError("Malformed dataframe payload: expected an object at top level.")
    columns = payload.get('columns', [])
    records = payload.get('records', [])
    if columns and not isinstance(columns, list):
        raise ValueError("Malformed dataframe payload: 'columns' must be a list.")
    if not isinstance(records, list):
        raise ValueError("Malformed dataframe payload: 'records' must be a list.")
    df = pd.DataFrame(records)
    if columns:
        ordered_columns = [str(col) for col in columns]
        seen = set()
        deduped_order = []
        for col in ordered_columns:
            if col in seen:
                continue
            seen.add(col)
            deduped_order.append(col)
        ordered_columns = deduped_order
        for column in ordered_columns:
            if column not in df.columns:
                df[column] = pd.NA
        df = df[ordered_columns]
    df = df.where(pd.notnull(df), pd.NA)
    return df


def _load_dataframe_from_path(path: Path):
    with _file_lock(path, shared=True):
        with path.open('rb') as handle:
            raw_bytes = handle.read()
    if not raw_bytes:
        return pd.DataFrame(), 'json'
    try:
        if raw_bytes.startswith(_DATASTORE_ENCRYPTED_MAGIC):
            fernet = _get_fernet()
            if fernet is None:
                raise RuntimeError(
                    "Encrypted datastore encountered but DATASTORE_ENCRYPTION_KEY is not configured or invalid."
                )
            decrypted = fernet.decrypt(raw_bytes[len(_DATASTORE_ENCRYPTED_MAGIC):])
            payload = json.loads(decrypted.decode('utf-8'))
            return _deserialise_dataframe_from_payload(payload), 'json-encrypted'
        if raw_bytes.startswith(_DATASTORE_JSON_MAGIC):
            payload = json.loads(raw_bytes[len(_DATASTORE_JSON_MAGIC):].decode('utf-8'))
            return _deserialise_dataframe_from_payload(payload), 'json'
        if path.suffix.lower() == '.json':
            payload = json.loads(raw_bytes.decode('utf-8'))
            return _deserialise_dataframe_from_payload(payload), 'json'
        data_obj = pickle.loads(raw_bytes)
        return data_obj, 'pickle'
    except InvalidToken as exc:
        raise RuntimeError("Failed to decrypt encrypted datastore.") from exc


def _archive_legacy_file(path: Path) -> None:
    if path.exists():
        with _file_lock(path, shared=False):
            _rotate_backups(path)


def _write_dataframe(file_field, df: pd.DataFrame, *, object_instance=None) -> str:
    relative_name, changed = _ensure_storage_name(file_field, desired_suffix='.json')
    absolute_path = _build_absolute_path(relative_name)
    absolute_path.parent.mkdir(parents=True, exist_ok=True)
    payload = _serialise_dataframe_to_bytes(df)
    with _file_lock(absolute_path, shared=False):
        _rotate_backups(absolute_path)
        with absolute_path.open('wb') as handle:
            handle.write(payload)
    if changed and object_instance is not None and getattr(object_instance, 'pk', None):
        object_instance.save(update_fields=['data'])
    return relative_name


@login_required
@permission_required('database_manager.manage_object_structure', raise_exception=True)
@require_http_methods(['POST'])
def upload_csv_and_get_columns(request):
    """Return semicolon-separated column names for the uploaded CSV file."""
    csv_file = request.FILES['csv_file']
    df = pd.read_csv(csv_file, converters={i: str for i in range(100)})
    return HttpResponse(";".join(str(x) for x in df.columns.tolist()))


@login_required
@permission_required('database_manager.view_object', raise_exception=True)
@require_http_methods(['POST'])
def get_object_parameters(request, pk):
    """Return JSON metadata for the object's parameters."""
    obj = get_object_or_404(Object, pk=pk)
    parameters = sorted(Parameter.objects.filter(object=obj), key=lambda x: x.id)
    result = [{'id': par.id, 'name': par.name, 'identificator': par.identificator} for par in parameters]
    return JsonResponse({'data': result})


# New endpoint: return child links for a given parent object and row identifier
@login_required
@permission_required('database_manager.manage_object_links', raise_exception=True)
@require_http_methods(['POST'])
def get_row_links(request, pk):
    """Return information about linked child rows for the selected identifier."""
    parent_obj = get_object_or_404(Object, pk=pk)
    parent_ident_id = request.POST.get('parent_ident_id')
    if not parent_ident_id:
        return JsonResponse({'links': []})
    links_data = object_link_service.get_row_links(
        parent_obj=parent_obj,
        parent_identifier=str(parent_ident_id),
    )
    return JsonResponse({'links': links_data})


@login_required
@permission_required('database_manager.manage_object_structure', raise_exception=True)
@require_http_methods(['POST'])
def view_data(request):
    """Return an HTML preview of the uploaded CSV file."""
    csv_file = request.FILES['csv_file']
    df = pd.read_csv(csv_file, converters={i: str for i in range(100)})
    drop_column = request.POST.get('drop_column', '-1')
    if drop_column != '-1':
        df = df.dropna(subset=[drop_column])
    return HttpResponse(df.to_html())


@login_required
@permission_required('database_manager.view_object', raise_exception=True)
def object_manager(request):
    """
    Display a list of all objects. If a CSV is posted, behave like view_data
    and return an HTML preview. This is primarily a convenience method for
    legacy templates.
    """
    if request.method == 'POST':
        csv_file = request.FILES['csv_file']
        df = pd.read_csv(csv_file, converters={i: str for i in range(100)})
        drop_column = request.POST.get('drop_column', '-1')
        if drop_column != '-1':
            df = df.dropna(subset=[drop_column])
        return HttpResponse(df.to_html())
    context = {
        'objects': Object.objects.all(),
    }
    return render(request, 'database_manager/object_manager.html', context)


# -----------------------------------------------------------------------------
# Category and grouping helpers
# -----------------------------------------------------------------------------

def _group_parameters_data(parameters_data):
    """
    Internal helper to group a list of parameter data tuples by the
    ``Parameter.category`` field.  Each element in ``parameters_data`` must be
    a tuple whose first element is a ``Parameter`` instance.  The function
    returns a list of tuples ``(category_name, grouped_list)`` sorted by
    category order and then by parameter order.  Parameters with no category
    are grouped under an empty string.

    :param parameters_data: iterable of tuples where the first element is a
      ``Parameter`` instance and subsequent elements are arbitrary data.
    :return: list of (category_name, [tuples]) entries.
    """
    # Build a mapping of category name (or empty) to list of items.
    # Parameters with no category are assigned to an empty key.
    group_map = {}
    for item in parameters_data:
        param = item[0]
        if param.category is not None and param.category.name:
            key = param.category.name
        elif param.category is not None and not param.category.name:
            key = ''
        else:
            key = ''
        group_map.setdefault(key, []).append(item)
    # Sort categories: alphabetical order for non-empty names; empty name last.
    grouped = []
    # Separate named and unnamed categories
    named_categories = sorted([k for k in group_map.keys() if k], key=lambda x: x.lower())
    for cat_name in named_categories:
        items = group_map[cat_name]
        # Sort items by parameter.order then id to preserve user-defined ordering
        items_sorted = sorted(items, key=lambda tup: (tup[0].order, tup[0].id))
        grouped.append((cat_name, items_sorted))
    # Handle items without category
    if '' in group_map:
        items = group_map['']
        items_sorted = sorted(items, key=lambda tup: (tup[0].order, tup[0].id))
        grouped.append(('Вне категории', items_sorted))
    return grouped


@login_required
@permission_required('database_manager.manage_object_links', raise_exception=True)
@require_http_methods(['POST'])
def get_objects_to_connect(request):
    """Return a JSON list of objects that can be linked."""
    payload = [{'id': obj.id, 'name': obj.name} for obj in Object.objects.all()]
    return JsonResponse({'object': payload})


@login_required
@permission_required('database_manager.add_object', raise_exception=True)
@permission_required('database_manager.manage_object_structure', raise_exception=True)
@require_http_methods(['POST'])
def create_new_object(request):
    """
    Create an empty object with the given name. A unique file is created on
    disk to store its eventual DataFrame and the path is persisted in the
    object's `data` field.
    """
    new_object = Object()
    new_object.name = request.POST['name']
    file_id = uuid.uuid4().hex
    # Configure the file name within the media root.  Assigning to
    # ``data.name`` ensures the FileField stores the relative path correctly.
    relative_path = '/'.join(['dataframes', f'{file_id}.json'])
    new_object.data.name = relative_path
    new_object.save()
    # Initialise an empty DataFrame with legacy and stable row identifiers.
    df = pd.DataFrame({"id_to_connect": [], _RECORD_UID_COLUMN: []})
    _write_dataframe(new_object.data, df, object_instance=new_object)
    return JsonResponse({'id': new_object.id})


@login_required
@permission_required('database_manager.view_object', raise_exception=True)
def get_object(request, pk):
    """
    Display data from an object as well as its identifier values and
    associated documents. When called via POST, returns a JSON payload
    instead of rendering the template.
    """
    obj = get_object_or_404(Object, pk=pk)
    warnings: List[str] = []
    requested_limit = request.GET.get('limit')
    requested_offset = request.GET.get('offset')
    requested_order = request.GET.get('order_by')
    try:
        limit = int(requested_limit) if requested_limit else None
    except (TypeError, ValueError):
        limit = None
    try:
        offset = int(requested_offset) if requested_offset else 0
    except (TypeError, ValueError):
        offset = 0
    overview = object_schema_service.get_object_overview(
        obj=obj,
        order_by=requested_order or "-updated_at",
        limit=limit,
        offset=offset,
    )
    if not any(parameter.identificator for parameter in overview['parameters']):
        warnings.append("Для объекта не найден параметр с флагом идентификатора.")
    idents = overview['idents']
    if warnings and request.method != 'POST':
        for warning in warnings:
            messages.warning(request, warning)
    context = {
        'object': overview['object'],
        'parameters': sorted(overview['parameters'], key=lambda x: x.id),
        'idents': idents,
        'documents': overview['documents'],
        'documents_json': overview['documents_json'],
        'child_params': overview['child_params'],
        'warnings': warnings,
    }
    if request.method == 'POST':
        return HttpResponse(json.dumps({
            'object': overview['object'].to_dict(),
            'idents': idents,
            'schema': overview['schema'],
            'documents': overview['documents_json'],
            'warnings': warnings,
        }))
    return render(request, 'database_manager/get_object.html', context)


@login_required
@permission_required('database_manager.view_object', raise_exception=True)
@require_http_methods(['GET', 'POST'])
def api_v1_object_records(request, pk):
    """
    API v1 endpoint for object records collection (list/search/pagination).
    """
    obj = get_object_or_404(Object, pk=pk)
    try:
        limit = int(request.GET.get('limit', 50))
    except (TypeError, ValueError):
        limit = 50
    try:
        offset = int(request.GET.get('offset', 0))
    except (TypeError, ValueError):
        offset = 0
    order = str(request.GET.get('order', 'updated_at') or 'updated_at').strip().lower()
    if order not in {'updated_at', 'record_uid', 'identificator'}:
        order = 'updated_at'
    include_schema_raw = str(request.GET.get('include_schema', '1')).strip().lower()
    include_schema = include_schema_raw not in {'0', 'false', 'no'}
    query = str(request.GET.get('q', '') or '')
    parameters = list(Parameter.objects.filter(object=obj).select_related('category').order_by('id'))
    if request.method == 'POST':
        if not request.user.has_perm('database_manager.manage_object_data'):
            return _v1_error("PERMISSION_DENIED", "Недостаточно прав для создания записи.", status=403)
        payload = _parse_json_request(request)
        if payload is None:
            return _v1_error("VALIDATION_ERROR", "Ожидался JSON payload.", details={"field": "body"})
        parsed = _extract_v1_fields_payload(payload, parameters, allow_partial=False)
        if isinstance(parsed, JsonResponse):
            return parsed
        fields_payload = parsed
        return _api_v1_create_record(obj=obj, parameters=parameters, fields_payload=fields_payload)

    try:
        records_payload = object_data_service.list_records_v1(
            obj=obj,
            parameters=parameters,
            limit=limit,
            offset=offset,
            order=order,
            q=query,
        )
    except Exception as exc:
        logger.exception("Failed to list v1 records for object %s.", obj.id)
        return _v1_error(
            "SERVER_ERROR",
            "Не удалось получить список записей.",
            status=500,
            details={"exc": str(exc)},
        )
    response_payload: Dict[str, Any] = {
        'api_version': 'v1',
        'object_id': obj.id,
        'records': records_payload['records'],
        'page': {
            'limit': max(int(limit), 0),
            'offset': max(int(offset), 0),
            'total': int(records_payload['total']),
        },
    }
    if include_schema:
        response_payload['schema'] = object_schema_service.build_schema(obj, parameters)
    return JsonResponse(response_payload)


@login_required
@permission_required('database_manager.manage_object_links', raise_exception=True)
@require_http_methods(['GET'])
def api_v1_objects_list(request):
    payload = [
        {"id": obj.id, "name": obj.name}
        for obj in Object.objects.all().order_by("name", "id")
    ]
    return JsonResponse(
        {
            "api_version": "v1",
            "objects": payload,
        }
    )


@login_required
@permission_required('database_manager.view_object', raise_exception=True)
@require_http_methods(['GET', 'PATCH', 'DELETE'])
def api_v1_object_record_detail(request, pk, record_uid):
    obj = get_object_or_404(Object, pk=pk)
    parameters = list(Parameter.objects.filter(object=obj).select_related('category').order_by('id'))
    if request.method == 'GET':
        try:
            record_payload = object_data_service.read_record_with_policy(
                obj,
                record_uid,
                parameters,
                build_file_record=lambda: _build_legacy_record_from_file(obj, record_uid, parameters),
            )
        except Exception as exc:
            logger.exception("Failed to fetch v1 record detail for object %s record %s.", obj.id, record_uid)
            return _v1_error(
                "SERVER_ERROR",
                "Не удалось получить запись.",
                status=500,
                details={"exc": str(exc)},
            )
        if record_payload is None:
            return _v1_error("NOT_FOUND", "Запись не найдена.", status=404)
        return JsonResponse(
            {
                "api_version": "v1",
                "object_id": obj.id,
                "schema": object_schema_service.build_schema(obj, parameters),
                "record": _legacy_record_to_v1_record(record_payload, parameters=parameters),
            }
        )

    if request.method == 'PATCH':
        if not request.user.has_perm('database_manager.manage_object_data'):
            return _v1_error("PERMISSION_DENIED", "Недостаточно прав для изменения записи.", status=403)
        payload = _parse_json_request(request)
        if payload is None:
            return _v1_error("VALIDATION_ERROR", "Ожидался JSON payload.", details={"field": "body"})
        parsed = _extract_v1_fields_payload(payload, parameters, allow_partial=True)
        if isinstance(parsed, JsonResponse):
            return parsed
        fields_payload = parsed
        if not fields_payload:
            return _v1_error("VALIDATION_ERROR", "Не переданы поля для обновления.", details={"field": "record.fields"})
        return _api_v1_update_record(
            obj=obj,
            record_uid=str(record_uid),
            parameters=parameters,
            fields_payload=fields_payload,
        )

    # DELETE
    if not request.user.has_perm('database_manager.manage_object_data'):
        return _v1_error("PERMISSION_DENIED", "Недостаточно прав для удаления записи.", status=403)
    return _api_v1_delete_record(obj=obj, record_uid=str(record_uid))


@login_required
@permission_required('database_manager.manage_object_links', raise_exception=True)
@require_http_methods(['GET', 'POST', 'DELETE'])
def api_v1_record_links(request, pk, record_uid):
    obj = get_object_or_404(Object, pk=pk)
    try:
        parent_uid = object_data_service.resolve_record_uid_from_identifier(obj=obj, identifier=str(record_uid))
    except Exception as exc:
        logger.exception("Failed to resolve parent record uid for links API object %s record %s.", obj.id, record_uid)
        return _v1_error(
            "SERVER_ERROR",
            "Не удалось определить запись родительского объекта.",
            status=500,
            details={"exc": str(exc)},
        )
    if request.method == 'GET':
        links_data = object_link_service.get_row_links(parent_obj=obj, parent_identifier=parent_uid)
        return JsonResponse(
            {
                "api_version": "v1",
                "object_id": obj.id,
                "record_uid": parent_uid,
                "links": [
                    {
                        "link_meta_id": link_data["link_id"],
                        "child_object_id": link_data["child_object_id"],
                        "child_object_name": link_data["child_object_name"],
                        "link_type": link_data["link_type"],
                        "child_record_uids": link_data["child_ident_ids"],
                    }
                    for link_data in links_data
                ],
            }
        )

    payload = _parse_json_request(request)
    if payload is None:
        return _v1_error("VALIDATION_ERROR", "Ожидался JSON payload.", details={"field": "body"})
    link_meta_id = payload.get("link_meta_id")
    child_record_uid = str(payload.get("child_record_uid") or "").strip()
    if not link_meta_id:
        return _v1_error("VALIDATION_ERROR", "Не указан link_meta_id.", details={"field": "link_meta_id"})
    if not child_record_uid:
        return _v1_error("VALIDATION_ERROR", "Не указан child_record_uid.", details={"field": "child_record_uid"})
    relation = Object_ParentObject.objects.filter(id=link_meta_id, parent_object=obj).select_related("object").first()
    if relation is None:
        return _v1_error("NOT_FOUND", "Связь объектов не найдена.", status=404)

    child_uid = object_data_service.resolve_record_uid_from_identifier(
        obj=relation.object,
        identifier=child_record_uid,
    )
    if request.method == 'POST':
        if relation.link_type == "single":
            ObjectLink_identificators.objects.update_or_create(
                object_link=relation,
                parent_object_identificator=parent_uid,
                defaults={"object_identificator": child_uid},
            )
        else:
            ObjectLink_identificators.objects.update_or_create(
                object_link=relation,
                parent_object_identificator=parent_uid,
                object_identificator=child_uid,
            )
        object_link_service.sync_parent_row_links(parent_obj=obj, parent_identifier=parent_uid)
        return JsonResponse(
            {
                "api_version": "v1",
                "object_id": obj.id,
                "record_uid": parent_uid,
                "link": {
                    "link_meta_id": relation.id,
                    "child_record_uid": child_uid,
                },
            },
            status=201,
        )

    deleted, _ = ObjectLink_identificators.objects.filter(
        object_link=relation,
        parent_object_identificator=parent_uid,
        object_identificator=child_uid,
    ).delete()
    object_link_service.sync_parent_row_links(parent_obj=obj, parent_identifier=parent_uid)
    return JsonResponse(
        {
            "api_version": "v1",
            "object_id": obj.id,
            "record_uid": parent_uid,
            "deleted": int(deleted),
        }
    )

def _build_legacy_record_from_file(
    obj: Object,
    record_identifier: str,
    parameters: List[Parameter],
) -> Optional[Dict[str, object]]:
    data_obj, warnings = _safe_load_dataframe(obj.data, object_id=obj.pk, object_instance=obj)
    if warnings:
        logger.warning(
            "Warnings while loading data for object %s during row fetch: %s",
            obj.pk,
            "; ".join(warnings),
        )
    if data_obj is None:
        return None
    data_obj, _ = _ensure_record_uid_column(obj, data_obj, persist=False)
    mask = _resolve_row_mask(data_obj, record_identifier)
    if mask is None:
        return None
    data = data_obj.loc[mask]
    if data.empty:
        return None
    row = data.iloc[0]
    row_record_uid = str(row.get(_RECORD_UID_COLUMN) or '').strip()
    row_legacy_id = str(row.get('id_to_connect') or '').strip()
    row_identifier = row_record_uid or row_legacy_id or str(record_identifier)
    parent_identifier_candidates = {row_identifier}
    if row_record_uid:
        parent_identifier_candidates.add(row_record_uid)
    if row_legacy_id:
        parent_identifier_candidates.add(row_legacy_id)
    response_record: Dict[str, object] = {
        'id_to_connect': row_identifier,
    }
    handled_columns = set()
    parent_links = {
        link.object_id: link
        for link in Object_ParentObject.objects.filter(parent_object=obj)
    }
    for param in parameters:
        column_key = _resolve_dataframe_column(data_obj, param.id)
        key_name = str(param.id)
        if column_key is None:
            warnings.append(
                f"Для параметра '{param.name}' (ID {param.id}) отсутствует столбец в данных объекта."
            )
            response_record[key_name] = {'data_type': param.data_type, 'value': ''}
            continue
        handled_columns.add(column_key)
        value = row.get(column_key, '')
        safe_val = value
        try:
            if isinstance(value, float) and (value != value):
                safe_val = ''
            elif str(value).strip().lower() in ['nan', 'none', '<na>', '']:
                safe_val = ''
        except Exception:
            logger.exception(
                "Failed to normalise value for parameter %s in object %s.",
                param.id,
                obj.pk,
            )
        linked_ids: List[str] = []
        if param.linked_object_id:
            link = parent_links.get(param.linked_object_id)
            if link:
                link_qs = ObjectLink_identificators.objects.filter(
                    object_link=link,
                    parent_object_identificator__in=list(parent_identifier_candidates),
                )
                if param.data_type == 'ARRAY':
                    linked_ids = [
                        entry.object_identificator
                        for entry in link_qs
                        if entry.object_identificator
                    ]
                else:
                    entry = link_qs.first()
                    if entry and entry.object_identificator:
                        linked_ids = [entry.object_identificator]
        if param.data_type == 'ARRAY':
            if linked_ids:
                array_value = linked_ids
            else:
                if param.array_separator:
                    raw_items = str(safe_val).split(param.array_separator) if safe_val else []
                elif isinstance(safe_val, (list, tuple, set)):
                    raw_items = safe_val
                else:
                    raw_items = [safe_val] if safe_val else []
                array_value = []
                for item in raw_items:
                    text = str(item).strip()
                    if text and text.lower() not in ['nan', 'none', '<na>']:
                        array_value.append(text)
            response_record[key_name] = {
                'data_type': param.data_type,
                'value': array_value,
            }
        elif param.data_type == 'DATE':
            if safe_val:
                try:
                    parsed_value = param.parse_date(safe_val)
                except Exception:
                    logger.exception(
                        "Failed to parse date value for parameter %s in object %s.",
                        param.id,
                        obj.pk,
                    )
                    parsed_value = ''
                response_record[key_name] = {'data_type': param.data_type, 'value': parsed_value}
            else:
                response_record[key_name] = {'data_type': param.data_type, 'value': ''}
        elif param.data_type == 'TXTS' and linked_ids:
            response_record[key_name] = {
                'data_type': param.data_type,
                'value': linked_ids[0],
            }
        else:
            if linked_ids:
                safe_val = linked_ids[0]
            response_record[key_name] = {
                'data_type': param.data_type,
                'value': safe_val if safe_val is not None else '',
            }

    known_param_keys = {str(p.id) for p in parameters}
    for column_label, value in row.items():
        if column_label in {'id_to_connect', _RECORD_UID_COLUMN}:
            continue
        if column_label in handled_columns:
            continue
        normalised = str(column_label).strip()
        if not normalised or normalised in response_record or normalised in known_param_keys:
            continue
        safe_val = value
        try:
            if isinstance(value, float) and (value != value):
                safe_val = ''
            elif str(value).strip().lower() in ['nan', 'none', '<na>']:
                safe_val = ''
        except Exception:
            logger.exception(
                "Failed to normalise orphan column value for object %s (column=%s).",
                obj.pk,
                column_label,
            )
        response_record[normalised] = {
            'data_type': 'TXT',
            'value': safe_val if safe_val is not None else '',
        }

    import math

    def sanitize(obj):
        if isinstance(obj, float):
            return '' if math.isnan(obj) else obj
        if obj is None:
            return ''
        if isinstance(obj, dict):
            return {k: sanitize(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [sanitize(x) for x in obj]
        return obj

    return sanitize(response_record)


@login_required
@permission_required('database_manager.view_object', raise_exception=True)
def post_data_from_object(request, pk):
    """
    Given an identifier (id_to_connect) return all values for that row.
    The response contains information about each parameter, including its data
    type and appropriately parsed value.
    """
    obj = get_object_or_404(Object, pk=pk)
    if request.method != 'POST':
        return HttpResponseBadRequest("Expected POST request.")
    id_to_connect = request.POST.get("param_ident_id")
    if not id_to_connect:
        logger.warning("Missing param_ident_id in request for object %s.", obj.pk)
        return JsonResponse([], safe=False)
    parameters = list(Parameter.objects.filter(object=obj).select_related('category').order_by('id'))
    safe_record = object_data_service.read_record_with_policy(
        obj,
        id_to_connect,
        parameters,
        build_file_record=lambda: _build_legacy_record_from_file(obj, id_to_connect, parameters),
    )
    if safe_record is None:
        return JsonResponse([], safe=False)
    payload, is_legacy = object_data_service.build_record_response(
        obj,
        safe_record,
        parameters,
        api_version=_resolve_api_version(request),
    )
    if not is_legacy:
        return JsonResponse(payload)
    legacy_response = JsonResponse(payload.get('records', [safe_record]), safe=False)
    legacy_response['Warning'] = '299 - "Legacy format is deprecated; use api_version=v1."'
    return legacy_response


@login_required
@permission_required('database_manager.add_object', raise_exception=True)
@permission_required('database_manager.manage_object_structure', raise_exception=True)
def upload_csv(request):
    """
    Handle the initial creation of an object from a CSV file. Creates a new
    Object, persists its parameters, writes the DataFrame to disk and returns
    a redirect URL to the object's detail page.
    """
    if request.method == 'POST':
        csv_file = request.FILES['csv_file']
        df = pd.read_csv(csv_file, converters={i: str for i in range(100)})
        drop_column = request.POST.get('drop_column', '-1')
        if drop_column != '-1':
            df.dropna(subset=[drop_column], inplace=True)
        # Convert all values to strings and trim whitespace
        df = df.map(lambda x: str(x).strip())
        # Create a unique filename for the data store
        file_id = uuid.uuid4().hex
        relative_name = '/'.join(['dataframes', f'{file_id}.json'])
        # Create Object and save
        obj = Object(name=request.POST['name'], data=relative_name)
        obj.save()
        # Determine which column is the identifier
        ident = request.POST.get('ident_column', df.columns[0])
        col_names = request.POST.getlist('col[]')
        col_types = request.POST.getlist('col_type[]')
        arr_delim = request.POST.getlist('arr_delim[]')
        date_format = request.POST.getlist('date_format[]')
        # Create Parameter objects for each CSV column
        parameters = [
            Parameter(
                object=obj,
                name=col_names[i],
                data_type=col_types[i],
                array_separator=arr_delim[i],
                identificator=(col == ident),
                date_format=date_format[i],
            )
            for i, col in enumerate(df.columns)
        ]
        params = Parameter.objects.bulk_create(parameters)
        df.columns = [str(param.id) for param in params]
        record_uids = [uuid.uuid4().hex for _ in range(df.shape[0])]
        df[_RECORD_UID_COLUMN] = record_uids
        df['id_to_connect'] = record_uids
        _write_dataframe(obj.data, df, object_instance=obj)
        for _, row in df.iterrows():
            object_data_service.dual_write_upsert(
                obj=obj,
                record_identifier=str(row.get('id_to_connect', '')),
                row_data=row.to_dict(),
                parameters=params,
                op='upload_csv',
                record_uid=str(row.get(_RECORD_UID_COLUMN, '')),
                legacy_id_to_connect=str(row.get('id_to_connect', '')),
            )
        return HttpResponse(f'/database/get_object/{obj.id}')
    return render(request, 'database_manager/upload_csv.html')


@login_required
@permission_required('database_manager.manage_object_structure', raise_exception=True)
def delete_param(request, pk):
    """
    Delete a Parameter. Used when modifying an object's schema.
    """
    param = get_object_or_404(Parameter, pk=pk)
    param.delete()
    return HttpResponse(status=200)


@login_required
@permission_required('database_manager.manage_object_structure', raise_exception=True)
def update_object(request, pk):
    """
    Edit the metadata of an existing Object including its name, identifier and
    parameter definitions. Writes the updated DataFrame back to disk.
    """
    obj = get_object_or_404(Object, pk=pk)
    data_obj, load_warnings = _safe_load_dataframe(obj.data, object_id=obj.pk, object_instance=obj, allow_empty=True)
    if load_warnings:
        logger.warning(
            "Warnings while loading data for update_object (object %s): %s",
            obj.pk,
            "; ".join(load_warnings),
        )
        if request.method != 'POST':
            for warning in load_warnings:
                messages.warning(request, warning)
    if data_obj is None:
        data_obj = pd.DataFrame({'id_to_connect': [], _RECORD_UID_COLUMN: []})
    data_obj, _ = _ensure_record_uid_column(obj, data_obj, persist=False)
    if request.method == 'POST':
        changed = int(request.POST.get('changed', '0'))
        if changed == 0:
            return HttpResponse(status=304)
        obj.name = request.POST['name']
        obj.save()
        col_ids = request.POST.getlist('col_ids[]')
        col_names = request.POST.getlist('col[]')
        col_types = request.POST.getlist('col_type[]')
        arr_delim = request.POST.getlist('arr_delim[]')
        date_format = request.POST.getlist('date_format[]')
        identificator_index = request.POST.get('identificator')
        identificator_index = int(identificator_index) if identificator_index is not None else -1
        # Categories and ordering from the form.  Each entry corresponds to a
        # parameter (existing or new).  The order in the list indicates the
        # parameter's position relative to others in the same category.
        col_categories = request.POST.getlist('col_category[]')
        col_order = request.POST.getlist('col_order[]')
        # Track category objects for this object by name
        category_map = {}
        existing_categories = {cat.name: cat for cat in ParameterCategory.objects.filter(object=obj)}
        max_order = ParameterCategory.objects.filter(object=obj).aggregate(Max('order'))['order__max'] or 0
        # Prepopulate category_map with existing categories
        category_map.update(existing_categories)
        # For each unique category name in form, ensure a category object exists
        for cat_name in filter(None, set(col_categories)):
            if cat_name not in category_map:
                max_order += 1
                category_map[cat_name] = ParameterCategory.objects.create(object=obj, name=cat_name, order=max_order)
        # We'll also ensure there is a placeholder entry for empty categories
        category_map[''] = None
        for i, col_id in enumerate(col_ids):
            # Determine the category for this parameter
            cat_name = col_categories[i].strip() if i < len(col_categories) else ''
            category = category_map.get(cat_name, None)
            order_val = col_order[i] if i < len(col_order) else '0'
            order = int(order_val) if order_val.isdigit() else 0
            identificator = i == identificator_index
            if col_id == '-1':
                parameter = Parameter(
                    object=obj,
                    name=col_names[i],
                    data_type=col_types[i],
                    array_separator=arr_delim[i],
                    identificator=identificator,
                    date_format=date_format[i],
                    category=category,
                    order=order,
                )
                parameter.save()
                # Extend DataFrame with new column
                data_obj[str(parameter.id)] = pd.NA
            else:
                parameter = Parameter.objects.get(id=int(col_id))
                parameter.identificator = identificator
                parameter.name = col_names[i]
                parameter.data_type = col_types[i]
                parameter.array_separator = arr_delim[i]
                parameter.date_format = date_format[i]
                parameter.category = category
                parameter.order = order
                parameter.save()
        # Persist DataFrame changes back to the file. The FileField's path
        # handles MEDIA_ROOT internally, so no need for os.path.join.
        _write_dataframe(obj.data, data_obj, object_instance=obj)
        # Update link types for existing object links
        for link in Object_ParentObject.objects.filter(parent_object=obj):
            lt = request.POST.get(f'link_type_{link.id}', None)
            if lt in ['single', 'multiple'] and lt != link.link_type:
                link.link_type = lt
                link.save()
        # Redirect to the object manager after saving changes to avoid a blank page
        return redirect('object_manager')
    parameters_objects = Object_ParentObject.objects.filter(parent_object=obj)
    categories = ParameterCategory.objects.filter(object=obj).order_by('order')
    return render(request, 'database_manager/update_object.html', context={
        'object': obj,
        'objects': Object.objects.all(),
        'parameters': Parameter.objects.filter(object=obj).order_by('category__order', 'order', 'id'),
        'categories': categories,
        # List of child object instances for backwards compatibility
        'parameters_objects': [po.object for po in parameters_objects],
        # Pass full object link instances to allow deletion
        'object_links': parameters_objects,
        'param_names': [p.name for p in Parameter.objects.filter(object=obj)],
        'param_names_json': json.dumps([p.name for p in Parameter.objects.filter(object=obj)]),
    })



def _safe_load_dataframe(file_field, *, object_id=None, object_instance=None, allow_empty=False):
    """
    Safely load a pandas DataFrame from a Django FileField.

    Returns a tuple ``(dataframe_or_none, warnings_list)``.  When ``allow_empty`` is
    True, the function falls back to an empty DataFrame with an ``id_to_connect``
    column if the file cannot be read.
    """
    warnings = []
    fallback_value = pd.DataFrame({'id_to_connect': [], _RECORD_UID_COLUMN: []}) if allow_empty else None
    if not file_field:
        logger.warning(
            "Attempted to load data for object %s, but FileField is empty.",
            object_id,
        )
        warnings.append(f"Для объекта не указан файл с данными.")
        return fallback_value, warnings

    relative_name = _normalise_storage_name(getattr(file_field, 'name', '') or '')
    try:
        file_path = Path(file_field.path)
    except (ValueError, AttributeError):
        file_path = None
        if relative_name:
            try:
                file_path = _build_absolute_path(relative_name)
            except ValueError:
                file_path = None

    if not file_path or not file_path.exists():
        logger.warning(
            "Data file for object %s is missing or inaccessible (name=%s, resolved_path=%s).",
            object_id,
            relative_name,
            file_path,
        )
        warnings.append(f"Файл с данными объекта не найден.")
        return fallback_value, warnings

    try:
        data_obj, format_hint = _load_dataframe_from_path(file_path)
    except FileNotFoundError:
        logger.warning(
            "Data file for object %s disappeared before it could be read (path=%s).",
            object_id,
            file_path,
        )
        warnings.append(f"Файл с данными объекта не найден.")
        return fallback_value, warnings
    except RuntimeError as exc:
        logger.exception(
            "Failed to decode datastore for object %s (path=%s): %s",
            object_id,
            file_path,
            exc,
        )
        warnings.append(
            f"Не удалось прочитать файл данных объекта. Проверьте настройки шифрования."
        )
        return fallback_value, warnings
    except (pickle.UnpicklingError, json.JSONDecodeError, UnicodeDecodeError):
        logger.exception(
            "Structured data for object %s is corrupted (path=%s).",
            object_id,
            file_path,
        )
        warnings.append(
            f"Файл с данными объекта повреждён или имеет неизвестный формат."
        )
        return fallback_value, warnings
    except Exception:
        logger.exception(
            "Unexpected error while loading data for object %s (path=%s).",
            object_id,
            file_path,
        )
        warnings.append(
            f"Произошла непредвиденная ошибка при чтении данных объекта."
        )
        return fallback_value, warnings

    if not isinstance(data_obj, pd.DataFrame):
        logger.warning(
            "Loaded data for object %s has unexpected type %s.",
            object_id,
            type(data_obj),
        )
        warnings.append(
            f"Файл с данными объекта содержит неподдерживаемый формат."
        )
        return fallback_value, warnings

    if format_hint == 'pickle':
        try:
            _archive_legacy_file(file_path)
        except Exception:
            logger.exception(
                "Failed to archive legacy pickle file for object %s (path=%s).",
                object_id,
                file_path,
            )
            warnings.append(
                f"Не удалось создать резервную копию старого pickle-файла объекта."
            )
        try:
            _write_dataframe(file_field, data_obj, object_instance=object_instance)
        except Exception:
            logger.exception(
                "Failed to convert pickle data to JSON for object %s.",
                object_id,
            )
            warnings.append(
                f"Не удалось автоматически конвертировать данные объекта в новый формат."
            )
        else:
            warnings.append(
                "Данные объекта автоматически переведены в новый формат хранения. "
                "Старый файл сохранён рядом с расширением .old1."
            )

    try:
        data_obj.columns = [str(col) for col in data_obj.columns]
    except Exception:
        logger.exception("Failed to normalise dataframe columns for object %s.", object_id)
    else:
        if data_obj.columns.duplicated().any():
            duplicate_cols = data_obj.columns[data_obj.columns.duplicated()].tolist()
            logger.warning(
                "Duplicate columns detected in dataframe for object %s; keeping first occurrence for: %s",
                object_id,
                ', '.join(duplicate_cols),
            )
            data_obj = data_obj.loc[:, ~data_obj.columns.duplicated()]

    return data_obj, warnings


def _resolve_row_mask(data_obj: pd.DataFrame, identifier: str):
    if data_obj is None:
        return None
    if _RECORD_UID_COLUMN in data_obj.columns:
        mask = data_obj[_RECORD_UID_COLUMN].astype(str) == str(identifier)
        if mask.any():
            return mask
    if 'id_to_connect' in data_obj.columns:
        mask = data_obj['id_to_connect'].astype(str) == str(identifier)
        if mask.any():
            return mask
    return None


def _ensure_record_uid_column(
    obj: Object,
    data_obj: pd.DataFrame,
    *,
    persist: bool = False,
) -> Tuple[pd.DataFrame, bool]:
    if data_obj is None:
        return pd.DataFrame({_RECORD_UID_COLUMN: [], 'id_to_connect': []}), True
    changed = False
    if _RECORD_UID_COLUMN not in data_obj.columns:
        data_obj[_RECORD_UID_COLUMN] = pd.NA
        changed = True
    if 'id_to_connect' not in data_obj.columns:
        data_obj['id_to_connect'] = pd.NA
        changed = True
    for idx, row in data_obj.iterrows():
        record_uid = row.get(_RECORD_UID_COLUMN)
        legacy_id = row.get('id_to_connect')
        record_uid_str = '' if pd.isna(record_uid) else str(record_uid).strip()
        legacy_id_str = '' if pd.isna(legacy_id) else str(legacy_id).strip()
        if not record_uid_str:
            if legacy_id_str:
                resolved_uid = object_data_service.resolve_record_uid_from_identifier(
                    obj=obj,
                    identifier=legacy_id_str,
                )
            else:
                resolved_uid = uuid.uuid4().hex
            data_obj.at[idx, _RECORD_UID_COLUMN] = resolved_uid
            record_uid_str = resolved_uid
            changed = True
        if not legacy_id_str:
            data_obj.at[idx, 'id_to_connect'] = record_uid_str
            changed = True
    if changed and persist:
        _write_dataframe(obj.data, data_obj, object_instance=obj)
    return data_obj, changed

def get_unique_filtered_strings(list_of_values):
    """
    Transform a list of values into a sorted list of unique, non‑empty strings.
    Filters out values such as 'None', 'nan' and '<NA>'.
    """
    filtered_values = []
    for val in list_of_values:
        s_val = str(val).strip()
        if s_val and s_val not in ['None', 'nan', '<NA>']:
            filtered_values.append(s_val)
    return sorted(list(set(filtered_values)))


def get_parameter_data(data_obj, parameter):
    """
    Given a DataFrame and a Parameter, return a list of unique values for that
    parameter column. Handles ARRAY types by splitting on the parameter's
    separator. For linked parameters, returns the ident_list of the linked object.
    """
    if parameter.linked_object:
        child_df, child_warnings = _safe_load_dataframe(
            getattr(parameter.linked_object, 'data', None),
            object_id=getattr(parameter.linked_object, 'id', None),
            object_instance=parameter.linked_object,
            allow_empty=True,
        )
        if child_warnings:
            logger.warning(
                "Warnings while loading linked parameter data in get_parameter_data (object %s -> child %s): %s",
                parameter.object_id,
                getattr(parameter.linked_object, 'id', None),
                "; ".join(child_warnings),
            )
        if child_df is None:
            return []
        child_df, _ = _ensure_record_uid_column(parameter.linked_object, child_df, persist=False)
        ident_param = Parameter.objects.filter(object=parameter.linked_object, identificator=True).first()
        if ident_param:
            ident_column_key = _resolve_dataframe_column(child_df, ident_param.id)
            if ident_column_key is not None:
                ident_list = []
                for _, row in child_df.iterrows():
                    child_ident = row.get(_RECORD_UID_COLUMN) or row.get('id_to_connect')
                    ident_value = row.get(ident_column_key)
                    if pd.isna(child_ident) or pd.isna(ident_value):
                        continue
                    ident_list.append((str(child_ident), str(ident_value).strip()))
                return ident_list
            logger.warning(
                "Linked object %s data missing identifier column %s when collecting parameter values.",
                getattr(parameter.linked_object, 'id', None),
                ident_param.id,
            )
        return []
    column_key = _resolve_dataframe_column(data_obj, parameter.id)
    if column_key is None:
        data_obj[str(parameter.id)] = pd.NA
        return None
    column_series = data_obj[column_key].dropna()
    if parameter.data_type == 'ARRAY':
        raw_values_list = []
        for cell_value in column_series:
            cell_value_str = str(cell_value)
            raw_values_list.extend([val.strip() for val in cell_value_str.split(parameter.array_separator)])
    else:
        raw_values_list = column_series.tolist()
    return get_unique_filtered_strings(list(set(raw_values_list)))


def get_parameters_data_all(obj):
    """
    Load the object's DataFrame and build a list of (Parameter, values) tuples
    for all parameters defined on the object. Parameters with no values are
    skipped.
    """
    data_obj, load_warnings = _safe_load_dataframe(obj.data, object_id=obj.pk, object_instance=obj)
    if load_warnings:
        logger.warning(
            "Warnings while loading data for get_parameters_data_all (object %s): %s",
            obj.pk,
            "; ".join(load_warnings),
        )
    if data_obj is None:
        return []
    parameters_data = []
    for parameter in Parameter.objects.filter(object=obj):
        data = get_parameter_data(data_obj, parameter)
        if data is not None:
            parameters_data.append((parameter, data))
    return parameters_data


@login_required
@permission_required('database_manager.manage_object_data', raise_exception=True)
def add_element_to_object(request, pk):
    """
    Add a new row to the object's DataFrame. Collects values for each
    parameter from the request and writes the updated DataFrame back to disk.

    When displaying the form (GET), parameters are grouped by categories and
    arranged in alphabetical order, with uncategorised parameters grouped
    under "Вне категории".  Any child objects (defined via
    ``Object_ParentObject``) are presented as tabs where the user can
    optionally select an identifier from the child object and preview its
    fields.  On POST, the new row is appended to the object's DataFrame and
    any selected child links are recorded in ``ObjectLink_identificators``.
    """
    obj = get_object_or_404(Object, pk=pk)
    data_obj, load_warnings = _safe_load_dataframe(obj.data, object_id=obj.pk, object_instance=obj, allow_empty=True)
    if load_warnings:
        logger.warning(
            "Warnings while loading data for add_element_to_object (object %s): %s",
            obj.pk,
            "; ".join(load_warnings),
        )
        if request.method != 'POST':
            for warning in load_warnings:
                messages.warning(request, warning)
    if data_obj is None:
        data_obj = pd.DataFrame({'id_to_connect': [], _RECORD_UID_COLUMN: []})
    data_obj, _ = _ensure_record_uid_column(obj, data_obj, persist=False)
    # Handle form submission
    if request.method == 'POST':
        # Validate that an identifier has been provided if the object defines one
        ident_param = Parameter.objects.filter(object=obj, identificator=True).first()
        if ident_param:
            ident_values = request.POST.getlist(f'col_value_{ident_param.id}[]')
            # Check first value (for single-select or single text input). Empty string considered missing
            if not ident_values or not str(ident_values[0]).strip():
                # Prepare data to re-render the form with an error
                base_params_data = get_parameters_data_all(obj)
                parameters_group_data = _group_parameters_data(base_params_data)
                parameters_objects = Object_ParentObject.objects.filter(parent_object=obj)
                child_objects_data = []
                for idx, po in enumerate(parameters_objects):
                    child_obj = po.object
                    ident_list = []
                    child_df, child_warnings = _safe_load_dataframe(
                        getattr(child_obj, 'data', None),
                        object_id=getattr(child_obj, 'id', None),
                        object_instance=child_obj,
                        allow_empty=True,
                    )
                    if child_warnings:
                        logger.warning(
                            "Warnings while loading child object data for validation (parent %s -> child %s): %s",
                            obj.pk,
                            getattr(child_obj, 'id', None),
                            "; ".join(child_warnings),
                        )
                        for warning in child_warnings:
                            messages.warning(request, warning)
                    if child_df is not None:
                        child_df, _ = _ensure_record_uid_column(child_obj, child_df, persist=False)
                        ident_child_param = Parameter.objects.filter(object=child_obj, identificator=True).first()
                        if ident_child_param is not None:
                            ident_column_key = _resolve_dataframe_column(child_df, ident_child_param.id)
                            if ident_column_key is None:
                                logger.warning(
                                    "Child object %s data missing identifier column %s during validation.",
                                    getattr(child_obj, 'id', None),
                                    ident_child_param.id,
                                )
                            else:
                                for _, row in child_df.iterrows():
                                    child_ident = row.get(_RECORD_UID_COLUMN) or row.get('id_to_connect')
                                    ident_value = row.get(ident_column_key)
                                    if pd.isna(child_ident) or pd.isna(ident_value):
                                        continue
                                    ident_list.append((str(child_ident), str(ident_value).strip()))
                    child_params_data = get_parameters_data_all(child_obj)
                    child_group_data = _group_parameters_data(child_params_data)
                    child_objects_data.append({
                        'link_id': po.id,
                        'object': child_obj,
                        'group_data': child_group_data,
                        'ident_list': ident_list,
                        'active': True if idx == 0 else False,
                        'link_type': po.link_type,
                    })
                return render(request, 'database_manager/add_element_to_object.html', context={
                    'object': obj,
                    'parameters_group_data': parameters_group_data,
                    'child_objects_data': child_objects_data,
                    'error_message': 'Поле идентификатора обязательно для заполнения.',
                })
        # Build a new row with a unique id_to_connect
        new_record_uid = uuid.uuid4().hex
        new_row_id = new_record_uid
        new_row = {"id_to_connect": new_row_id, _RECORD_UID_COLUMN: new_record_uid}

        def _parse_param_id_from_key(key: str):
            prefix = 'col_value_'
            if not key.startswith(prefix):
                return None
            suffix = key[len(prefix):]
            if suffix.endswith('[]'):
                suffix = suffix[:-2]
            try:
                return int(suffix)
            except (TypeError, ValueError):
                return None

        ordered_param_ids = []
        seen_param_ids = set()
        for raw_id in request.POST.getlist('col_id[]'):
            if not raw_id:
                continue
            try:
                col_id = int(raw_id)
            except (TypeError, ValueError):
                continue
            if col_id not in seen_param_ids:
                ordered_param_ids.append(col_id)
                seen_param_ids.add(col_id)
        if not ordered_param_ids:
            for key in request.POST.keys():
                parsed_id = _parse_param_id_from_key(key)
                if parsed_id is None or parsed_id in seen_param_ids:
                    continue
                ordered_param_ids.append(parsed_id)
                seen_param_ids.add(parsed_id)

        param_map = {
            param.id: param
            for param in Parameter.objects.filter(object=obj, id__in=ordered_param_ids)
        } if ordered_param_ids else {}

        for col_id in ordered_param_ids:
            parameter = param_map.get(col_id)
            if not parameter:
                logger.warning(
                    "Parameter %s referenced during add operation but not found for object %s.",
                    col_id,
                    obj.pk,
                )
                continue
            field_name = f'col_value_{col_id}[]'
            if field_name not in request.POST:
                # No value submitted (likely disabled field); skip.
                continue
            col_values = request.POST.getlist(field_name)
            column_key = _ensure_dataframe_column(data_obj, parameter.id)
            if not col_values:
                new_row[column_key] = ''
            elif len(col_values) == 1:
                new_row[column_key] = str(col_values[0])
            else:
                separator = parameter.array_separator if parameter.array_separator is not None else ' '
                new_row[column_key] = separator.join(col_values)
        # Append to DataFrame and persist
        data_obj = pd.concat([data_obj, pd.DataFrame([new_row])], ignore_index=True)
        if _sql_source_of_truth_enabled():
            object_data_service.dual_write_upsert(
                obj=obj,
                record_identifier=new_row_id,
                row_data=new_row,
                parameters=param_map.values(),
                op='add',
                record_uid=new_record_uid,
                legacy_id_to_connect=new_row_id,
            )
            object_data_service.run_secondary_file_write(
                write_callback=lambda: _write_dataframe(obj.data, data_obj, object_instance=obj),
                object_id=obj.id,
                record_uid=new_record_uid,
                op='add',
            )
        else:
            _write_dataframe(obj.data, data_obj, object_instance=obj)
            object_data_service.dual_write_upsert(
                obj=obj,
                record_identifier=new_row_id,
                row_data=new_row,
                parameters=param_map.values(),
                op='add',
                record_uid=new_record_uid,
                legacy_id_to_connect=new_row_id,
            )
        # After saving the row, record any selected child links from linked parameters
        for parameter in param_map.values():
            if not parameter.linked_object_id:
                continue
            link = Object_ParentObject.objects.get(parent_object=obj, object=parameter.linked_object)
            col_values = request.POST.getlist(f'col_value_{parameter.id}[]')
            if parameter.data_type == 'ARRAY':
                # Multiple links
                ObjectLink_identificators.objects.filter(object_link=link, parent_object_identificator=new_row_id).delete()
                for val in col_values:
                    if val:
                        child_record_uid = object_data_service.resolve_record_uid_from_identifier(
                            obj=parameter.linked_object,
                            identifier=str(val),
                        )
                        ObjectLink_identificators.objects.update_or_create(
                            object_link=link,
                            parent_object_identificator=new_row_id,
                            object_identificator=child_record_uid,
                        )
            else:
                # Single link
                child_value = col_values[0] if col_values else ''
                if child_value:
                    child_record_uid = object_data_service.resolve_record_uid_from_identifier(
                        obj=parameter.linked_object,
                        identifier=str(child_value),
                    )
                    ObjectLink_identificators.objects.update_or_create(
                        object_link=link,
                        parent_object_identificator=new_row_id,
                        defaults={'object_identificator': child_record_uid},
                    )
                else:
                    ObjectLink_identificators.objects.filter(
                        object_link=link,
                        parent_object_identificator=new_row_id
                    ).delete()
        object_link_service.sync_parent_row_links(parent_obj=obj, parent_identifier=new_row_id)
        # Redirect back to the object detail page to show the updated data
        return redirect('get_object', pk=obj.id)
    # Prepare data for GET: group base object parameters by categories
    base_params_data = get_parameters_data_all(obj)
    parameters_group_data = _group_parameters_data(base_params_data)
    # Prepare child object data: for each Object_ParentObject we need its
    # identifier list and parameter grouping for preview
    parameters_objects = Object_ParentObject.objects.filter(parent_object=obj)
    child_objects_data = []
    for idx, po in enumerate(parameters_objects):
        child_obj = po.object
        # Build list of (id_to_connect, identifier label) for the child object
        ident_list = []
        child_df, child_warnings = _safe_load_dataframe(
            getattr(child_obj, 'data', None),
            object_id=getattr(child_obj, 'id', None),
            object_instance=child_obj,
            allow_empty=True,
        )
        if child_warnings:
            logger.warning(
                "Warnings while loading child object data for preview (parent %s -> child %s): %s",
                obj.pk,
                getattr(child_obj, 'id', None),
                "; ".join(child_warnings),
            )
            for warning in child_warnings:
                messages.warning(request, warning)
        if child_df is not None:
            child_df, _ = _ensure_record_uid_column(child_obj, child_df, persist=False)
            ident_param = Parameter.objects.filter(object=child_obj, identificator=True).first()
            if ident_param is not None:
                ident_column_key = _resolve_dataframe_column(child_df, ident_param.id)
                if ident_column_key is None:
                    logger.warning(
                        "Child object %s data missing identifier column %s for preview.",
                        getattr(child_obj, 'id', None),
                        ident_param.id,
                    )
                else:
                    for _, row in child_df.iterrows():
                        child_ident = row.get(_RECORD_UID_COLUMN) or row.get('id_to_connect')
                        ident_value = row.get(ident_column_key)
                        if pd.isna(child_ident) or pd.isna(ident_value):
                            continue
                        ident_list.append((str(child_ident), str(ident_value).strip()))
        # Group the child object's parameters for display in the preview
        child_params_data = get_parameters_data_all(child_obj)
        child_group_data = _group_parameters_data(child_params_data)
        child_objects_data.append({
            'link_id': po.id,
            'object': child_obj,
            'group_data': child_group_data,
            'ident_list': ident_list,
            'active': True if idx == 0 else False,
            'link_type': po.link_type,
        })
    return render(request, 'database_manager/add_element_to_object.html', context={
        'object': obj,
        'parameters_group_data': parameters_group_data,
        'child_objects_data': child_objects_data,
    })


def find_in_params_data(params_data, value, param):
    """
    Helper used by update_element_to_object to compute the indices of selected
    values for rendering drop‑down inputs. For ARRAY parameters, splits the
    current value using the parameter separator.
    """
    result = []
    for data in params_data:
        if param.data_type == 'ARRAY':
            if data[1] in set(filter(lambda x: str(x).strip(), value.split(param.array_separator))):
                result.append(data[0])
        else:
            if value.strip() == data[1]:
                result.append(data[0])
    return result


def get_indexed_unique_filtered_values(values_list, array_separator=None):
    """
    Process a list of values: remove invalid entries, optionally split values
    using a separator and return a sorted list of tuples (index, unique value).
    """
    processed_elements = []
    for val in values_list:
        s_val = str(val).strip()
        if s_val and s_val not in ['None', 'nan', '<NA>']:
            if array_separator:
                processed_elements.extend([el.strip() for el in s_val.split(array_separator) if el.strip()])
            else:
                processed_elements.append(s_val)
    final_filtered = [el for el in processed_elements if el and el not in ['None', 'nan', '<NA>']]
    unique_filtered = sorted(list(set(final_filtered)))
    return [(i, data) for i, data in enumerate(unique_filtered)]


def _resolve_dataframe_column(df, parameter_id):
    """
    Return the actual column label used in the DataFrame for the given parameter id.
    Columns may be stored either as integers or strings depending on the source.
    """
    if df is None:
        return None

    candidates = []

    try:
        candidates.append(int(parameter_id))
    except (TypeError, ValueError):
        pass

    str_candidate = str(parameter_id)
    if str_candidate not in candidates:
        candidates.append(str_candidate)

    if parameter_id not in candidates:
        candidates.append(parameter_id)

    # Try exact matches for the primary candidates first
    for candidate in candidates:
        if candidate in df.columns:
            return candidate

    # Fallback: normalise column labels by trimming whitespace and trailing ".0" artefacts
    def _normalise_label(label):
        text = str(label).strip()
        if not text:
            return text
        # Remove trailing zeros in numeric-like labels (e.g. "798.0" -> "798")
        if '.' in text:
            stripped = text.rstrip('0').rstrip('.')
            if stripped:
                text = stripped
        return text

    target_norm = _normalise_label(str_candidate)
    if not target_norm and isinstance(parameter_id, (int, float)):
        target_norm = _normalise_label(parameter_id)

    for column in df.columns:
        if _normalise_label(column) == target_norm:
            return column

    return None


def _ensure_dataframe_column(df, parameter_id):
    """
    Ensure the DataFrame contains a column for the given parameter id.
    Returns the column key (existing or newly created).
    """
    column_key = _resolve_dataframe_column(df, parameter_id)
    if column_key is not None:
        return column_key
    column_key = str(parameter_id)
    if column_key not in df.columns:
        df[column_key] = pd.NA
    return column_key


def _is_api_request(request):
    """
    Detect whether the incoming request is likely triggered via fetch/AJAX/HTMX.
    This lets views decide between returning JSON payloads vs browser redirects.
    """
    headers = request.headers
    if headers.get('HX-Request'):
        return True
    if headers.get('X-Requested-With') == 'XMLHttpRequest':
        return True
    accept = headers.get('Accept', '')
    if 'application/json' in accept.lower():
        return True
    fetch_mode = headers.get('Sec-Fetch-Mode', '').lower()
    if fetch_mode in {'cors', 'no-cors', 'same-origin'}:
        return True
    fetch_dest = headers.get('Sec-Fetch-Dest', '').lower()
    if fetch_dest == 'empty':
        return True
    if request.META.get('HTTP_SEC_FETCH_DEST', '').lower() == 'empty':
        return True
    if not headers.get('Upgrade-Insecure-Requests'):
        return True
    if request.GET.get('format') == 'json':
        return True
    return False


def _resolve_api_version(request, default: str = 'legacy') -> str:
    """
    Resolve API version for response payloads.

    Supported selectors:
    - query param: ?api_version=v1
    - header: X-API-Version: v1
    """
    version_raw = request.GET.get('api_version') or request.headers.get('X-API-Version', '')
    version = str(version_raw).strip().lower()
    if version in {'v1', '1'}:
        return 'v1'
    return default


def _sql_source_of_truth_enabled() -> bool:
    return bool(getattr(settings, 'DBM_SQL_SOURCE_OF_TRUTH', False))


def _v1_error(code: str, message: str, *, status: int = 400, details: Optional[Dict[str, Any]] = None) -> JsonResponse:
    return JsonResponse(
        {
            "error": {
                "code": code,
                "message": message,
                "details": details or {},
            }
        },
        status=status,
    )


def _parse_json_request(request) -> Optional[Dict[str, Any]]:
    body = request.body.decode("utf-8").strip() if request.body else ""
    if not body:
        return {}
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    return payload


def _legacy_record_to_v1_record(
    legacy_record: Dict[str, Any],
    *,
    parameters: Optional[List[Parameter]] = None,
) -> Dict[str, Any]:
    schema = object_data_service._schema_from_parameters(parameters or [])
    return serialise_record_dto(
        legacy_record_to_dto(legacy_record, schema=schema, canonicalize=True)
    )


def _extract_v1_fields_payload(
    payload: Dict[str, Any],
    parameters: List[Parameter],
    *,
    allow_partial: bool,
) -> Union[Dict[str, Dict[str, Any]], JsonResponse]:
    record_payload = payload.get("record", {})
    if not isinstance(record_payload, dict):
        return _v1_error("VALIDATION_ERROR", "Поле record должно быть объектом.", details={"field": "record"})
    fields_payload = record_payload.get("fields")
    if not isinstance(fields_payload, dict):
        return _v1_error("VALIDATION_ERROR", "Поле record.fields должно быть объектом.", details={"field": "record.fields"})
    parameter_map = {str(parameter.id): parameter for parameter in parameters}
    normalised: Dict[str, Dict[str, Any]] = {}
    for raw_param_id, raw_field in fields_payload.items():
        param_id = str(raw_param_id)
        parameter = parameter_map.get(param_id)
        if parameter is None:
            return _v1_error(
                "VALIDATION_ERROR",
                "Передан неизвестный параметр.",
                details={"field": f"record.fields.{param_id}"},
            )
        if not isinstance(raw_field, dict):
            return _v1_error(
                "VALIDATION_ERROR",
                "Каждое поле должно быть объектом с type/value.",
                details={"field": f"record.fields.{param_id}"},
            )
        normalised[param_id] = {
            "type": str(raw_field.get("type") or parameter.data_type),
            "value": raw_field.get("value"),
        }
    if allow_partial:
        return normalised
    for parameter in parameters:
        param_id = str(parameter.id)
        normalised.setdefault(param_id, {"type": parameter.data_type, "value": ""})
    return normalised


def _field_to_dataframe_value(parameter: Parameter, field_payload: Dict[str, Any]) -> str:
    value = field_payload.get("value")
    if parameter.data_type == "ARRAY":
        separator = parameter.array_separator or " "
        if isinstance(value, list):
            values = [str(item).strip() for item in value if str(item).strip()]
        elif value in (None, ""):
            values = []
        else:
            values = [str(value).strip()]
        return separator.join(values)
    if value is None:
        return ""
    text = str(value).strip()
    if text.lower() in {"none", "nan", "<na>"}:
        return ""
    return text


def _sync_links_from_v1_fields(
    *,
    obj: Object,
    record_uid: str,
    parameter_map: Dict[str, Parameter],
    fields_payload: Dict[str, Dict[str, Any]],
) -> None:
    parent_uid = object_data_service.resolve_record_uid_from_identifier(obj=obj, identifier=str(record_uid))
    has_link_changes = False
    for param_id, field_payload in fields_payload.items():
        parameter = parameter_map.get(str(param_id))
        if parameter is None or not parameter.linked_object_id:
            continue
        relation = Object_ParentObject.objects.filter(
            parent_object=obj,
            object_id=parameter.linked_object_id,
        ).first()
        if relation is None:
            continue
        has_link_changes = True
        raw_value = field_payload.get("value")
        if parameter.data_type == "ARRAY":
            if isinstance(raw_value, list):
                child_identifiers = [str(item).strip() for item in raw_value if str(item).strip()]
            elif raw_value in (None, ""):
                child_identifiers = []
            else:
                child_identifiers = [str(raw_value).strip()]
            ObjectLink_identificators.objects.filter(
                object_link=relation,
                parent_object_identificator=parent_uid,
            ).delete()
            for child_identifier in child_identifiers:
                child_uid = object_data_service.resolve_record_uid_from_identifier(
                    obj=parameter.linked_object,
                    identifier=child_identifier,
                )
                ObjectLink_identificators.objects.update_or_create(
                    object_link=relation,
                    parent_object_identificator=parent_uid,
                    object_identificator=child_uid,
                )
        else:
            child_identifier = str(raw_value or "").strip()
            if child_identifier:
                child_uid = object_data_service.resolve_record_uid_from_identifier(
                    obj=parameter.linked_object,
                    identifier=child_identifier,
                )
                ObjectLink_identificators.objects.update_or_create(
                    object_link=relation,
                    parent_object_identificator=parent_uid,
                    defaults={"object_identificator": child_uid},
                )
            else:
                ObjectLink_identificators.objects.filter(
                    object_link=relation,
                    parent_object_identificator=parent_uid,
                ).delete()
    if has_link_changes:
        object_link_service.sync_parent_row_links(parent_obj=obj, parent_identifier=parent_uid)


def _api_v1_create_record(
    *,
    obj: Object,
    parameters: List[Parameter],
    fields_payload: Dict[str, Dict[str, Any]],
) -> JsonResponse:
    parameter_map = {str(parameter.id): parameter for parameter in parameters}
    record_uid = uuid.uuid4().hex
    row_payload: Dict[str, Any] = {"id_to_connect": record_uid, _RECORD_UID_COLUMN: record_uid}
    for parameter in parameters:
        param_id = str(parameter.id)
        field_payload = fields_payload.get(param_id, {"type": parameter.data_type, "value": ""})
        row_payload[param_id] = _field_to_dataframe_value(parameter, field_payload)

    data_obj, warnings = _safe_load_dataframe(obj.data, object_id=obj.pk, object_instance=obj, allow_empty=True)
    if warnings:
        logger.warning("Warnings while loading dataframe for v1 create (object %s): %s", obj.pk, "; ".join(warnings))
    if data_obj is None:
        data_obj = pd.DataFrame({"id_to_connect": [], _RECORD_UID_COLUMN: []})
    data_obj, _ = _ensure_record_uid_column(obj, data_obj, persist=False)
    for parameter in parameters:
        _ensure_dataframe_column(data_obj, parameter.id)
    updated_df = pd.concat([data_obj, pd.DataFrame([row_payload])], ignore_index=True)

    try:
        if _sql_source_of_truth_enabled():
            object_data_service.dual_write_upsert(
                obj=obj,
                record_identifier=record_uid,
                row_data=row_payload,
                parameters=parameters,
                op="api_create",
                record_uid=record_uid,
                legacy_id_to_connect=record_uid,
            )
            object_data_service.run_secondary_file_write(
                write_callback=lambda: _write_dataframe(obj.data, updated_df, object_instance=obj),
                object_id=obj.id,
                record_uid=record_uid,
                op="api_create",
            )
        else:
            _write_dataframe(obj.data, updated_df, object_instance=obj)
            object_data_service.dual_write_upsert(
                obj=obj,
                record_identifier=record_uid,
                row_data=row_payload,
                parameters=parameters,
                op="api_create",
                record_uid=record_uid,
                legacy_id_to_connect=record_uid,
            )
    except Exception as exc:
        logger.exception("v1 create failed for object %s.", obj.id)
        return _v1_error("SERVER_ERROR", "Не удалось создать запись.", status=500, details={"exc": str(exc)})

    _sync_links_from_v1_fields(
        obj=obj,
        record_uid=record_uid,
        parameter_map=parameter_map,
        fields_payload=fields_payload,
    )
    legacy_record = _build_legacy_record_from_file(obj, record_uid, parameters)
    if legacy_record is None:
        legacy_record = {"id_to_connect": record_uid}
        for parameter in parameters:
            param_id = str(parameter.id)
            legacy_record[param_id] = {
                "data_type": parameter.data_type,
                "value": fields_payload.get(param_id, {}).get("value", ""),
            }
    return JsonResponse(
        {
            "api_version": "v1",
            "object_id": obj.id,
                "record": _legacy_record_to_v1_record(legacy_record, parameters=parameters),
        },
        status=201,
    )


def _api_v1_update_record(
    *,
    obj: Object,
    record_uid: str,
    parameters: List[Parameter],
    fields_payload: Dict[str, Dict[str, Any]],
) -> JsonResponse:
    parameter_map = {str(parameter.id): parameter for parameter in parameters}
    changed_parameters = [parameter_map[str(param_id)] for param_id in fields_payload.keys() if str(param_id) in parameter_map]
    data_obj, warnings = _safe_load_dataframe(obj.data, object_id=obj.pk, object_instance=obj, allow_empty=True)
    if warnings:
        logger.warning("Warnings while loading dataframe for v1 update (object %s): %s", obj.pk, "; ".join(warnings))
    if data_obj is None:
        data_obj = pd.DataFrame({"id_to_connect": [], _RECORD_UID_COLUMN: []})
    data_obj, _ = _ensure_record_uid_column(obj, data_obj, persist=False)
    row_mask = _resolve_row_mask(data_obj, str(record_uid))
    row_exists = row_mask is not None and row_mask.any()

    sql_row_payload: Dict[str, Any] = {"id_to_connect": record_uid, _RECORD_UID_COLUMN: record_uid}
    for param_id, field_payload in fields_payload.items():
        parameter = parameter_map.get(str(param_id))
        if parameter is None:
            continue
        value_for_df = _field_to_dataframe_value(parameter, field_payload)
        sql_row_payload[str(param_id)] = value_for_df
        if row_exists:
            column_key = _ensure_dataframe_column(data_obj, parameter.id)
            data_obj.loc[row_mask, column_key] = value_for_df

    try:
        if _sql_source_of_truth_enabled():
            object_data_service.dual_write_upsert(
                obj=obj,
                record_identifier=record_uid,
                row_data=sql_row_payload,
                parameters=changed_parameters,
                op="api_update",
                record_uid=record_uid,
                legacy_id_to_connect=record_uid,
            )
            if row_exists:
                object_data_service.run_secondary_file_write(
                    write_callback=lambda: _write_dataframe(obj.data, data_obj, object_instance=obj),
                    object_id=obj.id,
                    record_uid=record_uid,
                    op="api_update",
                )
        else:
            if not row_exists:
                return _v1_error("NOT_FOUND", "Запись не найдена.", status=404)
            _write_dataframe(obj.data, data_obj, object_instance=obj)
            row_payload = data_obj.loc[row_mask].iloc[0].to_dict()
            object_data_service.dual_write_upsert(
                obj=obj,
                record_identifier=record_uid,
                row_data=row_payload,
                parameters=changed_parameters,
                op="api_update",
                record_uid=str(row_payload.get(_RECORD_UID_COLUMN) or record_uid),
                legacy_id_to_connect=str(row_payload.get("id_to_connect") or record_uid),
            )
    except Exception as exc:
        logger.exception("v1 update failed for object %s record %s.", obj.id, record_uid)
        return _v1_error("SERVER_ERROR", "Не удалось обновить запись.", status=500, details={"exc": str(exc)})

    _sync_links_from_v1_fields(
        obj=obj,
        record_uid=record_uid,
        parameter_map=parameter_map,
        fields_payload=fields_payload,
    )
    legacy_record = object_data_service.read_record_with_policy(
        obj,
        record_uid,
        parameters,
        build_file_record=lambda: _build_legacy_record_from_file(obj, record_uid, parameters),
    )
    if legacy_record is None:
        return _v1_error("NOT_FOUND", "Запись не найдена.", status=404)
    return JsonResponse(
        {
            "api_version": "v1",
            "object_id": obj.id,
            "record": _legacy_record_to_v1_record(legacy_record, parameters=parameters),
        }
    )


def _api_v1_delete_record(*, obj: Object, record_uid: str) -> JsonResponse:
    data_obj, warnings = _safe_load_dataframe(obj.data, object_id=obj.pk, object_instance=obj, allow_empty=True)
    if warnings:
        logger.warning("Warnings while loading dataframe for v1 delete (object %s): %s", obj.pk, "; ".join(warnings))
    if data_obj is None:
        data_obj = pd.DataFrame({"id_to_connect": [], _RECORD_UID_COLUMN: []})
    data_obj, _ = _ensure_record_uid_column(obj, data_obj, persist=False)
    row_mask = _resolve_row_mask(data_obj, str(record_uid))
    row_exists = row_mask is not None and row_mask.any()
    parent_identifiers = {str(record_uid)}
    if row_exists:
        row_data = data_obj.loc[row_mask].iloc[0]
        row_uid = str(row_data.get(_RECORD_UID_COLUMN) or "").strip()
        row_legacy = str(row_data.get("id_to_connect") or "").strip()
        if row_uid:
            parent_identifiers.add(row_uid)
        if row_legacy:
            parent_identifiers.add(row_legacy)
        data_obj = data_obj.loc[~row_mask].copy()

    ObjectLink_identificators.objects.filter(
        object_link__parent_object=obj,
        parent_object_identificator__in=list(parent_identifiers),
    ).delete()
    ObjectLink_identificators.objects.filter(
        object_link__object=obj,
        object_identificator__in=list(parent_identifiers),
    ).delete()

    try:
        if _sql_source_of_truth_enabled():
            object_data_service.dual_write_delete(obj=obj, record_identifier=str(record_uid))
            if row_exists:
                object_data_service.run_secondary_file_write(
                    write_callback=lambda: _write_dataframe(obj.data, data_obj, object_instance=obj),
                    object_id=obj.id,
                    record_uid=str(record_uid),
                    op="api_delete",
                )
        else:
            if not row_exists:
                return _v1_error("NOT_FOUND", "Запись не найдена.", status=404)
            _write_dataframe(obj.data, data_obj, object_instance=obj)
            object_data_service.dual_write_delete(obj=obj, record_identifier=str(record_uid))
    except Exception as exc:
        logger.exception("v1 delete failed for object %s record %s.", obj.id, record_uid)
        return _v1_error("SERVER_ERROR", "Не удалось удалить запись.", status=500, details={"exc": str(exc)})

    return JsonResponse(
        {
            "api_version": "v1",
            "object_id": obj.id,
            "record_uid": str(record_uid),
            "deleted": True,
        }
    )

def get_parameters_data_by_ident(obj: Object, param_ident_id) -> list:
    """
    Load a DataFrame and return, for each Parameter, the available values and
    which indices should be selected for the given row (`param_ident_id`).
    """
    data_obj, load_warnings = _safe_load_dataframe(obj.data, object_id=obj.pk, object_instance=obj)
    if load_warnings:
        logger.warning(
            "Warnings while loading data for get_parameters_data_by_ident (object %s): %s",
            obj.pk,
            "; ".join(load_warnings),
        )
    if data_obj is None:
        return []
    data_obj, _ = _ensure_record_uid_column(obj, data_obj, persist=False)
    row = None
    row_mask = None
    parent_identifier_candidates = set()
    if param_ident_id is not None:
        row_mask = _resolve_row_mask(data_obj, str(param_ident_id))
        if row_mask is not None:
            row = data_obj.loc[row_mask]
        parent_identifier_candidates.add(str(param_ident_id))
        sql_parent_record = object_data_service.sql_repo.get_record_by_uid_or_legacy(obj, str(param_ident_id))
        if sql_parent_record is not None:
            parent_identifier_candidates.add(sql_parent_record.record_uid)
            if sql_parent_record.legacy_id_to_connect:
                parent_identifier_candidates.add(str(sql_parent_record.legacy_id_to_connect))
    parameters_data = []
    for parameter in Parameter.objects.filter(object=obj):
        current_data = ""
        column_key = _ensure_dataframe_column(data_obj, parameter.id)
        if row is not None and not row.empty and column_key is not None:
            refreshed_row = data_obj.loc[row_mask] if row_mask is not None else row
            candidate_key = None
            if column_key in refreshed_row.columns:
                candidate_key = column_key
            else:
                candidate_key = _resolve_dataframe_column(refreshed_row, parameter.id)
            if candidate_key is None:
                try:
                    numeric_key = int(column_key)
                except (TypeError, ValueError):
                    numeric_key = None
                else:
                    if numeric_key in refreshed_row.columns:
                        candidate_key = numeric_key
            if candidate_key is not None and candidate_key in refreshed_row.columns:
                series = refreshed_row[candidate_key]
                if not series.empty:
                    try:
                        cell_value = series.iloc[0]
                    except (IndexError, KeyError):
                        cell_value = ""
                    else:
                        if not (pd.isna(cell_value) or str(cell_value) in ['None', 'nan', '<NA>']):
                            current_data = str(cell_value).strip()
        current_data = current_data if current_data not in ['None', 'nan', None, '<NA>'] else ""
        if parameter.linked_object:
            # For linked parameters, get selected ids from links
            link = Object_ParentObject.objects.get(parent_object=obj, object=parameter.linked_object)
            if parameter.data_type == 'ARRAY':
                selected_ids = [
                    li.object_identificator
                    for li in ObjectLink_identificators.objects.filter(
                        object_link=link,
                        parent_object_identificator__in=list(parent_identifier_candidates),
                    )
                ]
            else:
                li = ObjectLink_identificators.objects.filter(
                    object_link=link,
                    parent_object_identificator__in=list(parent_identifier_candidates),
                ).first()
                selected_ids = [li.object_identificator] if li else []
            if not selected_ids and current_data:
                if parameter.data_type == 'ARRAY':
                    if parameter.array_separator:
                        fallback_items = [
                            item.strip()
                            for item in str(current_data).split(parameter.array_separator)
                            if item and item.strip()
                        ]
                    else:
                        fallback_items = [str(current_data).strip()]
                    selected_ids = [item for item in fallback_items if item]
                else:
                    cleaned = str(current_data).strip()
                    if cleaned:
                        selected_ids = [cleaned]
            # Get data as list of (id, label)
            child_df, child_warnings = _safe_load_dataframe(
                getattr(parameter.linked_object, 'data', None),
                object_id=getattr(parameter.linked_object, 'id', None),
                object_instance=parameter.linked_object,
                allow_empty=True,
            )
            if child_warnings:
                logger.warning(
                    "Warnings while loading linked parameter data (object %s -> child %s): %s",
                    obj.pk,
                    getattr(parameter.linked_object, 'id', None),
                    "; ".join(child_warnings),
                )
            data = []
            if child_df is not None:
                child_df, _ = _ensure_record_uid_column(parameter.linked_object, child_df, persist=False)
                ident_param = Parameter.objects.filter(object=parameter.linked_object, identificator=True).first()
                if ident_param:
                    ident_column_key = _resolve_dataframe_column(child_df, ident_param.id)
                    if ident_column_key is None:
                        logger.warning(
                            "Linked object %s data missing identifier column %s.",
                            getattr(parameter.linked_object, 'id', None),
                            ident_param.id,
                        )
                    else:
                        for _, row in child_df.iterrows():
                            child_ident = row.get(_RECORD_UID_COLUMN) or row.get('id_to_connect')
                            ident_value = row.get(ident_column_key)
                            if pd.isna(child_ident) or pd.isna(ident_value):
                                continue
                            data.append((str(child_ident), str(ident_value).strip()))
            parameters_data.append((parameter, data, selected_ids, current_data))
        else:
            param_series = data_obj[column_key] if column_key in data_obj.columns else pd.Series(dtype=object)
            param_data_raw = param_series.dropna().values.tolist()
            if parameter.data_type == 'ARRAY':
                param_data = [str(value) for value in param_data_raw]
                arrays_data = parameter.array_separator.join(param_data)
                unique_param_data = set(filter(lambda x: str(x).strip(), arrays_data.split(parameter.array_separator)))
                arrays_data_list = list(set([value.strip() for value in unique_param_data if value not in ['None', 'nan', None, '<NA>'] and value]))
                arrays_data_indexed = [(i, data) for i, data in enumerate(arrays_data_list)]
                parameters_data.append((parameter, arrays_data_indexed, find_in_params_data(arrays_data_indexed, current_data, parameter), current_data))
            else:
                param_data = param_data_raw
                cleaned_values = []
                for value in param_data:
                    if value in ['None', 'nan', None, '<NA>']:
                        continue
                    if isinstance(value, str):
                        normalised = value.strip()
                        if normalised and normalised not in ['None', 'nan', '<NA>']:
                            cleaned_values.append(normalised)
                    elif isinstance(value, (list, tuple, set)):
                        for item in value:
                            if item in ['None', 'nan', None, '<NA>']:
                                continue
                            normalised = str(item).strip()
                            if normalised and normalised not in ['None', 'nan', '<NA>']:
                                cleaned_values.append(normalised)
                    else:
                        normalised = str(value).strip()
                        if normalised and normalised not in ['None', 'nan', '<NA>']:
                            cleaned_values.append(normalised)
                filtered_param_data = list(set(cleaned_values))
                param_data_indexed = [(i, data) for i, data in enumerate(filtered_param_data)]
                # print(current_data)
                parameters_data.append((parameter, param_data_indexed, find_in_params_data(param_data_indexed, current_data, parameter), current_data))
    # print('#'*50)
    # print(parameters_data)
    # print('#'*50)
    return parameters_data


def _extract_param_ids_from_request(request) -> List[int]:
    """
    Restore the order in which parameters were rendered in the form.
    Falls back to inspecting POST keys if the hidden inputs are missing.
    """
    ordered_param_ids: List[int] = []
    seen: set[int] = set()
    for col_id_raw in request.POST.getlist('col_id[]'):
        try:
            col_id = int(col_id_raw)
        except (TypeError, ValueError):
            continue
        if col_id in seen:
            continue
        ordered_param_ids.append(col_id)
        seen.add(col_id)
    if ordered_param_ids:
        return ordered_param_ids
    prefix = 'col_value_'
    for key in request.POST.keys():
        if not key.startswith(prefix):
            continue
        suffix = key[len(prefix):]
        if suffix.endswith('[]'):
            suffix = suffix[:-2]
        try:
            col_id = int(suffix)
        except (TypeError, ValueError):
            continue
        if col_id in seen:
            continue
        ordered_param_ids.append(col_id)
        seen.add(col_id)
    return ordered_param_ids


def _gather_posted_parameter_values(request, param_map: Dict[int, Parameter]) -> Dict[int, List[str]]:
    """
    Build a mapping param_id -> list of submitted values. Missing fields are
    skipped so that disabled inputs keep their stored value.
    """
    values: Dict[int, List[str]] = {}
    for param_id in param_map.keys():
        field_name = f'col_value_{param_id}[]'
        if field_name not in request.POST:
            continue
        raw_values = request.POST.getlist(field_name)
        values[param_id] = ["" if value is None else str(value) for value in raw_values]
    return values


def _format_parameter_value(parameter: Parameter, raw_values: List[str]) -> str:
    """
    Convert a list of posted values into a single string suitable for keeping
    inside the pandas DataFrame column.
    """
    if not raw_values:
        return ''
    cleaned = [value.strip() for value in raw_values if value is not None]
    if not cleaned:
        return ''
    if len(cleaned) == 1:
        return cleaned[0]
    separator = parameter.array_separator if parameter.array_separator else ' '
    return separator.join(cleaned)


def _apply_values_to_dataframe(
    data_obj: pd.DataFrame,
    row_identifier: Optional[str],
    param_map: Dict[int, Parameter],
    posted_values: Dict[int, List[str]],
) -> bool:
    """
    Update the dataframe row identified by ``row_identifier`` with the posted
    parameter values. Returns True when any column was actually updated.
    """
    if row_identifier is None:
        return False
    mask = _resolve_row_mask(data_obj, row_identifier)
    if mask is None:
        logger.warning("Row %s not found in dataframe during update.", row_identifier)
        return False
    updated = False
    for param_id, parameter in param_map.items():
        if param_id not in posted_values:
            continue
        column_key = _ensure_dataframe_column(data_obj, parameter.id)
        if column_key is None:
            logger.warning("Column for parameter %s is missing while updating row %s.", parameter.id, row_identifier)
            continue
        formatted_value = _format_parameter_value(parameter, posted_values[param_id])
        data_obj.loc[mask, column_key] = formatted_value
        updated = True
    return updated


def _sync_linked_parameters(
    obj: Object,
    row_identifier: Optional[str],
    param_map: Dict[int, Parameter],
    posted_values: Dict[int, List[str]],
) -> None:
    """
    Persist row-level links for parameters that reference child objects.
    """
    if row_identifier is None:
        return
    parent_record_uid = object_data_service.resolve_record_uid_from_identifier(
        obj=obj,
        identifier=str(row_identifier),
    )
    for parameter in param_map.values():
        if not parameter.linked_object_id:
            continue
        link = Object_ParentObject.objects.filter(parent_object=obj, object=parameter.linked_object).first()
        if link is None:
            logger.warning(
                "Linked parameter %s references child object %s but link is missing.",
                parameter.id,
                parameter.linked_object_id,
            )
            continue
        values = posted_values.get(parameter.id, [])
        if parameter.data_type == 'ARRAY':
            ObjectLink_identificators.objects.filter(
                object_link=link,
                parent_object_identificator=parent_record_uid,
            ).delete()
            for value in values:
                cleaned = value.strip()
                if not cleaned:
                    continue
                child_record_uid = object_data_service.resolve_record_uid_from_identifier(
                    obj=parameter.linked_object,
                    identifier=cleaned,
                )
                ObjectLink_identificators.objects.update_or_create(
                    object_link=link,
                    parent_object_identificator=parent_record_uid,
                    object_identificator=child_record_uid,
                )
        else:
            choice = ''
            for value in values:
                cleaned = value.strip()
                if cleaned:
                    choice = cleaned
                    break
            if choice:
                child_record_uid = object_data_service.resolve_record_uid_from_identifier(
                    obj=parameter.linked_object,
                    identifier=choice,
                )
                ObjectLink_identificators.objects.update_or_create(
                    object_link=link,
                    parent_object_identificator=parent_record_uid,
                    defaults={'object_identificator': child_record_uid},
                )
            else:
                ObjectLink_identificators.objects.filter(
                    object_link=link,
                    parent_object_identificator=parent_record_uid,
                ).delete()


def _prepare_parameter_options(parameter: Parameter, data, selected) -> List[Dict[str, Union[str, bool]]]:
    """
    Convert helper data produced by ``get_parameters_data_by_ident`` into a
    uniform ``[{value, label, selected}]`` structure understood by templates.
    """
    options: List[Dict[str, Union[str, bool]]] = []
    if parameter.linked_object_id:
        selected_set = {str(item) for item in selected}
        for value, label in data:
            value_str = str(value)
            options.append({
                'value': value_str,
                'label': label,
                'selected': value_str in selected_set,
            })
    else:
        selected_indices = set(selected)
        for idx, label in data:
            label_str = str(label)
            options.append({
                'value': label_str,
                'label': label_str,
                'selected': idx in selected_indices,
            })
    return options


def _serialise_grouped_parameters(parameters_data) -> List[dict]:
    """
    Transform grouped parameter tuples into template-friendly dictionaries.
    """
    grouped_payload = []
    for category_name, entries in _group_parameters_data(parameters_data):
        fields = []
        for parameter, data, selected, current_data in entries:
            fields.append({
                'parameter': parameter,
                'id': parameter.id,
                'name': parameter.name,
                'data_type': parameter.data_type,
                'value': current_data,
                'is_identificator': parameter.identificator,
                'linked_object': parameter.linked_object,
                'linked_object_id': parameter.linked_object_id,
                'options': _prepare_parameter_options(parameter, data, selected),
                'has_options': bool(data),
                'allow_multiple': parameter.data_type == 'ARRAY',
            })
        grouped_payload.append({
            'name': category_name,
            'fields': fields,
        })
    return grouped_payload


def _collect_child_identifier_options(child_obj: Object):
    """
    Load identifier options for a child object together with user-facing warnings.
    """
    warnings: List[str] = []
    child_df, child_warnings = _safe_load_dataframe(
        getattr(child_obj, 'data', None),
        object_id=getattr(child_obj, 'id', None),
        object_instance=child_obj,
        allow_empty=True,
    )
    if child_warnings:
        warnings.extend(child_warnings)
    ident_list: List[Tuple[str, str]] = []
    if child_df is not None:
        child_df, _ = _ensure_record_uid_column(child_obj, child_df, persist=False)
        ident_param = Parameter.objects.filter(object=child_obj, identificator=True).first()
        if ident_param is None:
            warnings.append(f"У дочернего объекта {child_obj.id} не найден идентификаторный параметр.")
        else:
            ident_column_key = _resolve_dataframe_column(child_df, ident_param.id)
            if ident_column_key is None:
                warnings.append(
                    f"У дочернего объекта {child_obj.id} отсутствует колонка для идентификатора {ident_param.id}."
                )
            else:
                for _, row in child_df.iterrows():
                    child_ident = row.get(_RECORD_UID_COLUMN) or row.get('id_to_connect')
                    ident_value = row.get(ident_column_key)
                    if pd.isna(child_ident) or pd.isna(ident_value):
                        continue
                    ident_list.append((str(child_ident), str(ident_value).strip()))
    return ident_list, warnings


@login_required
@permission_required('database_manager.manage_object_data', raise_exception=True)
def update_element_to_object(request, pk):
    """
    Edit a specific row within an object's DataFrame. When the request is GET,
    the form is rendered with current values; when POST, the submitted values
    are persisted. This view also populates data for any linked child objects.
    """
    obj = get_object_or_404(Object, pk=pk)
    data_obj, load_warnings = _safe_load_dataframe(
        obj.data,
        object_id=obj.pk,
        object_instance=obj,
        allow_empty=True,
    )
    if load_warnings and request.method != 'POST':
        for warning in load_warnings:
            messages.warning(request, warning)
    if data_obj is None:
        data_obj = pd.DataFrame({'id_to_connect': [], _RECORD_UID_COLUMN: []})
    data_obj, _ = _ensure_record_uid_column(obj, data_obj, persist=False)
    param_ident_id = request.GET.get('id')
    row_identifier = str(param_ident_id) if param_ident_id not in (None, '') else None
    if request.method == 'POST':
        ordered_param_ids = _extract_param_ids_from_request(request)
        param_qs = Parameter.objects.filter(object=obj, id__in=ordered_param_ids).select_related('linked_object')
        param_map = {param.id: param for param in param_qs}
        posted_values = _gather_posted_parameter_values(request, param_map)
        row_updated = _apply_values_to_dataframe(data_obj, row_identifier, param_map, posted_values)
        row_data = None
        record_uid = str(row_identifier or '')
        legacy_id = str(row_identifier or '')
        if row_identifier:
            row_mask = _resolve_row_mask(data_obj, str(row_identifier))
            row_series = data_obj.loc[row_mask] if row_mask is not None else pd.DataFrame()
            if not row_series.empty:
                row_data = row_series.iloc[0].to_dict()
                record_uid = str(row_data.get(_RECORD_UID_COLUMN) or row_identifier)
                legacy_id = str(row_data.get('id_to_connect') or row_identifier)
        if row_updated:
            if _sql_source_of_truth_enabled():
                if row_data is not None:
                    object_data_service.dual_write_upsert(
                        obj=obj,
                        record_identifier=str(row_identifier),
                        row_data=row_data,
                        parameters=param_map.values(),
                        op='update',
                        record_uid=record_uid,
                        legacy_id_to_connect=legacy_id,
                    )
                object_data_service.run_secondary_file_write(
                    write_callback=lambda: _write_dataframe(obj.data, data_obj, object_instance=obj),
                    object_id=obj.id,
                    record_uid=record_uid,
                    op='update',
                )
            else:
                _write_dataframe(obj.data, data_obj, object_instance=obj)
                if row_data is not None:
                    object_data_service.dual_write_upsert(
                        obj=obj,
                        record_identifier=str(row_identifier),
                        row_data=row_data,
                        parameters=param_map.values(),
                        op='update',
                        record_uid=record_uid,
                        legacy_id_to_connect=legacy_id,
                    )
        _sync_linked_parameters(obj, row_identifier, param_map, posted_values)
        if row_identifier:
            object_link_service.sync_parent_row_links(parent_obj=obj, parent_identifier=str(row_identifier))
        redirect_url = reverse('get_object', args=[obj.id])
        if _is_api_request(request):
            return JsonResponse({'status': 'ok', 'redirect_url': redirect_url})
        if posted_values and not row_updated:
            messages.warning(
                request,
                "Не удалось обновить запись: строка с указанным идентификатором не найдена.",
            )
        return redirect(redirect_url)
    parameters_data = get_parameters_data_by_ident(obj, row_identifier)
    base_categories = _serialise_grouped_parameters(parameters_data)
    child_links = []
    child_relations = Object_ParentObject.objects.filter(parent_object=obj).select_related('object')
    for index, relation in enumerate(child_relations):
        if row_identifier is None:
            selected_ids: List[str] = []
            linked_row_id = None
        else:
            parent_identifiers = {str(row_identifier)}
            parent_record = object_data_service.sql_repo.get_record_by_uid_or_legacy(obj, str(row_identifier))
            if parent_record is not None:
                parent_identifiers.add(parent_record.record_uid)
                if parent_record.legacy_id_to_connect:
                    parent_identifiers.add(str(parent_record.legacy_id_to_connect))
            links_qs = ObjectLink_identificators.objects.filter(
                object_link=relation,
                parent_object_identificator__in=list(parent_identifiers),
            )
            if relation.link_type == 'multiple':
                selected_ids = [str(item.object_identificator) for item in links_qs]
            else:
                mapping = links_qs.first()
                selected_ids = [str(mapping.object_identificator)] if mapping else []
            linked_row_id = selected_ids[0] if selected_ids else None
        child_params = get_parameters_data_by_ident(relation.object, linked_row_id)
        child_categories = _serialise_grouped_parameters(child_params)
        ident_list, child_warnings = _collect_child_identifier_options(relation.object)
        if child_warnings and request.method != 'POST':
            for warning in child_warnings:
                messages.warning(request, warning)
        selected_set = set(selected_ids)
        options = [
            {
                'value': value,
                'label': label,
                'selected': value in selected_set,
            }
            for value, label in ident_list
        ]
        child_links.append({
            'link_id': relation.id,
            'object': relation.object,
            'is_active': index == 0,
            'is_multiple': relation.link_type == 'multiple',
            'link_type': relation.link_type,
            'options': options,
            'categories': child_categories,
            'selected_ids': selected_ids,
        })
    context = {
        'object': obj,
        'param_ident_id': row_identifier,
        'base_categories': base_categories,
        'child_links': child_links,
    }
    return render(request, 'database_manager/update_element_to_object.html', context)


@login_required
@permission_required('database_manager.manage_object_data', raise_exception=True)
def delete_element_to_object(request, pk):
    """
    Delete a specific row from an object's DataFrame. Expects the row identifier
    to be provided as the 'id' query parameter.
    """
    if request.method != 'POST':
        return HttpResponseBadRequest("Expected POST request.")
    obj = get_object_or_404(Object, pk=pk)
    data_obj, load_warnings = _safe_load_dataframe(obj.data, object_id=obj.pk, object_instance=obj)
    if load_warnings:
        logger.warning(
            "Warnings while loading data for delete_element_to_object (object %s): %s",
            obj.pk,
            "; ".join(load_warnings),
        )
    if data_obj is None:
        return HttpResponse(status=200)
    data_obj, _ = _ensure_record_uid_column(obj, data_obj, persist=False)
    param_ident_id = request.GET.get('id')
    if not param_ident_id:
        logger.warning("Missing id parameter when deleting element from object %s.", obj.pk)
        return HttpResponseBadRequest("Missing id parameter.")
    mask = _resolve_row_mask(data_obj, str(param_ident_id))
    if mask is None or not mask.any():
        logger.warning(
            "Row %s not found in object %s during delete request.",
            param_ident_id,
            obj.pk,
        )
        return HttpResponse(status=204)
    index = data_obj[mask].index
    data_obj.drop(index, inplace=True)
    if _sql_source_of_truth_enabled():
        object_data_service.dual_write_delete(obj=obj, record_identifier=str(param_ident_id))
        object_data_service.run_secondary_file_write(
            write_callback=lambda: _write_dataframe(obj.data, data_obj, object_instance=obj),
            object_id=obj.id,
            record_uid=str(param_ident_id),
            op='delete',
        )
    else:
        _write_dataframe(obj.data, data_obj, object_instance=obj)
        object_data_service.dual_write_delete(obj=obj, record_identifier=str(param_ident_id))
    return HttpResponse()


@login_required
@permission_required('database_manager.manage_object_structure', raise_exception=True)
def update_csv(request, pk):
    """
    Replace an object's DataFrame with data from a new CSV file. Maps CSV
    columns to parameters defined on the object. Any columns not mapped to a
    parameter are filled with NA. Generates new id_to_connect values for all
    rows.
    """
    obj = get_object_or_404(Object, pk=pk)
    if request.method == 'POST':
        old_df, load_warnings = _safe_load_dataframe(obj.data, object_id=obj.pk, object_instance=obj, allow_empty=True)
        if load_warnings:
            logger.warning(
                "Warnings while loading existing data before CSV update (object %s): %s",
                obj.pk,
                "; ".join(load_warnings),
            )
        if old_df is None:
            old_df = pd.DataFrame({'id_to_connect': [], _RECORD_UID_COLUMN: []})
        old_df, _ = _ensure_record_uid_column(obj, old_df, persist=False)

        parameters = list(Parameter.objects.filter(object=obj).order_by('id'))
        match_param_ids = [parameter.id for parameter in parameters if parameter.identificator]
        if not match_param_ids:
            raw_match_keys = getattr(obj, 'match_keys', None) or []
            for raw_key in raw_match_keys:
                try:
                    key_int = int(raw_key)
                except (TypeError, ValueError):
                    continue
                if any(parameter.id == key_int for parameter in parameters):
                    match_param_ids.append(key_int)
        if not match_param_ids:
            logger.warning("Object %s has no identificator/match_keys; all CSV rows will be treated as new.", obj.pk)

        def _build_match_key(row_dict: Dict[str, Any]) -> Optional[Tuple[str, ...]]:
            if not match_param_ids:
                return None
            result = []
            for param_id in match_param_ids:
                value = row_dict.get(str(param_id), '')
                if value is None:
                    value = ''
                result.append(str(value).strip())
            return tuple(result)

        old_key_to_row: Dict[Tuple[str, ...], Dict[str, Any]] = {}
        duplicated_old_keys: set = set()
        old_identifier_to_uid: Dict[str, str] = {}
        for _, row in old_df.iterrows():
            row_dict = row.to_dict()
            old_uid = str(row_dict.get(_RECORD_UID_COLUMN) or '').strip()
            old_legacy = str(row_dict.get('id_to_connect') or '').strip()
            if old_uid:
                old_identifier_to_uid[old_uid] = old_uid
            if old_legacy and old_uid:
                old_identifier_to_uid[old_legacy] = old_uid
            key = _build_match_key(row_dict)
            if key is None:
                continue
            if key in old_key_to_row:
                duplicated_old_keys.add(key)
                continue
            old_key_to_row[key] = row_dict

        csv_file = request.FILES['csv_file']
        incoming_df = pd.read_csv(csv_file, converters={i: str for i in range(100)})
        drop_column = request.POST.get('drop_column', '-1')
        if drop_column != '-1':
            incoming_df.dropna(subset=[drop_column], inplace=True)

        mapped_rows: List[Dict[str, Any]] = []
        matched_count = 0
        new_count = 0
        collision_count = 0
        consumed_old_keys: set = set()
        for _, csv_row in incoming_df.iterrows():
            row_payload: Dict[str, Any] = {}
            for parameter in parameters:
                csv_name = request.POST.get(f'csv_column_{parameter.id}', '')
                if not csv_name or csv_name == '-1':
                    row_payload[str(parameter.id)] = ''
                    continue
                raw_value = csv_row.get(csv_name, '')
                row_payload[str(parameter.id)] = '' if raw_value is None else str(raw_value).strip()
            key = _build_match_key(row_payload)
            matched_row = None
            if key is not None:
                if key in duplicated_old_keys or key in consumed_old_keys:
                    collision_count += 1
                else:
                    matched_row = old_key_to_row.get(key)
                    if matched_row is not None:
                        consumed_old_keys.add(key)
            if matched_row is not None:
                record_uid = str(matched_row.get(_RECORD_UID_COLUMN) or '').strip()
                legacy_id = str(matched_row.get('id_to_connect') or '').strip()
                if not record_uid:
                    record_uid = uuid.uuid4().hex
                if not legacy_id:
                    legacy_id = record_uid
                matched_count += 1
            else:
                record_uid = uuid.uuid4().hex
                legacy_id = record_uid
                new_count += 1
            row_payload[_RECORD_UID_COLUMN] = record_uid
            row_payload['id_to_connect'] = legacy_id
            mapped_rows.append(row_payload)

        new_df = pd.DataFrame(mapped_rows)
        if new_df.empty:
            new_df = pd.DataFrame(columns=[str(parameter.id) for parameter in parameters] + [_RECORD_UID_COLUMN, 'id_to_connect'])
        _write_dataframe(obj.data, new_df, object_instance=obj)

        record_uids = set(str(row.get(_RECORD_UID_COLUMN)) for row in mapped_rows if row.get(_RECORD_UID_COLUMN))
        for row in mapped_rows:
            object_data_service.dual_write_upsert(
                obj=obj,
                record_identifier=str(row.get('id_to_connect', '')),
                row_data=row,
                parameters=parameters,
                op='update_csv',
                record_uid=str(row.get(_RECORD_UID_COLUMN, '')),
                legacy_id_to_connect=str(row.get('id_to_connect', '')),
            )
        if getattr(settings, 'DBM_DUAL_WRITE', False):
            sql_records = object_data_service.sql_repo.list_records(obj)
            for sql_record in sql_records:
                if sql_record.record_uid not in record_uids:
                    object_data_service.sql_repo.delete_links_for_parent_record(sql_record)
                    object_data_service.sql_repo.delete_record(obj, sql_record.record_uid)

        # Convert previously stored legacy link identifiers to record_uid.
        for row_link in ObjectLink_identificators.objects.filter(object_link__parent_object=obj):
            parent_uid = old_identifier_to_uid.get(str(row_link.parent_object_identificator), str(row_link.parent_object_identificator))
            if parent_uid != row_link.parent_object_identificator:
                row_link.parent_object_identificator = parent_uid
                row_link.save(update_fields=['parent_object_identificator'])
        for row_link in ObjectLink_identificators.objects.filter(object_link__object=obj):
            child_uid = old_identifier_to_uid.get(str(row_link.object_identificator), str(row_link.object_identificator))
            if child_uid != row_link.object_identificator:
                row_link.object_identificator = child_uid
                row_link.save(update_fields=['object_identificator'])

        # Keep link table consistent with active rows after CSV replacement.
        if record_uids:
            ObjectLink_identificators.objects.filter(
                object_link__parent_object=obj
            ).exclude(parent_object_identificator__in=list(record_uids)).delete()
            ObjectLink_identificators.objects.filter(
                object_link__object=obj
            ).exclude(object_identificator__in=list(record_uids)).delete()
        if getattr(settings, 'DBM_DUAL_WRITE', False):
            parent_ids_for_sync = ObjectLink_identificators.objects.filter(
                object_link__parent_object=obj
            ).values_list('parent_object_identificator', flat=True).distinct()
            for parent_identifier in parent_ids_for_sync:
                object_link_service.sync_parent_row_links(
                    parent_obj=obj,
                    parent_identifier=str(parent_identifier),
                )

        logger.warning(
            "update_csv_match_stats %s",
            json.dumps(
                {
                    "object_id": obj.id,
                    "matched_count": matched_count,
                    "new_count": new_count,
                    "collision_count": collision_count,
                },
                ensure_ascii=False,
            ),
        )
        return HttpResponse(f'/database/get_object/{obj.id}')
    return render(request, 'database_manager/upload_csv.html')


@login_required
@permission_required('database_manager.delete_object', raise_exception=True)
@permission_required('database_manager.manage_object_structure', raise_exception=True)
def DeleteObject(request, pk):
    """
    Delete an entire object and any associated parameters and document links. Does
    not remove the underlying pickle file from disk.
    """
    try:
        obj = Object.objects.get(id=int(pk))
        DocumentPattern_Objects.objects.filter(object=obj).delete()
        Parameter.objects.filter(object=obj).delete()
        obj.delete()
        return HttpResponse()
    except Exception:
        return HttpResponseNotModified()


@require_http_methods(['POST'])
@login_required
@permission_required('database_manager.view_object', raise_exception=True)
def generate_excel_file(request, pk):
    """
    Generate an Excel file representing the object's identifier column and any
    associated documents. Returns the file as a binary response.
    """
    obj = get_object_or_404(Object, pk=pk)
    ident_param = Parameter.objects.get(object=obj, identificator=True)
    documents = DocumentPattern_Objects.objects.filter(object=obj)
    doc_list = [f"{doc.document.name}**{doc.document.id}**" for doc in documents]
    df_object, load_warnings = _safe_load_dataframe(obj.data, object_id=obj.pk, object_instance=obj)
    if load_warnings:
        logger.warning("Warnings while preparing Excel export for object %s: %s", obj.pk, '; '.join(load_warnings))
    if df_object is None:
        raise Http404
    ident_column_key = _resolve_dataframe_column(df_object, ident_param.id)
    if ident_column_key is None:
        ident_list = [f"**{row.get(_RECORD_UID_COLUMN) or row.get('id_to_connect')}" for _, row in df_object.iterrows()]
    else:
        ident_list = [
            f"{row[ident_column_key]}**{row.get(_RECORD_UID_COLUMN) or row.get('id_to_connect')}"
            for _, row in df_object.iterrows()
        ]
    dict_to_df = {f'{ident_param.id}**{ident_param.object.id}**': ident_list}
    df = pd.DataFrame({**dict_to_df, **{doc: ['-'] * len(ident_list) for doc in doc_list}})
    temp_dir = os.path.join(settings.MEDIA_ROOT, 'generated_files')
    os.makedirs(temp_dir, exist_ok=True)
    file_path = os.path.join(temp_dir, 'file_changer.xlsx')
    df.to_excel(file_path, index=False)
    if os.path.exists(file_path):
        return FileResponse(open(file_path, 'rb'), content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    raise Http404


@require_http_methods(['POST'])
@login_required
@permission_required('database_manager.manage_object_links', raise_exception=True)
def add_objects_links(request, pk):
    """
    Link the given object (`pk` refers to the parent) to one or more child
    objects. The request must contain a list of integer child IDs under the
    key `child_object_ids[]`. Duplicate relationships are ignored.
    """
    parent_object = get_object_or_404(Object, pk=pk)
    child_ids = request.POST.getlist('child_object_ids[]') or request.POST.getlist('child_object_idents[]')
    # Accept legacy key name 'child_object_idents[]' for backwards compatibility
    if not child_ids:
        return HttpResponseForbidden("No child object identifiers provided.")
    response_data = object_schema_service.add_object_links(
        parent_object=parent_object,
        child_ids=child_ids,
    )
    return JsonResponse(response_data)


def add_objects_link(object_id, object_child_id):
    """
    Backwards‑compatible helper for linking a parent and child object. Uses
    get_or_create to prevent duplicate relationships.
    """
    try:
        parent = Object.objects.get(id=int(object_id))
        child = Object.objects.get(id=int(object_child_id))
    except (Object.DoesNotExist, ValueError):
        return False
    if parent.id == child.id:
        return False
    Object_ParentObject.objects.get_or_create(parent_object=parent, object=child)
    return True


@require_http_methods(['POST'])
@login_required
@permission_required('database_manager.manage_object_links', raise_exception=True)
def save_row_link(request, object_link_id):
    """
    Create or update a row‑level link between a parent object's row and a
    child object's row. The `object_link_id` must reference an existing
    `Object_ParentObject` relationship. The request must include the parent
    row identifier (`parent_ident_id`) and the child row identifier
    (`child_ident_id`). If a link already exists for the given parent row, it
    is overwritten.
    """
    object_link = get_object_or_404(Object_ParentObject, pk=object_link_id)
    parent_ident_id = request.POST.get('parent_ident_id')
    child_ident_id = request.POST.get('child_ident_id')
    if not parent_ident_id or not child_ident_id:
        message = "Both parent_ident_id and child_ident_id must be provided."
        logger.warning("Row link save rejected: %s (object_link_id=%s, user=%s)", message, object_link_id, request.user)
        return HttpResponseBadRequest(message)
    parent_record_uid = object_data_service.resolve_record_uid_from_identifier(
        obj=object_link.parent_object,
        identifier=str(parent_ident_id),
    )
    child_record_uid = object_data_service.resolve_record_uid_from_identifier(
        obj=object_link.object,
        identifier=str(child_ident_id),
    )
    # Update or create the row mapping
    ObjectLink_identificators.objects.update_or_create(
        object_link=object_link,
        parent_object_identificator=parent_record_uid,
        defaults={'object_identificator': child_record_uid},
    )
    object_link_service.sync_parent_row_links(
        parent_obj=object_link.parent_object,
        parent_identifier=parent_record_uid,
    )
    return HttpResponse('')


@require_http_methods(['POST'])
@login_required
@permission_required('database_manager.manage_object_links', raise_exception=True)
def delete_object_link(request, pk):
    """
    Delete an Object_ParentObject link and any associated row-level links.
    Expects a POST request. Returns a 204-like empty response on success.
    """
    link = get_object_or_404(Object_ParentObject, pk=pk)
    # Delete associated parameter
    Parameter.objects.filter(object=link.parent_object, linked_object=link.object).delete()
    # Delete any row-level links referencing this object link
    ObjectLink_identificators.objects.filter(object_link=link).delete()
    object_link_service.delete_object_link(link)
    link.delete()
    return HttpResponse('')


