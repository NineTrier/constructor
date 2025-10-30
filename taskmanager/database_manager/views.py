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
from django.urls import reverse_lazy
from django.conf import settings
from django.db import connection
from django.db.models import Q, Max

logger = logging.getLogger(__name__)

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
    links_data = []
    for link in Object_ParentObject.objects.filter(parent_object=parent_obj):
        child_obj = link.object
        child_ids = list(
            ObjectLink_identificators.objects.filter(
                object_link=link,
                parent_object_identificator=parent_ident_id
            ).values_list('object_identificator', flat=True)
        )
        links_data.append({
            'child_object_id': child_obj.id,
            'child_object_name': child_obj.name,
            'link_type': link.link_type,
            'child_ident_ids': child_ids,
            'link_id': link.id,
        })
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
    # Initialise an empty DataFrame with only the id_to_connect column
    df = pd.DataFrame({"id_to_connect": []})
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
    warnings = []
    idents = []
    param_ident = Parameter.objects.filter(object=obj, identificator=True).first()
    data_obj, load_warnings = _safe_load_dataframe(
        obj.data,
        object_id=obj.pk,
        object_instance=obj,
    )
    if load_warnings:
        warnings.extend(load_warnings)
    if data_obj is not None:
        if not isinstance(data_obj, pd.DataFrame):
            logger.warning(
                "Data for object %s is of type %s instead of pandas.DataFrame",
                obj.pk,
                type(data_obj),
            )
            warnings.append("Файл с данными объекта имеет неподдерживаемый формат, список идентификаторов будет пуст.")
        elif param_ident is None:
            warnings.append("Для объекта не найден параметр с флагом идентификатора.")
        else:
            id_column_key = _resolve_dataframe_column(data_obj, 'id_to_connect')
            param_column_key = _resolve_dataframe_column(data_obj, param_ident.id)
            missing_columns = []
            if id_column_key is None:
                missing_columns.append('id_to_connect')
            if param_column_key is None:
                missing_columns.append(str(param_ident.id))
            if missing_columns:
                logger.warning(
                    "Object %s data frame is missing columns: %s",
                    obj.pk,
                    ', '.join(str(col) for col in missing_columns),
                )
                warnings.append(
                    "В данных объекта отсутствуют обязательные столбцы ({0}), список идентификаторов будет пуст."
                    .format(', '.join(str(col) for col in missing_columns))
                )
            else:
                for index, row in data_obj.sort_index(axis=0, ascending=False).iterrows():
                    ident_value = row.get(id_column_key)
                    if pd.isna(ident_value):
                        logger.warning(
                            "Row %s in object %s skipped: empty id_to_connect value",
                            index,
                            obj.pk,
                        )
                        warnings.append(f"Строка {index} пропущена: пустое значение id_to_connect.")
                        continue
                    param_value = row.get(param_column_key)
                    if pd.isna(param_value):
                        logger.warning(
                            "Row %s in object %s has empty value for identifier parameter %s",
                            index,
                            obj.pk,
                            param_ident.id,
                        )
                        param_value = ''
                    idents.append({'id': str(ident_value), 'param_ident': '' if param_value is None else str(param_value)})
    if warnings and request.method != 'POST':
        for warning in warnings:
            messages.warning(request, warning)
    # Build a mapping of parameter metadata for the base object.  We
    # additionally prepare a mapping of child object parameter IDs to
    # their names so that the front‑end can render human‑readable labels
    # when displaying row data from linked objects.  Without this the
    # client would need to fetch the parameter names separately for each
    # child, leading to additional network round trips.  By computing
    # `child_params` here, we encapsulate that logic server‑side.
    # child_params structure: { child_obj_id: {param_id: param_name, ...}, ... }
    child_params = {}
    for link in Object_ParentObject.objects.filter(parent_object=obj):
        child_obj = link.object
        # Only build the mapping once per child object
        if child_obj.id not in child_params:
            mapping = {p.id: p.name for p in Parameter.objects.filter(object=child_obj)}
            child_params[child_obj.id] = mapping
    param_qs = Parameter.objects.filter(object=obj)
    logger.debug(
        "Rendering object %s with parameters %s",
        obj.pk,
        list(param_qs.values_list('id', flat=True)),
    )
    context = {
        'object': obj,
        'parameters': sorted(param_qs, key=lambda x: x.id),
        'idents': idents,
        'documents': [doc.document for doc in DocumentPattern_Objects.objects.filter(object=obj)],
        'child_params': child_params,
        'warnings': warnings,
    }
    if request.method == 'POST':
        return HttpResponse(json.dumps({
            'object': obj.to_dict(),
            'idents': idents,
            'documents': [{doc.document.id: doc.document.name} for doc in DocumentPattern_Objects.objects.filter(object=obj)],
            'warnings': warnings,
        }))
    return render(request, 'database_manager/get_object.html', context)


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
    data_obj, warnings = _safe_load_dataframe(obj.data, object_id=obj.pk, object_instance=obj)
    if warnings:
        logger.info(
            "Warnings while loading data for object %s during row fetch: %s",
            obj.pk,
            "; ".join(warnings),
        )
    if data_obj is None:
        return JsonResponse([], safe=False)
    id_to_connect = request.POST.get("param_ident_id")
    if not id_to_connect:
        logger.warning("Missing param_ident_id in request for object %s.", obj.pk)
        return JsonResponse([], safe=False)
    if 'id_to_connect' not in data_obj.columns:
        logger.warning(
            "Object %s data frame missing 'id_to_connect' column when fetching value %s.",
            obj.pk,
            id_to_connect,
        )
        return JsonResponse([], safe=False)
    try:
        data = data_obj.loc[data_obj['id_to_connect'] == id_to_connect]
    except KeyError:
        logger.exception(
            "Failed to filter DataFrame by id_to_connect for object %s.",
            obj.pk,
        )
        return JsonResponse([], safe=False)
    if data.empty:
        logger.info(
            "Row with id_to_connect=%s not found for object %s.",
            id_to_connect,
            obj.pk,
        )
        return JsonResponse([], safe=False)
    row = data.iloc[0]
    response_record = {'id_to_connect': id_to_connect}
    parameters = Parameter.objects.filter(object=obj).order_by('id')
    for param in parameters:
        column_key = _resolve_dataframe_column(data_obj, param.id)
        key_name = str(param.id)
        if column_key is None:
            warnings.append(
                f"Для параметра '{param.name}' (ID {param.id}) отсутствует столбец в данных объекта."
            )
            logger.warning(
                "Missing column for parameter %s (object %s, parameter id=%s).",
                param.name,
                obj.pk,
                param.id,
            )
            response_record[key_name] = {'data_type': param.data_type, 'value': ''}
            continue
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
        if param.data_type == 'ARRAY':
            response_record[key_name] = {
                'data_type': param.data_type,
                'value': str(safe_val).split(param.array_separator) if safe_val else [],
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
        else:
            response_record[key_name] = {
                'data_type': param.data_type,
                'value': safe_val if safe_val is not None else '',
            }
    logger.info(
        "Prepared row payload for object %s (id_to_connect=%s): %s",
        obj.pk,
        id_to_connect,
        response_record,
    )
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

    safe_record = sanitize(response_record)
    return JsonResponse([safe_record], safe=False)


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
        df['id_to_connect'] = [f"{_}_{uuid.uuid4().hex}" for _ in range(df.shape[0])]
        _write_dataframe(obj.data, df, object_instance=obj)
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
        logger.info(
            "Warnings while loading data for update_object (object %s): %s",
            obj.pk,
            "; ".join(load_warnings),
        )
        if request.method != 'POST':
            for warning in load_warnings:
                messages.warning(request, warning)
    if data_obj is None:
        data_obj = pd.DataFrame({'id_to_connect': []})
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
    fallback_value = pd.DataFrame({'id_to_connect': []}) if allow_empty else None
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
            logger.info(
                "Warnings while loading linked parameter data in get_parameter_data (object %s -> child %s): %s",
                parameter.object_id,
                getattr(parameter.linked_object, 'id', None),
                "; ".join(child_warnings),
            )
        if child_df is None:
            return []
        ident_param = Parameter.objects.filter(object=parameter.linked_object, identificator=True).first()
        if ident_param:
            ident_column_key = _resolve_dataframe_column(child_df, ident_param.id)
            if ident_column_key is not None:
                ident_list = []
                for _, row in child_df.iterrows():
                    child_ident = row.get('id_to_connect')
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
        logger.info(
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
        logger.info(
            "Warnings while loading data for add_element_to_object (object %s): %s",
            obj.pk,
            "; ".join(load_warnings),
        )
        if request.method != 'POST':
            for warning in load_warnings:
                messages.warning(request, warning)
    if data_obj is None:
        data_obj = pd.DataFrame({'id_to_connect': []})
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
                        logger.info(
                            "Warnings while loading child object data for validation (parent %s -> child %s): %s",
                            obj.pk,
                            getattr(child_obj, 'id', None),
                            "; ".join(child_warnings),
                        )
                        for warning in child_warnings:
                            messages.warning(request, warning)
                    if child_df is not None:
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
                                    child_ident = row.get('id_to_connect')
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
        new_row_id = f"{data_obj.shape[0]}_{uuid.uuid4().hex}"
        new_row = {"id_to_connect": new_row_id}
        # Collect values for each parameter.  The form sends parallel lists
        # ``col_id[]`` and ``col_value_<id>[]`` for each parameter.
        col_ids_raw = request.POST.getlist('col_id[]')
        col_ids = [int(x) for x in col_ids_raw if x]
        for col_id in col_ids:
            # For array parameters multiple values may be submitted
            col_values = request.POST.getlist(f'col_value_{col_id}[]')
            try:
                parameter = Parameter.objects.get(id=col_id)
            except Parameter.DoesNotExist:
                continue
            column_key = _ensure_dataframe_column(data_obj, parameter.id)
            if not col_values:
                new_row[column_key] = ''
            else:
                new_row[column_key] = (
                    str(col_values[0]) if len(col_values) == 1 else parameter.array_separator.join(col_values)
                )
        # Append to DataFrame and persist
        data_obj = pd.concat([data_obj, pd.DataFrame([new_row])], ignore_index=True)
        _write_dataframe(obj.data, data_obj, object_instance=obj)
        # After saving the row, record any selected child links from linked parameters
        for parameter in Parameter.objects.filter(object=obj, linked_object__isnull=False):
            link = Object_ParentObject.objects.get(parent_object=obj, object=parameter.linked_object)
            col_values = request.POST.getlist(f'col_value_{parameter.id}[]')
            if parameter.data_type == 'ARRAY':
                # Multiple links
                ObjectLink_identificators.objects.filter(object_link=link, parent_object_identificator=new_row_id).delete()
                for val in col_values:
                    if val:
                        ObjectLink_identificators.objects.update_or_create(
                            object_link=link,
                            parent_object_identificator=new_row_id,
                            object_identificator=val,
                        )
            else:
                # Single link
                child_value = col_values[0] if col_values else ''
                if child_value:
                    ObjectLink_identificators.objects.update_or_create(
                        object_link=link,
                        parent_object_identificator=new_row_id,
                        defaults={'object_identificator': child_value},
                    )
                else:
                    ObjectLink_identificators.objects.filter(
                        object_link=link,
                        parent_object_identificator=new_row_id
                    ).delete()
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
            logger.info(
                "Warnings while loading child object data for preview (parent %s -> child %s): %s",
                obj.pk,
                getattr(child_obj, 'id', None),
                "; ".join(child_warnings),
            )
            for warning in child_warnings:
                messages.warning(request, warning)
        if child_df is not None:
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
                        child_ident = row.get('id_to_connect')
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

    for candidate in candidates:
        if candidate in df.columns:
            return candidate

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


def get_parameters_data_by_ident(obj: Object, param_ident_id) -> list:
    """
    Load a DataFrame and return, for each Parameter, the available values and
    which indices should be selected for the given row (`param_ident_id`).
    """
    data_obj, load_warnings = _safe_load_dataframe(obj.data, object_id=obj.pk, object_instance=obj)
    if load_warnings:
        logger.info(
            "Warnings while loading data for get_parameters_data_by_ident (object %s): %s",
            obj.pk,
            "; ".join(load_warnings),
        )
    if data_obj is None:
        return []
    if param_ident_id is None:
        row = None
    else:
        if 'id_to_connect' not in data_obj.columns:
            logger.warning(
                "Data frame for object %s missing 'id_to_connect' column when querying row %s.",
                obj.pk,
                param_ident_id,
            )
            row = None
        else:
            try:
                row = data_obj[data_obj['id_to_connect'] == param_ident_id]
            except KeyError:
                logger.exception(
                    "Failed to locate row %s in object %s due to missing column.",
                    param_ident_id,
                    obj.pk,
                )
                row = None
    parameters_data = []
    for parameter in Parameter.objects.filter(object=obj):
        current_data = ""
        column_key = _ensure_dataframe_column(data_obj, parameter.id)
        if row is not None and not row.empty and column_key is not None:
            try:
                current_data = str(row[column_key].iloc[0]).strip()
            except (KeyError, IndexError):
                current_data = ""
        current_data = current_data if current_data not in ['None', 'nan', None, '<NA>'] else ""
        if parameter.linked_object:
            # For linked parameters, get selected ids from links
            link = Object_ParentObject.objects.get(parent_object=obj, object=parameter.linked_object)
            if parameter.data_type == 'ARRAY':
                selected_ids = [li.object_identificator for li in ObjectLink_identificators.objects.filter(object_link=link, parent_object_identificator=param_ident_id)]
            else:
                li = ObjectLink_identificators.objects.filter(object_link=link, parent_object_identificator=param_ident_id).first()
                selected_ids = [li.object_identificator] if li else []
            # Get data as list of (id, label)
            child_df, child_warnings = _safe_load_dataframe(
                getattr(parameter.linked_object, 'data', None),
                object_id=getattr(parameter.linked_object, 'id', None),
                object_instance=parameter.linked_object,
                allow_empty=True,
            )
            if child_warnings:
                logger.info(
                    "Warnings while loading linked parameter data (object %s -> child %s): %s",
                    obj.pk,
                    getattr(parameter.linked_object, 'id', None),
                    "; ".join(child_warnings),
                )
            data = []
            if child_df is not None:
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
                            child_ident = row.get('id_to_connect')
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
                parameters_data.append((parameter, param_data_indexed, find_in_params_data(param_data_indexed, current_data, parameter), current_data))
    return parameters_data


@login_required
@permission_required('database_manager.manage_object_data', raise_exception=True)
def update_element_to_object(request, pk):
    """
    Edit a specific row within an object's DataFrame. When the request is GET,
    the form is rendered with current values; when POST, the submitted values
    are persisted. This view also populates data for any linked child objects.
    """
    obj = get_object_or_404(Object, pk=pk)
    data_obj, load_warnings = _safe_load_dataframe(obj.data, object_id=obj.pk, object_instance=obj, allow_empty=True)
    if load_warnings:
        logger.info(
            "Warnings while loading data for update_element_to_object (object %s): %s",
            obj.pk,
            "; ".join(load_warnings),
        )
        if request.method != 'POST':
            for warning in load_warnings:
                messages.warning(request, warning)
    if data_obj is None:
        data_obj = pd.DataFrame({'id_to_connect': []})
    if 'id_to_connect' not in data_obj.columns:
        data_obj['id_to_connect'] = pd.NA
    param_ident_id = request.GET.get('id')
    if request.method == 'POST':
        col_ids = list(map(int, request.POST.getlist('col_id[]')))
        for col_id in col_ids:
            col_values = request.POST.getlist(f'col_value_{col_id}[]')
            parameter = Parameter.objects.get(id=col_id)
            column_key = _ensure_dataframe_column(data_obj, parameter.id)
            data_obj.loc[data_obj['id_to_connect'] == param_ident_id, column_key] = (
                str(col_values[0]) if len(col_values) == 1 else parameter.array_separator.join(col_values)
            )
        _write_dataframe(obj.data, data_obj, object_instance=obj)
        # Update child links from linked parameters
        for parameter in Parameter.objects.filter(object=obj, linked_object__isnull=False):
            link = Object_ParentObject.objects.get(parent_object=obj, object=parameter.linked_object)
            col_values = request.POST.getlist(f'col_value_{parameter.id}[]')
            if parameter.data_type == 'ARRAY':
                # Multiple links
                ObjectLink_identificators.objects.filter(object_link=link, parent_object_identificator=param_ident_id).delete()
                for val in col_values:
                    if val:
                        ObjectLink_identificators.objects.update_or_create(
                            object_link=link,
                            parent_object_identificator=param_ident_id,
                            object_identificator=val,
                        )
            else:
                # Single link
                child_value = col_values[0] if col_values else ''
                if child_value:
                    ObjectLink_identificators.objects.update_or_create(
                        object_link=link,
                        parent_object_identificator=param_ident_id,
                        defaults={'object_identificator': child_value},
                    )
                else:
                    ObjectLink_identificators.objects.filter(
                        object_link=link,
                        parent_object_identificator=param_ident_id
                    ).delete()
        return redirect('get_object', pk=obj.id)
    parameters_data = get_parameters_data_by_ident(obj, param_ident_id)
    # Group parameters by categories for the base object
    parameters_group_data = _group_parameters_data(parameters_data)
    parameters_objects = Object_ParentObject.objects.filter(parent_object=obj)
    parameters_objects_data = []
    parameters_objects_params_group_data = []
    # List of tuples: (object_link_id, child_object, ident_list, selected_ident) for row-level linking
    parameters_objects_idents = []
    for idx, po in enumerate(parameters_objects):
        if po.link_type == 'multiple':
            linked_links = ObjectLink_identificators.objects.filter(
                object_link=po,
                parent_object_identificator=param_ident_id,
            )
            selected_ids = [link.object_identificator for link in linked_links]
            linked_param_ident_id = selected_ids[0] if selected_ids else None
        else:
            parameters_link = ObjectLink_identificators.objects.filter(
                object_link=po,
                parent_object_identificator=param_ident_id,
            ).first()
            selected_ids = [parameters_link.object_identificator] if parameters_link else []
            linked_param_ident_id = parameters_link.object_identificator if parameters_link else None
        po_params_data = get_parameters_data_by_ident(po.object, linked_param_ident_id)
        po_group_data = _group_parameters_data(po_params_data)
        parameters_objects_data.append((po.object, True if idx == 0 else False))
        parameters_objects_params_group_data.append((po.object, po_group_data, True if idx == 0 else False))
        # Build list of identifiers for the child object
        child_df, child_warnings = _safe_load_dataframe(
            getattr(po.object, 'data', None),
            object_id=getattr(po.object, 'id', None),
            object_instance=po.object,
            allow_empty=True,
        )
        if child_warnings:
            logger.info(
                "Warnings while loading identifiers for update_element_to_object (parent %s -> child %s): %s",
                obj.pk,
                getattr(po.object, 'id', None),
                "; ".join(child_warnings),
            )
            if request.method != 'POST':
                for warning in child_warnings:
                    messages.warning(request, warning)
        ident_list = []
        if child_df is not None:
            ident_param = Parameter.objects.filter(object=po.object, identificator=True).first()
            if ident_param is not None:
                ident_column_key = _resolve_dataframe_column(child_df, ident_param.id)
                if ident_column_key is None:
                    logger.warning(
                        "Child object %s data missing identifier column %s.",
                        getattr(po.object, 'id', None),
                        ident_param.id,
                    )
                else:
                    for _, row in child_df.iterrows():
                        child_ident = row.get('id_to_connect')
                        ident_value = row.get(ident_column_key)
                        if pd.isna(child_ident) or pd.isna(ident_value):
                            continue
                        ident_list.append((str(child_ident), str(ident_value).strip()))
        parameters_objects_idents.append((po.id, po.object, ident_list, selected_ids, po.link_type))
    return render(request, 'database_manager/update_element_to_object.html', context={
        'object': obj,
        'parameters_group_data': parameters_group_data,
        'param_ident_id': param_ident_id,
        'parameters_objects': parameters_objects_data,
        'parameters_objects_params_group_data': parameters_objects_params_group_data,
        'parameters_objects_idents': parameters_objects_idents,
    })


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
        logger.info(
            "Warnings while loading data for delete_element_to_object (object %s): %s",
            obj.pk,
            "; ".join(load_warnings),
        )
    if data_obj is None:
        return HttpResponse(status=200)
    param_ident_id = request.GET.get('id')
    if not param_ident_id:
        logger.warning("Missing id parameter when deleting element from object %s.", obj.pk)
        return HttpResponseBadRequest("Missing id parameter.")
    if 'id_to_connect' not in data_obj.columns:
        logger.warning(
            "Data frame for object %s missing 'id_to_connect' column during delete.",
            obj.pk,
        )
        return HttpResponse(status=200)
    try:
        index = data_obj[data_obj['id_to_connect'] == param_ident_id].index
    except KeyError:
        logger.exception(
            "Failed to locate row %s in object %s while deleting (missing column).",
            param_ident_id,
            obj.pk,
        )
        return HttpResponse(status=200)
    if index.empty:
        logger.info(
            "Row %s not found in object %s during delete request.",
            param_ident_id,
            obj.pk,
        )
        return HttpResponse(status=204)
    data_obj.drop(index, inplace=True)
    _write_dataframe(obj.data, data_obj, object_instance=obj)
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
        _, load_warnings = _safe_load_dataframe(obj.data, object_id=obj.pk, object_instance=obj)
        if load_warnings:
            logger.info(
                "Warnings while loading existing data before CSV update (object %s): %s",
                obj.pk,
                "; ".join(load_warnings),
            )
        csv_file = request.FILES['csv_file']
        df = pd.read_csv(csv_file, converters={i: str for i in range(100)})
        drop_column = request.POST.get('drop_column', '-1')
        if drop_column != '-1':
            df.dropna(subset=[drop_column], inplace=True)
        new_df = {}
        for parameter in sorted(Parameter.objects.filter(object=obj), key=lambda x: x.id):
            column_key = str(parameter.id)
            csv_name = request.POST.get(f'csv_column_{parameter.id}', '')
            if not csv_name or csv_name == '-1':
                new_df[column_key] = pd.NA
                continue
            new_df[column_key] = df[csv_name].map(lambda x: str(x).strip()).tolist()
        new_df['id_to_connect'] = [f"{_}_{uuid.uuid4().hex}" for _ in range(df.shape[0])]
        new_df = pd.DataFrame(new_df)
        _write_dataframe(obj.data, new_df, object_instance=obj)
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
        logger.info("Warnings while preparing Excel export for object %s: %s", obj.pk, '; '.join(load_warnings))
    if df_object is None:
        raise Http404
    ident_column_key = _resolve_dataframe_column(df_object, ident_param.id)
    if ident_column_key is None:
        ident_list = [f"**{row['id_to_connect']}" for _, row in df_object.iterrows()]
    else:
        ident_list = [f"{row[ident_column_key]}**{row['id_to_connect']}" for _, row in df_object.iterrows()]
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
    links = []
    for child_id_str in child_ids:
        try:
            child_id = int(child_id_str)
        except ValueError:
            continue
        if child_id == parent_object.id:
            continue  # skip linking an object to itself
        child_obj = Object.objects.filter(id=child_id).first()
        if not child_obj:
            continue
        # Use get_or_create to avoid duplicate links
        link, created = Object_ParentObject.objects.get_or_create(parent_object=parent_object, object=child_obj)
        param = None
        if created:
            # Auto-create a parameter for the link
            data_type = 'ARRAY' if link.link_type == 'multiple' else 'TXTS'
            param = Parameter.objects.create(
                object=parent_object,
                name=f'Связь с {child_obj.name}',
                data_type=data_type,
                linked_object=child_obj,
                order=0
            )
            parent_df, parent_warnings = _safe_load_dataframe(
                parent_object.data,
                object_id=parent_object.id,
                object_instance=parent_object,
                allow_empty=True,
            )
            if parent_warnings:
                logger.info(
                    "Warnings while loading parent data during link creation (parent %s -> child %s): %s",
                    parent_object.id,
                    child_obj.id,
                    "; ".join(parent_warnings),
                )
            if parent_df is None:
                parent_df = pd.DataFrame({'id_to_connect': []})
            _ensure_dataframe_column(parent_df, param.id)
            try:
                _write_dataframe(parent_object.data, parent_df, object_instance=parent_object)
            except Exception:
                logger.exception(
                    "Failed to persist column for new linked parameter %s on object %s",
                    param.id,
                    parent_object.id,
                )
        links.append({'link': link, 'param': param})
    response_data = {'links': []}
    for item in links:
        link_data = {'id': item['link'].id, 'child_name': item['link'].object.name}
        if item['param']:
            link_data['param'] = {
                'id': item['param'].id,
                'name': item['param'].name,
                'data_type': item['param'].data_type,
                'linked_object_id': item['param'].linked_object.id
            }
        response_data['links'].append(link_data)
    return HttpResponse(json.dumps(response_data))


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
    # Update or create the row mapping
    ObjectLink_identificators.objects.update_or_create(
        object_link=object_link,
        parent_object_identificator=parent_ident_id,
        defaults={'object_identificator': child_ident_id},
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
    link.delete()
    return HttpResponse('')

