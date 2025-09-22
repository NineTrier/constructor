from django.shortcuts import render, get_object_or_404, redirect
from django.views.generic import CreateView
from django.contrib.auth import views, models  # noqa: F401  # imported for side effects
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
from django.views.decorators.csrf import csrf_exempt
from django.urls import reverse_lazy
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
import pandas as pd
from sqlalchemy.engine import create_engine  # noqa: F401  # imported for side effects
from sqlalchemy import inspect, text  # noqa: F401  # imported for side effects


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

def upload_csv_and_get_columns(request):
    """
    Read a CSV file sent via POST and return its column names joined by
    semicolons. This is a lightweight helper used by the front‑end to
    dynamically build forms when uploading new objects.
    """
    if request.method == 'POST':
        csv_file = request.FILES['csv_file']
        df = pd.read_csv(csv_file, converters={i: str for i in range(100)})
        return HttpResponse(";".join(str(x) for x in df.columns.tolist()))


def get_object_parameters(request, pk):
    """
    Return JSON with the parameters defined for the given object. Each
    parameter entry includes its database ID, name and whether it is marked
    as the identifier column.
    """
    obj = get_object_or_404(Object, pk=pk)
    if request.method == 'POST':
        parameters = sorted(Parameter.objects.filter(object=obj), key=lambda x: x.id)
        result = []
        for par in parameters:
            result.append({'id': par.id, 'name': par.name, 'identificator': par.identificator})
        return HttpResponse(json.dumps({'data': result}))


# New endpoint: return child links for a given parent object and row identifier
@csrf_exempt
def get_row_links(request, pk):
    """
    Given a parent object ID (pk) and a parent row identifier (parent_ident_id) in
    POST data, return a JSON structure describing the linked child rows. For each
    Object_ParentObject record associated with the parent object, this returns
    the child object ID, name, link type and a list of identifiers selected
    for the given parent row.  If no links exist, returns an empty list.
    """
    parent_obj = get_object_or_404(Object, pk=pk)
    if request.method == 'POST':
        parent_ident_id = request.POST.get('parent_ident_id')
        if not parent_ident_id:
            return HttpResponse(json.dumps({'links': []}))
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
        return HttpResponse(json.dumps({'links': links_data}))
    return HttpResponse(status=405)


def view_data(request):
    """
    Render a CSV preview using Pandas. If a drop_column is provided in the
    POST body, rows with missing values in that column are filtered out.
    """
    if request.method == 'POST':
        csv_file = request.FILES['csv_file']
        df = pd.read_csv(csv_file, converters={i: str for i in range(100)})
        drop_column = request.POST.get('drop_column', '-1')
        if drop_column != '-1':
            df = df.dropna(subset=[drop_column])
        return HttpResponse(df.to_html())


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


def get_objects_to_connect(request):
    """
    Return JSON describing all available objects. This is used by front‑end
    components when linking objects.
    """
    if request.method == 'POST':
        return HttpResponse(json.dumps({'object': [{'id': x.id, 'name': x.name} for x in Object.objects.all()]}))


@csrf_exempt
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
    relative_path = os.path.join('dataframes', f'{file_id}.pkl')
    new_object.data.name = relative_path
    new_object.save()
    # Initialise an empty DataFrame with only the id_to_connect column
    df = pd.DataFrame({"id_to_connect": []})
    # Build the absolute file path using obj.data.path (which resolves
    # settings.MEDIA_ROOT + relative_path)
    file_path = new_object.data.path
    os.makedirs(os.path.dirname(file_path), exist_ok=True)
    df.to_pickle(file_path)
    return HttpResponse(json.dumps({'id': new_object.id}))


def get_object(request, pk):
    """
    Display data from an object as well as its identifier values and
    associated documents. When called via POST, returns a JSON payload
    instead of rendering the template.
    """
    obj = get_object_or_404(Object, pk=pk)
    try:
        with open(obj.data.path, 'rb') as f:
            data_obj = pickle.load(f)
        param_ident = Parameter.objects.filter(object=obj, identificator=True).first()
        idents = []
        if param_ident is not None:
            for i, row in data_obj.sort_index(axis=0, ascending=False).iterrows():
                idents.append({'id': row['id_to_connect'], 'param_ident': row[param_ident.id]})
    except (Object.DoesNotExist, Parameter.DoesNotExist, KeyError, IndexError, FileNotFoundError, pickle.UnpicklingError):
        return HttpResponse(status=404)
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
    context = {
        'object': obj,
        'parameters': sorted(Parameter.objects.filter(object=obj), key=lambda x: x.id),
        'idents': idents,
        'documents': [doc.document for doc in DocumentPattern_Objects.objects.filter(object=obj)],
        'child_params': child_params,
    }
    if request.method == 'POST':
        return HttpResponse(json.dumps({
            'object': obj.to_dict(),
            'idents': idents,
            'documents': [{doc.document.id: doc.document.name} for doc in DocumentPattern_Objects.objects.filter(object=obj)],
        }))
    return render(request, 'database_manager/get_object.html', context)


def post_data_from_object(request, pk):
    """
    Given an identifier (id_to_connect) return all values for that row.
    The response contains information about each parameter, including its data
    type and appropriately parsed value.
    """
    obj = get_object_or_404(Object, pk=pk)
    if request.method == 'POST':
        with open(obj.data.path, 'rb') as f:
            data_obj = pickle.load(f)
        id_to_connect = request.POST.get("param_ident_id")
        param_ident = Parameter.objects.filter(object=obj, identificator=True).first()
        # Retrieve the row matching id_to_connect
        data = data_obj.loc[data_obj['id_to_connect'] == id_to_connect]
        if data.empty:
            return HttpResponse(status=404)
        data_dict = data.to_dict(orient='records')
        for key, value in data_dict[0].items():
            try:
                # Keys that aren't integers correspond to DataFrame index columns like id_to_connect
                param_id = int(key)
                param = Parameter.objects.get(id=param_id)
            except (ValueError, Parameter.DoesNotExist):
                continue
            # Convert the value based on its data type
            # Normalise missing/invalid values: treat NaN, None, 'nan', 'None', '<NA>' as empty
            safe_val = value
            try:
                # Check for float NaN
                if isinstance(value, float) and (value != value):
                    safe_val = ''
                elif str(value).strip().lower() in ['nan', 'none', '<na>', '']:
                    safe_val = ''
            except Exception:
                pass
            if param.data_type == 'ARRAY':
                # Split only if there is a value; otherwise return empty list
                if safe_val:
                    data_dict[0][key] = {'data_type': param.data_type, 'value': str(safe_val).split(param.array_separator)}
                else:
                    data_dict[0][key] = {'data_type': param.data_type, 'value': []}
            elif param.data_type == 'DATE':
                if safe_val:
                    data_dict[0][key] = {'data_type': param.data_type, 'value': param.parse_date(safe_val)}
                else:
                    data_dict[0][key] = {'data_type': param.data_type, 'value': ''}
            else:
                data_dict[0][key] = {'data_type': param.data_type, 'value': safe_val if safe_val is not None else ''}
        # Recursively replace NaN/None values in the data_dict to avoid invalid JSON like NaN
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
        safe_data = sanitize(data_dict)
        return HttpResponse(json.dumps(safe_data))


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
        # Create a unique filename for the data pickle
        file_id = uuid.uuid4().hex
        file_path = os.path.join(settings.MEDIA_ROOT, 'dataframes', f'{file_id}.pkl')
        # Create Object and save
        obj = Object(name=request.POST['name'], data=os.path.join('dataframes', f'{file_id}.pkl'))
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
        df.columns = [param.id for param in params]
        df['id_to_connect'] = [f"{_}_{uuid.uuid4().hex}" for _ in range(df.shape[0])]
        os.makedirs(os.path.dirname(file_path), exist_ok=True)
        df.to_pickle(file_path)
        return HttpResponse(f'/database/get_object/{obj.id}')
    return render(request, 'database_manager/upload_csv.html')


@csrf_exempt
def delete_param(request, pk):
    """
    Delete a Parameter. Used when modifying an object's schema.
    """
    param = get_object_or_404(Parameter, pk=pk)
    param.delete()
    return HttpResponse(status=200)


def update_object(request, pk):
    """
    Edit the metadata of an existing Object including its name, identifier and
    parameter definitions. Writes the updated DataFrame back to disk.
    """
    obj = get_object_or_404(Object, pk=pk)
    # Use the FileField's path attribute to access the underlying file
    # instead of constructing the path manually. obj.data is a FieldFile,
    # and obj.data.path resolves to MEDIA_ROOT + obj.data.name.
    with open(obj.data.path, 'rb') as f:
        data_obj = pickle.load(f)
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
                data_obj[int(parameter.id)] = pd.NA
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
        data_obj.to_pickle(obj.data.path)
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
        # For linked parameters, return the list of (id_to_connect, identifier_label)
        try:
            with open(parameter.linked_object.data.path, 'rb') as f:
                child_df = pickle.load(f)
            ident_param = Parameter.objects.filter(object=parameter.linked_object, identificator=True).first()
            if ident_param:
                ident_list = [(row['id_to_connect'], str(row[int(ident_param.id)]).strip()) for _, row in child_df.iterrows() if str(row[int(ident_param.id)]).strip()]
                return ident_list
            else:
                return []
        except Exception:
            return []
    col_id = int(parameter.id)
    if col_id not in data_obj.columns:
        data_obj[col_id] = pd.NA
        return None
    column_series = data_obj[col_id].dropna()
    if parameter.data_type == 'ARRAY':
        raw_values_list = []
        for cell_value in column_series:
            cell_value_str = str(cell_value)
            raw_values_list.extend(cell_value_str.split(parameter.array_separator))
    else:
        raw_values_list = column_series.tolist()
    return get_unique_filtered_strings(raw_values_list)


def get_parameters_data_all(obj):
    """
    Load the object's DataFrame and build a list of (Parameter, values) tuples
    for all parameters defined on the object. Parameters with no values are
    skipped.
    """
    # Load the DataFrame using the FileField's path instead of building
    # the absolute path manually. This avoids mixing str and FieldFile types.
    with open(obj.data.path, 'rb') as f:
        data_obj = pickle.load(f)
    parameters_data = []
    for parameter in Parameter.objects.filter(object=obj):
        data = get_parameter_data(data_obj, parameter)
        if data is not None:
            parameters_data.append((parameter, data))
    return parameters_data


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
    # Load the object's DataFrame. If the file is missing or corrupt, an empty
    # DataFrame is created to allow adding the first row.
    try:
        with open(obj.data.path, 'rb') as f:
            data_obj = pickle.load(f)
    except Exception:
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
                    try:
                        with open(child_obj.data.path, 'rb') as f:
                            child_df = pickle.load(f)
                        ident_child_param = Parameter.objects.filter(object=child_obj, identificator=True).first()
                        if ident_child_param is not None:
                            for _, row in child_df.iterrows():
                                ident_list.append((row['id_to_connect'], row[int(ident_child_param.id)]))
                    except Exception:
                        ident_list = []
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
            if not col_values:
                new_row[col_id] = ''
            else:
                new_row[col_id] = (
                    str(col_values[0]) if len(col_values) == 1 else parameter.array_separator.join(col_values)
                )
        # Append to DataFrame and persist
        data_obj = pd.concat([data_obj, pd.DataFrame([new_row])], ignore_index=True)
        os.makedirs(os.path.dirname(obj.data.path), exist_ok=True)
        data_obj.to_pickle(obj.data.path)
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
        try:
            with open(child_obj.data.path, 'rb') as f:
                child_df = pickle.load(f)
            ident_param = Parameter.objects.filter(object=child_obj, identificator=True).first()
            if ident_param is not None:
                for _, row in child_df.iterrows():
                    ident_list.append((row['id_to_connect'], row[int(ident_param.id)]))
        except Exception:
            ident_list = []
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


def get_parameters_data_by_ident(obj: Object, param_ident_id) -> list:
    """
    Load a DataFrame and return, for each Parameter, the available values and
    which indices should be selected for the given row (`param_ident_id`).
    """
    # Read the pickled DataFrame using FileField.path. Using os.path.join
    # with obj.data (a FieldFile) causes a TypeError because join expects
    # a string or path-like object, not a FieldFile.
    with open(obj.data.path, 'rb') as f:
        data_obj = pickle.load(f)
    row = None if param_ident_id is None else data_obj[data_obj['id_to_connect'] == param_ident_id]
    parameters_data = []
    for parameter in Parameter.objects.filter(object=obj):
        current_data = ""
        if row is not None and not row.empty:
            current_data = str(row[int(parameter.id)].iloc[0]).strip()
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
            try:
                with open(parameter.linked_object.data.path, 'rb') as f:
                    child_df = pickle.load(f)
                ident_param = Parameter.objects.filter(object=parameter.linked_object, identificator=True).first()
                if ident_param:
                    data = [(row['id_to_connect'], str(row[int(ident_param.id)]).strip()) for _, row in child_df.iterrows() if str(row[int(ident_param.id)]).strip()]
                else:
                    data = []
            except Exception:
                data = []
            parameters_data.append((parameter, data, selected_ids, current_data))
        else:
            param_data = data_obj[int(parameter.id)].dropna().values.tolist()
            if parameter.data_type == 'ARRAY':
                arrays_data = parameter.array_separator.join(param_data)
                unique_param_data = set(filter(lambda x: str(x).strip(), arrays_data.split(parameter.array_separator)))
                arrays_data_list = [value for value in unique_param_data if value not in ['None', 'nan', None, '<NA>'] and value]
                arrays_data_indexed = [(i, data) for i, data in enumerate(arrays_data_list)]
                parameters_data.append((parameter, arrays_data_indexed, find_in_params_data(arrays_data_indexed, current_data, parameter), current_data))
            else:
                filtered_param_data = [value for value in param_data if value not in ['None', 'nan', None, '<NA>'] and value]
                param_data_indexed = [(i, data) for i, data in enumerate(filtered_param_data)]
                parameters_data.append((parameter, param_data_indexed, find_in_params_data(param_data_indexed, current_data, parameter), current_data))
    return parameters_data


def update_element_to_object(request, pk):
    """
    Edit a specific row within an object's DataFrame. When the request is GET,
    the form is rendered with current values; when POST, the submitted values
    are persisted. This view also populates data for any linked child objects.
    """
    obj = get_object_or_404(Object, pk=pk)
    with open(obj.data.path, 'rb') as f:
        data_obj = pickle.load(f)
    param_ident_id = request.GET.get('id')
    if request.method == 'POST':
        col_ids = list(map(int, request.POST.getlist('col_id[]')))
        for col_id in col_ids:
            col_values = request.POST.getlist(f'col_value_{col_id}[]')
            parameter = Parameter.objects.get(id=col_id)
            data_obj.loc[data_obj['id_to_connect'] == param_ident_id, int(col_id)] = (
                str(col_values[0]) if len(col_values) == 1 else parameter.array_separator.join(col_values)
            )
        data_obj.to_pickle(obj.data.path)
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
        try:
            with open(po.object.data.path, 'rb') as f:
                child_df = pickle.load(f)
            ident_param = Parameter.objects.filter(object=po.object, identificator=True).first()
            ident_list = []
            if ident_param is not None:
                for _, row in child_df.iterrows():
                    ident_list.append((row['id_to_connect'], row[int(ident_param.id)]))
            parameters_objects_idents.append((po.id, po.object, ident_list, selected_ids, po.link_type))
        except Exception:
            parameters_objects_idents.append((po.id, po.object, [], linked_param_ident_id))
    print(parameters_objects_idents)
    return render(request, 'database_manager/update_element_to_object.html', context={
        'object': obj,
        'parameters_group_data': parameters_group_data,
        'param_ident_id': param_ident_id,
        'parameters_objects': parameters_objects_data,
        'parameters_objects_params_group_data': parameters_objects_params_group_data,
        'parameters_objects_idents': parameters_objects_idents,
    })


def delete_element_to_object(request, pk):
    """
    Delete a specific row from an object's DataFrame. Expects the row identifier
    to be provided as the 'id' query parameter.
    """
    if request.method == 'POST':
        obj = get_object_or_404(Object, pk=pk)
        with open(obj.data.path, 'rb') as f:
            data_obj = pickle.load(f)
        param_ident_id = request.GET.get('id')
        index = data_obj[data_obj['id_to_connect'] == param_ident_id].index
        data_obj.drop(index, inplace=True)
        data_obj.to_pickle(obj.data.path)
        return HttpResponse()


def update_csv(request, pk):
    """
    Replace an object's DataFrame with data from a new CSV file. Maps CSV
    columns to parameters defined on the object. Any columns not mapped to a
    parameter are filled with NA. Generates new id_to_connect values for all
    rows.
    """
    obj = get_object_or_404(Object, pk=pk)
    if request.method == 'POST':
        with open(obj.data.path, 'rb') as f:
            data_obj = pickle.load(f)
        csv_file = request.FILES['csv_file']
        df = pd.read_csv(csv_file, converters={i: str for i in range(100)})
        drop_column = request.POST.get('drop_column', '-1')
        if drop_column != '-1':
            df.dropna(subset=[drop_column], inplace=True)
        new_df = {}
        for parameter in sorted(Parameter.objects.filter(object=obj), key=lambda x: x.id):
            col_id = int(parameter.id)
            csv_name = request.POST.get(f'csv_column_{col_id}', '')
            if not csv_name or csv_name == '-1':
                new_df[col_id] = pd.NA
                continue
            new_df[col_id] = df[csv_name].map(lambda x: str(x).strip()).tolist()
        new_df['id_to_connect'] = [f"{_}_{uuid.uuid4().hex}" for _ in range(df.shape[0])]
        new_df = pd.DataFrame(new_df)
        # Overwrite the existing pickle
        os.makedirs(os.path.dirname(obj.data.path), exist_ok=True)
        new_df.to_pickle(obj.data.path)
        return HttpResponse(f'/database/get_object/{obj.id}')
    return render(request, 'database_manager/upload_csv.html')


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
def generate_excel_file(request, pk):
    """
    Generate an Excel file representing the object's identifier column and any
    associated documents. Returns the file as a binary response.
    """
    obj = get_object_or_404(Object, pk=pk)
    ident_param = Parameter.objects.get(object=obj, identificator=True)
    documents = DocumentPattern_Objects.objects.filter(object=obj)
    doc_list = [f"{doc.document.name}**{doc.document.id}**" for doc in documents]
    df_object = pd.read_pickle(obj.data.path)
    ident_list = [f"{row[ident_param.id]}**{row['id_to_connect']}" for _, row in df_object.iterrows()]
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
        return HttpResponseBadRequest("Both parent_ident_id and child_ident_id must be provided.")
    # Update or create the row mapping
    ObjectLink_identificators.objects.update_or_create(
        object_link=object_link,
        parent_object_identificator=parent_ident_id,
        defaults={'object_identificator': child_ident_id},
    )
    return HttpResponse('')


@require_http_methods(['POST'])
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
