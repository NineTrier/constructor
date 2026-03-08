import io
import json
import re
import tempfile
import uuid
import zipfile
from pathlib import Path
from unittest.mock import patch

import pandas as pd
from docx import Document as PyDocxDocument
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse

from .models import (
    Object,
    ObjectLinkMeta,
    ObjectLink_identificators,
    ObjectRecord,
    Object_ParentObject,
    Parameter,
    ParameterValue,
    RecordLink,
)
from .views import _safe_load_dataframe, _write_dataframe
from .application.flag_guardrails import validate_dbm_flags
from document.models import DocType, DocumentPattern_Objects, DocumentsPattern
from user_manager.models import Profile


@override_settings(
    DBM_READ_FROM_SQL=False,
    DBM_SQL_SOURCE_OF_TRUTH=False,
    DBM_SQL_WRITE_FILE_SECONDARY=True,
    DBM_FILE_FALLBACK_READ=True,
    DBM_DUAL_WRITE=False,
)
class DatabaseManagerRecordApiTests(TestCase):
    def setUp(self):
        self._temp_media = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp_media.cleanup)
        self.media_override = override_settings(MEDIA_ROOT=self._temp_media.name)
        self.media_override.enable()
        self.addCleanup(self.media_override.disable)

        user_model = get_user_model()
        self.user = user_model.objects.create_superuser(
            username='admin_test',
            email='admin_test@example.com',
            password='pass1234',
        )
        self.client.force_login(self.user)

        self.obj = Object.objects.create(name='Cars', data='dataframes/cars.json')
        self.param_ident = Parameter.objects.create(
            object=self.obj,
            name='Code',
            data_type='TXT',
            identificator=True,
            order=1,
        )
        self.param_title = Parameter.objects.create(
            object=self.obj,
            name='Title',
            data_type='TXT',
            order=2,
        )
        self.param_tags = Parameter.objects.create(
            object=self.obj,
            name='Tags',
            data_type='ARRAY',
            array_separator=',',
            order=3,
        )

        self.child_obj = Object.objects.create(name='Owners', data='dataframes/owners.json')
        self.child_ident = Parameter.objects.create(
            object=self.child_obj,
            name='OwnerCode',
            data_type='TXT',
            identificator=True,
            order=1,
        )
        self.child_name = Parameter.objects.create(
            object=self.child_obj,
            name='OwnerName',
            data_type='TXT',
            order=2,
        )

        self.parent_uids = [uuid.uuid4().hex for _ in range(3)]
        cars_df = pd.DataFrame(
            [
                {
                    str(self.param_ident.id): 'CAR-1',
                    str(self.param_title.id): 'Sedan',
                    str(self.param_tags.id): 'family,comfort',
                    'id_to_connect': 'legacy-car-1',
                    'record_uid': self.parent_uids[0],
                },
                {
                    str(self.param_ident.id): 'CAR-2',
                    str(self.param_title.id): 'Hatchback',
                    str(self.param_tags.id): 'city,compact',
                    'id_to_connect': 'legacy-car-2',
                    'record_uid': self.parent_uids[1],
                },
                {
                    str(self.param_ident.id): 'CAR-3',
                    str(self.param_title.id): 'SUV',
                    str(self.param_tags.id): 'family,offroad',
                    'id_to_connect': 'legacy-car-3',
                    'record_uid': self.parent_uids[2],
                },
            ]
        )
        _write_dataframe(self.obj.data, cars_df, object_instance=self.obj)

        self.child_uid = uuid.uuid4().hex
        owners_df = pd.DataFrame(
            [
                {
                    str(self.child_ident.id): 'OWN-1',
                    str(self.child_name.id): 'Alice',
                    'id_to_connect': 'legacy-own-1',
                    'record_uid': self.child_uid,
                }
            ]
        )
        _write_dataframe(self.child_obj.data, owners_df, object_instance=self.child_obj)

        self.relation = Object_ParentObject.objects.create(parent_object=self.obj, object=self.child_obj, link_type='single')
        self.relation_meta = ObjectLinkMeta.objects.create(
            parent_object=self.obj,
            child_object=self.child_obj,
            object_link=self.relation,
            code='OWNER',
            display_name='Владелец',
            link_type='single',
            order=0,
        )
        ObjectLink_identificators.objects.create(
            object_link=self.relation,
            object_link_meta=self.relation_meta,
            parent_object_identificator=self.parent_uids[0],
            object_identificator=self.child_uid,
        )
        self.profile = Profile.for_user(self.user)
        self.doc_type = DocType.objects.create(name='Тестовый тип', author=self.profile)

    def _csv_file(self, text: str, name: str = "rows.csv"):
        return SimpleUploadedFile(name, text.encode("utf-8"), content_type="text/csv")

    def _create_docx_file(self, relative_path: str) -> Path:
        absolute = Path(self._temp_media.name) / Path(relative_path)
        absolute.parent.mkdir(parents=True, exist_ok=True)
        docx = PyDocxDocument()
        docx.add_paragraph("stub")
        docx.save(str(absolute))
        return absolute

    def _create_document_pattern(self, *, payload: dict, file_path: str = 'documents/user_1/test.docx') -> DocumentsPattern:
        self._create_docx_file(file_path)
        document = DocumentsPattern.objects.create(
            name='Тестовый документ',
            type=self.doc_type,
            owner=self.profile,
            file=file_path,
            json=payload,
        )
        DocumentPattern_Objects.objects.create(document=document, object=self.obj)
        return document

    @staticmethod
    def _build_minimal_document_json(*, token_text: str, context=None) -> dict:
        run_payload = {
            "id": "r_0",
            "class": "runs",
            "text": token_text,
            "data-invis": token_text,
            "data-name": token_text,
            "data-idRadioGroup": "radioGroup-1",
            "attrib": "r_0",
            "style": {},
            "hidden": False,
            "userCanSee": False,
        }
        paragraph_payload = {
            "id": "p_0",
            "class": "paragraph",
            "data-name": token_text,
            "data-idRadioGroup": "radioGroup-1",
            "childs": [json.dumps(run_payload, ensure_ascii=False)],
            "style": {},
            "hidden": False,
            "userCanSee": False,
        }
        return {
            "elements": [json.dumps(paragraph_payload, ensure_ascii=False)],
            "sectPr": "",
            "doc_name": "Тестовый документ",
            "radioGroups": {"childs": []},
            "fixedVariable": [],
            "images": {},
            "filters": {},
            "events": {},
            "cycles": {},
            "dbm_context": context or {},
        }

    def test_get_data_from_object_v1_contract_is_clean(self):
        response = self.client.post(
            reverse('get_data_from_object', args=[self.obj.id]) + '?api_version=v1',
            {'param_ident_id': self.parent_uids[0]},
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload.get('api_version'), 'v1')
        self.assertIn('record', payload)
        self.assertIn('schema', payload)
        self.assertNotIn('legacy_records', payload)
        self.assertNotIn('parameters', payload)
        self.assertEqual(payload['record']['record_uid'], self.parent_uids[0])
        self.assertEqual(payload['schema']['object_id'], self.obj.id)
        self.assertEqual(payload['schema']['parameters'][str(self.param_title.id)]['name'], 'Title')

    def test_v1_records_endpoint_structure_and_pagination(self):
        response = self.client.get(
            reverse('api_v1_object_records', args=[self.obj.id]),
            {
                'limit': 2,
                'offset': 0,
                'order': 'identificator',
                'include_schema': 1,
            },
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload.get('api_version'), 'v1')
        self.assertEqual(payload.get('object_id'), self.obj.id)
        self.assertIn('schema', payload)
        self.assertIn('records', payload)
        self.assertIn('page', payload)
        self.assertEqual(len(payload['records']), 2)
        self.assertIn('has_more', payload['page'])
        self.assertNotIn('total', payload['page'])
        self.assertIn('record_uid', payload['records'][0])
        self.assertIn('identificator', payload['records'][0])

    def test_v1_records_endpoint_include_total_and_case_insensitive_search(self):
        response = self.client.get(
            reverse('api_v1_object_records', args=[self.obj.id]),
            {
                'limit': 1,
                'offset': 0,
                'order': 'identificator',
                'include_total': 1,
                'include_schema': 0,
            },
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload['page']['total'], 3)
        self.assertTrue(payload['page']['has_more'])

        search_response = self.client.get(
            reverse('api_v1_object_records', args=[self.obj.id]),
            {
                'limit': 10,
                'offset': 0,
                'order': 'identificator',
                'include_total': 1,
                'include_schema': 0,
                'q': 'car-2',
            },
        )
        self.assertEqual(search_response.status_code, 200)
        search_payload = search_response.json()
        self.assertEqual(search_payload['page']['total'], 1)
        self.assertEqual(search_payload['records'][0]['identificator'], 'CAR-2')

    @override_settings(DBM_READ_FROM_SQL=True)
    def test_v1_records_endpoint_sql_and_fallback(self):
        sql_record = ObjectRecord.objects.create(
            object=self.obj,
            record_uid='sql-record-1',
            legacy_id_to_connect='sql-legacy-1',
        )
        ParameterValue.objects.create(
            record=sql_record,
            parameter=self.param_ident,
            value_text='SQL-CAR-1',
        )
        ParameterValue.objects.create(
            record=sql_record,
            parameter=self.param_title,
            value_text='SQL Sedan',
        )
        sql_response = self.client.get(
            reverse('api_v1_object_records', args=[self.obj.id]),
            {'limit': 10, 'offset': 0, 'order': 'record_uid', 'q': 'sql-car-1', 'include_total': 1},
        )
        self.assertEqual(sql_response.status_code, 200)
        sql_payload = sql_response.json()
        self.assertEqual(sql_payload['page']['total'], 1)
        self.assertEqual(sql_payload['records'][0]['record_uid'], 'sql-record-1')
        self.assertEqual(sql_payload['records'][0]['identificator'], 'SQL-CAR-1')

        # Search works only by identificator, not by other fields.
        sql_title_search = self.client.get(
            reverse('api_v1_object_records', args=[self.obj.id]),
            {'limit': 10, 'offset': 0, 'order': 'record_uid', 'q': 'sedan', 'include_total': 1},
        )
        self.assertEqual(sql_title_search.status_code, 200)
        self.assertEqual(sql_title_search.json()['page']['total'], 0)

        ObjectRecord.objects.filter(object=self.obj).delete()
        with self.assertLogs('database_manager.views', level='WARNING') as captured:
            fallback_response = self.client.get(
                reverse('api_v1_object_records', args=[self.obj.id]),
                {'limit': 10, 'offset': 0, 'order': 'identificator', 'include_total': 1},
            )
        self.assertEqual(fallback_response.status_code, 200)
        fallback_payload = fallback_response.json()
        self.assertGreaterEqual(fallback_payload['page']['total'], 1)
        self.assertTrue(any('sql_miss' in line for line in captured.output))
        fallback_title_search = self.client.get(
            reverse('api_v1_object_records', args=[self.obj.id]),
            {'limit': 10, 'offset': 0, 'order': 'identificator', 'q': 'sedan', 'include_total': 1},
        )
        self.assertEqual(fallback_title_search.status_code, 200)
        self.assertEqual(fallback_title_search.json()['page']['total'], 0)

    @override_settings(DBM_DUAL_WRITE=True, DBM_DUAL_WRITE_STRICT_FOR_TESTS=True)
    def test_update_csv_preserves_record_uid_and_links(self):
        before_df, _ = _safe_load_dataframe(self.obj.data, object_id=self.obj.pk, object_instance=self.obj, allow_empty=False)
        before_map = {str(row[str(self.param_ident.id)]): str(row['record_uid']) for _, row in before_df.iterrows()}

        update_payload = {
            'csv_file': self._csv_file("Code,Title\nCAR-1,Sedan Updated\nCAR-2,Hatchback Updated\nCAR-3,SUV Updated\n"),
            f'csv_column_{self.param_ident.id}': 'Code',
            f'csv_column_{self.param_title.id}': 'Title',
            f'csv_column_{self.param_tags.id}': '-1',
            'drop_column': '-1',
        }
        response = self.client.post(reverse('update_csv', args=[self.obj.id]), update_payload)
        self.assertEqual(response.status_code, 200)

        after_df, _ = _safe_load_dataframe(self.obj.data, object_id=self.obj.pk, object_instance=self.obj, allow_empty=False)
        after_map = {str(row[str(self.param_ident.id)]): str(row['record_uid']) for _, row in after_df.iterrows()}
        self.assertEqual(before_map, after_map)

        self.assertTrue(
            ObjectLink_identificators.objects.filter(
                object_link=self.relation,
                parent_object_identificator=self.parent_uids[0],
                object_identificator=self.child_uid,
            ).exists()
        )
        self.assertTrue(RecordLink.objects.filter(object_link=self.relation).exists())

    @override_settings(DBM_DUAL_WRITE=True, DBM_DUAL_WRITE_STRICT_FOR_TESTS=True)
    def test_update_csv_collision_logs_warning(self):
        duplicate_uids = [uuid.uuid4().hex, uuid.uuid4().hex]
        dup_df = pd.DataFrame(
            [
                {
                    str(self.param_ident.id): 'DUP-1',
                    str(self.param_title.id): 'A',
                    str(self.param_tags.id): '',
                    'id_to_connect': 'legacy-dup-a',
                    'record_uid': duplicate_uids[0],
                },
                {
                    str(self.param_ident.id): 'DUP-1',
                    str(self.param_title.id): 'B',
                    str(self.param_tags.id): '',
                    'id_to_connect': 'legacy-dup-b',
                    'record_uid': duplicate_uids[1],
                },
            ]
        )
        _write_dataframe(self.obj.data, dup_df, object_instance=self.obj)
        with self.assertLogs('database_manager.views', level='WARNING') as captured:
            response = self.client.post(
                reverse('update_csv', args=[self.obj.id]),
                {
                    'csv_file': self._csv_file("Code,Title\nDUP-1,Replaced\n"),
                    f'csv_column_{self.param_ident.id}': 'Code',
                    f'csv_column_{self.param_title.id}': 'Title',
                    f'csv_column_{self.param_tags.id}': '-1',
                    'drop_column': '-1',
                },
            )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(any('update_csv_match_stats' in line for line in captured.output))

        refreshed_df, _ = _safe_load_dataframe(self.obj.data, object_id=self.obj.pk, object_instance=self.obj, allow_empty=False)
        new_uid = str(refreshed_df.iloc[0]['record_uid'])
        self.assertNotIn(new_uid, set(duplicate_uids))

    @override_settings(DBM_READ_FROM_SQL=True)
    def test_sql_list_records_limit_and_fallback(self):
        for index in range(10):
            record = ObjectRecord.objects.create(
                object=self.obj,
                record_uid=f'sql-uid-{index}',
                legacy_id_to_connect=f'sql-legacy-{index}',
            )
            ParameterValue.objects.create(
                record=record,
                parameter=self.param_ident,
                value_text=f'SQL-CAR-{index}',
            )

        response = self.client.post(reverse('get_object', args=[self.obj.id]) + '?limit=5')
        self.assertEqual(response.status_code, 200)
        payload = json.loads(response.content.decode('utf-8'))
        self.assertEqual(len(payload['idents']), 5)

        ObjectRecord.objects.filter(object=self.obj).delete()
        with self.assertLogs('database_manager.views', level='WARNING') as captured:
            fallback_response = self.client.post(reverse('get_object', args=[self.obj.id]) + '?limit=5')
        self.assertEqual(fallback_response.status_code, 200)
        fallback_payload = json.loads(fallback_response.content.decode('utf-8'))
        self.assertGreaterEqual(len(fallback_payload['idents']), 1)
        self.assertTrue(any('sql_miss' in line for line in captured.output))

    @override_settings(DBM_DUAL_WRITE=True, DBM_DUAL_WRITE_STRICT_FOR_TESTS=True)
    def test_dual_write_add_update_delete(self):
        add_payload = {
            'col_id[]': [str(self.param_ident.id), str(self.param_title.id), str(self.param_tags.id)],
            f'col_value_{self.param_ident.id}[]': ['CAR-NEW'],
            f'col_value_{self.param_title.id}[]': ['Roadster'],
            f'col_value_{self.param_tags.id}[]': ['speed', 'sport'],
        }
        add_response = self.client.post(reverse('add_element', args=[self.obj.id]), add_payload)
        self.assertEqual(add_response.status_code, 302)

        df, _ = _safe_load_dataframe(self.obj.data, object_id=self.obj.pk, object_instance=self.obj, allow_empty=False)
        row = df[df[str(self.param_ident.id)] == 'CAR-NEW'].iloc[0]
        row_uid = str(row['record_uid'])

        sql_record = ObjectRecord.objects.get(object=self.obj, record_uid=row_uid)
        title_value = ParameterValue.objects.get(record=sql_record, parameter=self.param_title)
        tags_value = ParameterValue.objects.get(record=sql_record, parameter=self.param_tags)
        self.assertEqual(title_value.value_text, 'Roadster')
        self.assertEqual(tags_value.value_json, ['speed', 'sport'])

        update_response = self.client.post(
            reverse('update_element', args=[self.obj.id]) + f'?id={row_uid}',
            {
                'col_id[]': [str(self.param_ident.id), str(self.param_title.id), str(self.param_tags.id)],
                f'col_value_{self.param_ident.id}[]': ['CAR-NEW'],
                f'col_value_{self.param_title.id}[]': ['Roadster X'],
                f'col_value_{self.param_tags.id}[]': ['speed', 'sport', 'updated'],
            },
        )
        self.assertIn(update_response.status_code, (200, 302))

        title_value.refresh_from_db()
        tags_value.refresh_from_db()
        self.assertEqual(title_value.value_text, 'Roadster X')
        self.assertEqual(tags_value.value_json, ['speed', 'sport', 'updated'])

        delete_response = self.client.post(reverse('delete_element', args=[self.obj.id]) + f'?id={row_uid}')
        self.assertEqual(delete_response.status_code, 200)
        self.assertFalse(ObjectRecord.objects.filter(object=self.obj, record_uid=row_uid).exists())

    def test_update_element_child_link_payload_syncs_link_parameter(self):
        link_parameter = Parameter.objects.create(
            object=self.obj,
            name='Связь: Владелец',
            data_type='TXTS',
            linked_object=self.child_obj,
            link_meta=self.relation_meta,
            is_managed_link_param=True,
            order=10,
        )

        response = self.client.post(
            reverse('update_element', args=[self.obj.id]) + f'?id={self.parent_uids[1]}',
            {
                'col_id[]': [str(self.param_ident.id), str(self.param_title.id), str(link_parameter.id)],
                f'col_value_{self.param_ident.id}[]': ['CAR-2'],
                f'col_value_{self.param_title.id}[]': ['Hatchback'],
                f'col_value_{link_parameter.id}[]': [''],
                f'child_link_{self.relation_meta.id}': [self.child_uid],
            },
        )
        self.assertIn(response.status_code, (200, 302))
        self.assertTrue(
            ObjectLink_identificators.objects.filter(
                object_link=self.relation,
                object_link_meta=self.relation_meta,
                parent_object_identificator=self.parent_uids[1],
                object_identificator=self.child_uid,
            ).exists()
        )
        df, _ = _safe_load_dataframe(self.obj.data, object_id=self.obj.pk, object_instance=self.obj, allow_empty=False)
        row = df[df['record_uid'] == self.parent_uids[1]].iloc[0].to_dict()
        stored_value = str(row.get(str(link_parameter.id)) or row.get(link_parameter.id) or '').strip()
        self.assertEqual(stored_value, self.child_uid)

    def test_update_element_link_parameter_payload_without_child_link_creates_row_link(self):
        link_parameter = Parameter.objects.create(
            object=self.obj,
            name='Связь: Владелец',
            data_type='TXTS',
            linked_object=self.child_obj,
            link_meta=self.relation_meta,
            is_managed_link_param=True,
            order=10,
        )

        response = self.client.post(
            reverse('update_element', args=[self.obj.id]) + f'?id={self.parent_uids[2]}',
            {
                'col_id[]': [str(self.param_ident.id), str(self.param_title.id), str(link_parameter.id)],
                f'col_value_{self.param_ident.id}[]': ['CAR-3'],
                f'col_value_{self.param_title.id}[]': ['SUV'],
                f'col_value_{link_parameter.id}[]': [self.child_uid],
            },
        )
        self.assertIn(response.status_code, (200, 302))
        self.assertTrue(
            ObjectLink_identificators.objects.filter(
                object_link=self.relation,
                object_link_meta=self.relation_meta,
                parent_object_identificator=self.parent_uids[2],
                object_identificator=self.child_uid,
            ).exists()
        )

    def test_update_element_link_parameter_payload_without_child_link_restores_child_select_on_get(self):
        link_parameter = Parameter.objects.create(
            object=self.obj,
            name='Связь: Владелец',
            data_type='TXTS',
            linked_object=self.child_obj,
            link_meta=self.relation_meta,
            is_managed_link_param=True,
            order=10,
        )
        response = self.client.post(
            reverse('update_element', args=[self.obj.id]) + f'?id={self.parent_uids[2]}',
            {
                'col_id[]': [str(self.param_ident.id), str(self.param_title.id), str(link_parameter.id)],
                f'col_value_{self.param_ident.id}[]': ['CAR-3'],
                f'col_value_{self.param_title.id}[]': ['SUV'],
                f'col_value_{link_parameter.id}[]': [self.child_uid],
            },
        )
        self.assertIn(response.status_code, (200, 302))

        get_response = self.client.get(
            reverse('update_element', args=[self.obj.id]),
            {'id': self.parent_uids[2]},
        )
        self.assertEqual(get_response.status_code, 200)
        html = get_response.content.decode('utf-8')
        self.assertRegex(
            html,
            r'name="child_link_{meta}"[^>]*data-linked-param-id="{param}"[^>]*data-selected-values="{uid}"'.format(
                meta=self.relation_meta.id,
                param=link_parameter.id,
                uid=re.escape(self.child_uid),
            ),
        )
        self.assertRegex(
            html,
            r'name="col_value_{param}\[\]"[^>]*data-link-meta-id="{meta}"[^>]*data-selected-values="{uid}"'.format(
                param=link_parameter.id,
                meta=self.relation_meta.id,
                uid=re.escape(self.child_uid),
            ),
        )

    def test_update_element_get_prefills_child_param_field_for_selected_child(self):
        link_parameter = Parameter.objects.create(
            object=self.obj,
            name='Связь: Владелец',
            data_type='TXTS',
            linked_object=self.child_obj,
            link_meta=self.relation_meta,
            is_managed_link_param=True,
            order=10,
        )
        response = self.client.post(
            reverse('update_element', args=[self.obj.id]) + f'?id={self.parent_uids[1]}',
            {
                'col_id[]': [str(self.param_ident.id), str(self.param_title.id), str(link_parameter.id)],
                f'col_value_{self.param_ident.id}[]': ['CAR-2'],
                f'col_value_{self.param_title.id}[]': ['Hatchback'],
                f'col_value_{link_parameter.id}[]': [self.child_uid],
            },
        )
        self.assertIn(response.status_code, (200, 302))

        get_response = self.client.get(
            reverse('update_element', args=[self.obj.id]),
            {'id': self.parent_uids[1]},
        )
        self.assertEqual(get_response.status_code, 200)
        html = get_response.content.decode('utf-8')
        self.assertRegex(
            html,
            r'class="form-control child-param-field"[^>]*data-child-object-id="{child}"[^>]*data-link-meta-id="{meta}"[^>]*data-param-id="{param}"[^>]*value="Alice"'.format(
                child=self.child_obj.id,
                meta=self.relation_meta.id,
                param=self.child_name.id,
            ),
        )

    def test_update_element_without_file_uses_sql_fallback(self):
        record_uid = 'sql-only-missing-file-record'
        sql_record = ObjectRecord.objects.create(
            object=self.obj,
            record_uid=record_uid,
            legacy_id_to_connect=record_uid,
        )
        ParameterValue.objects.create(
            record=sql_record,
            parameter=self.param_title,
            value_text='before-update',
        )
        self.obj.data = 'dataframes/missing_file_source.json'
        self.obj.save(update_fields=['data'])

        response = self.client.post(
            reverse('update_element', args=[self.obj.id]) + f'?id={record_uid}',
            {
                'col_id[]': [str(self.param_title.id)],
                f'col_value_{self.param_title.id}[]': ['after-update'],
            },
        )
        self.assertIn(response.status_code, (200, 302))
        updated_value = ParameterValue.objects.get(record=sql_record, parameter=self.param_title)
        self.assertEqual(updated_value.value_text, 'after-update')

    @override_settings(DBM_READ_FROM_SQL=True)
    def test_update_element_get_restores_child_selected_from_sql_links(self):
        link_parameter = Parameter.objects.create(
            object=self.obj,
            name='Связь: Владелец',
            data_type='TXTS',
            linked_object=self.child_obj,
            link_meta=self.relation_meta,
            is_managed_link_param=True,
            order=10,
        )
        ObjectLink_identificators.objects.filter(
            object_link=self.relation,
            object_link_meta=self.relation_meta,
            parent_object_identificator=self.parent_uids[0],
        ).delete()
        parent_record = ObjectRecord.objects.create(
            object=self.obj,
            record_uid=self.parent_uids[0],
            legacy_id_to_connect=self.parent_uids[0],
        )
        child_record = ObjectRecord.objects.create(
            object=self.child_obj,
            record_uid=self.child_uid,
            legacy_id_to_connect=self.child_uid,
        )
        RecordLink.objects.create(
            object_link=self.relation,
            object_link_meta=self.relation_meta,
            parent_record=parent_record,
            child_record=child_record,
        )

        response = self.client.get(
            reverse('update_element', args=[self.obj.id]),
            {'id': self.parent_uids[0]},
        )
        self.assertEqual(response.status_code, 200)
        html = response.content.decode('utf-8')
        self.assertRegex(
            html,
            r'name="child_link_{meta}"[^>]*data-linked-param-id="{param}"[^>]*data-selected-values="{uid}"'.format(
                meta=self.relation_meta.id,
                param=link_parameter.id,
                uid=re.escape(self.child_uid),
            ),
        )
        self.assertRegex(
            html,
            r'name="col_value_{param}\[\]"[^>]*data-link-meta-id="{meta}"[^>]*data-selected-values="{uid}"'.format(
                param=link_parameter.id,
                meta=self.relation_meta.id,
                uid=re.escape(self.child_uid),
            ),
        )

    def test_backfill_idempotent(self):
        call_command('backfill_records_to_sql', '--links', stdout=io.StringIO())
        records_after_first = ObjectRecord.objects.count()
        values_after_first = ParameterValue.objects.count()
        links_after_first = RecordLink.objects.count()

        call_command('backfill_records_to_sql', '--links', stdout=io.StringIO())
        self.assertEqual(ObjectRecord.objects.count(), records_after_first)
        self.assertEqual(ParameterValue.objects.count(), values_after_first)
        self.assertEqual(RecordLink.objects.count(), links_after_first)

    def test_backfill_deep_detects_link_diff(self):
        call_command('backfill_records_to_sql', '--links', stdout=io.StringIO())
        RecordLink.objects.filter(object_link=self.relation).delete()
        with self.assertLogs('database_manager.management.commands.backfill_records_to_sql', level='WARNING') as captured:
            call_command('backfill_records_to_sql', '--deep', '--dry-run', '--no-links', stdout=io.StringIO())
        self.assertTrue(any('backfill_deep_diff' in line for line in captured.output))

    def test_drift_report_json_detects_diffs(self):
        call_command('backfill_records_to_sql', '--links', stdout=io.StringIO())
        clean_output = io.StringIO()
        call_command('dbm_drift_report', '--json', '--sample', '50', '--links', stdout=clean_output)
        clean_payload = json.loads(clean_output.getvalue().strip().splitlines()[-1])
        self.assertIn('summary', clean_payload)
        self.assertIn('objects', clean_payload)
        self.assertEqual(clean_payload['summary']['objects_with_diff'], 0)

        sql_record = ObjectRecord.objects.get(object=self.obj, record_uid=self.parent_uids[0])
        title_value = ParameterValue.objects.get(record=sql_record, parameter=self.param_title)
        title_value.value_text = 'Tampered value'
        title_value.save(update_fields=['value_text'])
        RecordLink.objects.filter(object_link=self.relation).delete()

        diff_output = io.StringIO()
        call_command('dbm_drift_report', '--json', '--sample', '50', '--links', stdout=diff_output)
        diff_payload = json.loads(diff_output.getvalue().strip().splitlines()[-1])
        self.assertGreaterEqual(diff_payload['summary']['objects_with_diff'], 1)
        object_report = next(item for item in diff_payload['objects'] if item['object_id'] == self.obj.id)
        self.assertGreaterEqual(
            object_report['sample_record_diff_count'] + object_report['link_diff_count'],
            1,
        )

        explain_output = io.StringIO()
        call_command(
            'dbm_drift_report',
            '--json',
            '--sample',
            '50',
            '--links',
            '--explain',
            '--top',
            '5',
            stdout=explain_output,
        )
        explain_payload = json.loads(explain_output.getvalue().strip().splitlines()[-1])
        self.assertIn('diff_reasons', explain_payload['summary'])
        explain_object = next(item for item in explain_payload['objects'] if item['object_id'] == self.obj.id)
        self.assertIn('top_parameters', explain_object)
        self.assertIn('examples', explain_object)

    @override_settings(DBM_SQL_SOURCE_OF_TRUTH=True, DBM_DUAL_WRITE=False, DBM_SQL_WRITE_FILE_SECONDARY=True)
    def test_sql_source_of_truth_keeps_sql_on_file_secondary_failure(self):
        payload = {
            'col_id[]': [str(self.param_ident.id), str(self.param_title.id), str(self.param_tags.id)],
            f'col_value_{self.param_ident.id}[]': ['CAR-SQL-FIRST'],
            f'col_value_{self.param_title.id}[]': ['SQL First'],
            f'col_value_{self.param_tags.id}[]': ['one', 'two'],
        }
        with patch('database_manager.views._write_dataframe', side_effect=OSError('disk write failed')):
            response = self.client.post(reverse('add_element', args=[self.obj.id]), payload)
        self.assertEqual(response.status_code, 302)
        record = ObjectRecord.objects.filter(object=self.obj).first()
        self.assertIsNotNone(record)
        ident_value = ParameterValue.objects.get(record=record, parameter=self.param_ident)
        self.assertEqual(ident_value.value_text, 'CAR-SQL-FIRST')

    @override_settings(DBM_SQL_SOURCE_OF_TRUTH=False, DBM_DUAL_WRITE=True)
    def test_file_primary_mode_still_fails_on_file_write_error(self):
        payload = {
            'col_id[]': [str(self.param_ident.id)],
            f'col_value_{self.param_ident.id}[]': ['CAR-FILE-FIRST'],
        }
        with patch('database_manager.views._write_dataframe', side_effect=OSError('disk write failed')):
            with self.assertRaises(OSError):
                self.client.post(reverse('add_element', args=[self.obj.id]), payload)

    def test_normalize_legacy_links_in_file_idempotent(self):
        ObjectLink_identificators.objects.create(
            object_link=self.relation,
            parent_object_identificator='legacy-car-2',
            object_identificator='legacy-own-1',
        )
        orphan = ObjectLink_identificators.objects.create(
            object_link=self.relation,
            parent_object_identificator='legacy-missing-parent',
            object_identificator='legacy-own-1',
        )
        call_command('normalize_legacy_links_in_file', stdout=io.StringIO())
        self.assertFalse(ObjectLink_identificators.objects.filter(pk=orphan.pk).exists())
        self.assertTrue(
            ObjectLink_identificators.objects.filter(
                object_link=self.relation,
                parent_object_identificator=self.parent_uids[1],
                object_identificator=self.child_uid,
            ).exists()
        )
        count_after_first = ObjectLink_identificators.objects.count()
        call_command('normalize_legacy_links_in_file', stdout=io.StringIO())
        self.assertEqual(ObjectLink_identificators.objects.count(), count_after_first)

    def test_cleanup_sql_orphans_idempotent(self):
        orphan_record = ObjectRecord.objects.create(
            object=self.obj,
            record_uid='sql-only-orphan',
            legacy_id_to_connect='legacy-sql-only-orphan',
        )
        ParameterValue.objects.create(
            record=orphan_record,
            parameter=self.param_ident,
            value_text='ORPHAN-CODE',
        )

        dry_run_output = io.StringIO()
        call_command(
            'dbm_cleanup_sql_orphans',
            '--object-id',
            str(self.obj.id),
            stdout=dry_run_output,
        )
        dry_payload = json.loads(dry_run_output.getvalue().strip().splitlines()[-1])
        self.assertGreaterEqual(dry_payload['candidates'], 1)
        self.assertEqual(dry_payload['deleted'], 0)
        self.assertTrue(ObjectRecord.objects.filter(object=self.obj, record_uid='sql-only-orphan').exists())

        apply_output = io.StringIO()
        call_command(
            'dbm_cleanup_sql_orphans',
            '--object-id',
            str(self.obj.id),
            '--apply',
            stdout=apply_output,
        )
        apply_payload = json.loads(apply_output.getvalue().strip().splitlines()[-1])
        self.assertGreaterEqual(apply_payload['deleted'], 1)
        self.assertFalse(ObjectRecord.objects.filter(object=self.obj, record_uid='sql-only-orphan').exists())

        second_output = io.StringIO()
        call_command(
            'dbm_cleanup_sql_orphans',
            '--object-id',
            str(self.obj.id),
            '--apply',
            stdout=second_output,
        )
        second_payload = json.loads(second_output.getvalue().strip().splitlines()[-1])
        self.assertEqual(second_payload['deleted'], 0)

    @override_settings(DBM_DUAL_WRITE=True, DBM_DUAL_WRITE_STRICT_FOR_TESTS=True)
    def test_v1_record_crud_endpoints(self):
        create_response = self.client.post(
            reverse('api_v1_object_records', args=[self.obj.id]),
            data=json.dumps(
                {
                    "record": {
                        "fields": {
                            str(self.param_ident.id): {"type": "TXT", "value": "CAR-V1-1"},
                            str(self.param_title.id): {"type": "TXT", "value": "Coupe"},
                            str(self.param_tags.id): {"type": "ARRAY", "value": ["fast", "new"]},
                        }
                    }
                }
            ),
            content_type='application/json',
            HTTP_X_API_VERSION='v1',
        )
        self.assertEqual(create_response.status_code, 201)
        created_payload = create_response.json()
        self.assertEqual(created_payload.get('api_version'), 'v1')
        record_uid = created_payload['record']['record_uid']

        get_response = self.client.get(
            reverse('api_v1_object_record_detail', args=[self.obj.id, record_uid]),
            HTTP_X_API_VERSION='v1',
        )
        self.assertEqual(get_response.status_code, 200)
        get_payload = get_response.json()
        self.assertEqual(get_payload.get('api_version'), 'v1')
        self.assertEqual(get_payload['record']['record_uid'], record_uid)

        update_response = self.client.patch(
            reverse('api_v1_object_record_detail', args=[self.obj.id, record_uid]),
            data=json.dumps(
                {
                    "record": {
                        "fields": {
                            str(self.param_title.id): {"type": "TXT", "value": "Coupe Updated"},
                            str(self.param_tags.id): {"type": "ARRAY", "value": ["fast", "updated"]},
                        }
                    }
                }
            ),
            content_type='application/json',
            HTTP_X_API_VERSION='v1',
        )
        self.assertEqual(update_response.status_code, 200)
        update_payload = update_response.json()
        self.assertEqual(update_payload['record']['fields'][str(self.param_title.id)]['value'], 'Coupe Updated')

        delete_response = self.client.delete(
            reverse('api_v1_object_record_detail', args=[self.obj.id, record_uid]),
            data=json.dumps({}),
            content_type='application/json',
            HTTP_X_API_VERSION='v1',
        )
        self.assertEqual(delete_response.status_code, 200)
        delete_payload = delete_response.json()
        self.assertTrue(delete_payload.get('deleted'))

        not_found_response = self.client.get(
            reverse('api_v1_object_record_detail', args=[self.obj.id, record_uid]),
            HTTP_X_API_VERSION='v1',
        )
        self.assertEqual(not_found_response.status_code, 404)
        self.assertEqual(not_found_response.json()['error']['code'], 'NOT_FOUND')

    @override_settings(DBM_DUAL_WRITE=True, DBM_DUAL_WRITE_STRICT_FOR_TESTS=True)
    def test_v1_links_create_get_delete(self):
        parent_uid = self.parent_uids[1]
        create_link_response = self.client.post(
            reverse('api_v1_record_links', args=[self.obj.id, parent_uid]),
            data=json.dumps(
                {
                    "link_meta_id": self.relation_meta.id,
                    "child_record_uid": self.child_uid,
                }
            ),
            content_type='application/json',
            HTTP_X_API_VERSION='v1',
        )
        self.assertEqual(create_link_response.status_code, 201)
        create_payload = create_link_response.json()
        self.assertEqual(create_payload['link']['link_meta_id'], self.relation_meta.id)

        get_links_response = self.client.get(
            reverse('api_v1_record_links', args=[self.obj.id, parent_uid]),
            HTTP_X_API_VERSION='v1',
        )
        self.assertEqual(get_links_response.status_code, 200)
        get_payload = get_links_response.json()
        link_entry = next((item for item in get_payload['links'] if item['link_meta_id'] == self.relation_meta.id), None)
        self.assertIsNotNone(link_entry)
        self.assertIn(self.child_uid, link_entry['child_record_uids'])

        delete_link_response = self.client.delete(
            reverse('api_v1_record_links', args=[self.obj.id, parent_uid]),
            data=json.dumps(
                {
                    "link_meta_id": self.relation_meta.id,
                    "child_record_uid": self.child_uid,
                }
            ),
            content_type='application/json',
            HTTP_X_API_VERSION='v1',
        )
        self.assertEqual(delete_link_response.status_code, 200)
        self.assertGreaterEqual(delete_link_response.json()['deleted'], 1)

    @override_settings(DBM_DUAL_WRITE=True, DBM_DUAL_WRITE_STRICT_FOR_TESTS=True)
    def test_v1_links_rejects_meta_from_other_parent(self):
        other_parent = Object.objects.create(name='Other Parent', data='dataframes/other_parent.json')
        other_parent_ident = Parameter.objects.create(
            object=other_parent,
            name='OtherCode',
            data_type='TXT',
            identificator=True,
            order=1,
        )
        _write_dataframe(
            other_parent.data,
            pd.DataFrame(
                [
                    {
                        str(other_parent_ident.id): 'OTHER-1',
                        'id_to_connect': 'legacy-other-1',
                        'record_uid': uuid.uuid4().hex,
                    }
                ]
            ),
            object_instance=other_parent,
        )
        foreign_relation = Object_ParentObject.objects.create(
            parent_object=other_parent,
            object=self.child_obj,
            link_type='single',
        )
        foreign_meta = ObjectLinkMeta.objects.create(
            parent_object=other_parent,
            child_object=self.child_obj,
            object_link=foreign_relation,
            code='FOREIGN_CHILD',
            display_name='Чужая связь',
            link_type='single',
            order=0,
        )

        response = self.client.post(
            reverse('api_v1_record_links', args=[self.obj.id, self.parent_uids[0]]),
            data=json.dumps(
                {
                    "link_meta_id": foreign_meta.id,
                    "child_record_uid": self.child_uid,
                }
            ),
            content_type='application/json',
            HTTP_X_API_VERSION='v1',
        )
        self.assertEqual(response.status_code, 400)
        payload = response.json()
        self.assertEqual(payload['error']['code'], 'VALIDATION_ERROR')
        self.assertIn('родительскому', payload['error']['message'].lower())

    def test_v1_links_meta_api_crud(self):
        create_response = self.client.post(
            reverse('api_v1_object_links_meta', args=[self.obj.id]),
            data=json.dumps(
                {
                    "child_object_id": self.child_obj.id,
                    "code": "RESPONDENT",
                    "display_name": "Ответчик",
                    "link_type": "single",
                    "order": 10,
                }
            ),
            content_type='application/json',
            HTTP_X_API_VERSION='v1',
        )
        self.assertEqual(create_response.status_code, 201)
        created_payload = create_response.json()
        self.assertEqual(created_payload['link_meta']['display_name'], 'Ответчик')
        self.assertIn('link_parameter', created_payload['link_meta'])
        self.assertIsNotNone(created_payload['link_meta']['link_parameter'])
        self.assertEqual(created_payload['link_meta']['link_parameter']['name'], 'Связь: Ответчик')
        meta_id = created_payload['link_meta']['id']
        created_param_id = created_payload['link_meta']['link_parameter']['id']

        list_response = self.client.get(
            reverse('api_v1_object_links_meta', args=[self.obj.id]),
            HTTP_X_API_VERSION='v1',
        )
        self.assertEqual(list_response.status_code, 200)
        list_payload = list_response.json()
        self.assertTrue(any(item['id'] == meta_id for item in list_payload['links_meta']))

        patch_response = self.client.patch(
            reverse('api_v1_object_links_meta_detail', args=[self.obj.id, meta_id]),
            data=json.dumps({"display_name": "Представитель", "order": 12}),
            content_type='application/json',
            HTTP_X_API_VERSION='v1',
        )
        self.assertEqual(patch_response.status_code, 200)
        self.assertEqual(patch_response.json()['link_meta']['display_name'], 'Представитель')
        patched_param = Parameter.objects.get(id=created_param_id)
        self.assertEqual(patched_param.name, 'Связь: Представитель')
        self.assertTrue(patched_param.is_managed_link_param)

        delete_response = self.client.delete(
            reverse('api_v1_object_links_meta_detail', args=[self.obj.id, meta_id]),
            data=json.dumps({}),
            content_type='application/json',
            HTTP_X_API_VERSION='v1',
        )
        self.assertEqual(delete_response.status_code, 200)
        self.assertEqual(delete_response.json()['deleted'], 1)
        patched_param.refresh_from_db()
        self.assertIsNone(patched_param.link_meta_id)
        self.assertFalse(patched_param.is_managed_link_param)

    def test_link_meta_create_reuses_unique_legacy_parameter(self):
        legacy_param = Parameter.objects.create(
            object=self.obj,
            name=f'Связь с {self.child_obj.name}',
            data_type='TXTS',
            linked_object=self.child_obj,
            order=100,
        )
        response = self.client.post(
            reverse('api_v1_object_links_meta', args=[self.obj.id]),
            data=json.dumps(
                {
                    "child_object_id": self.child_obj.id,
                    "code": "LEGACY_BRIDGE",
                    "display_name": "Роль legacy",
                    "link_type": "single",
                    "order": 9,
                }
            ),
            content_type='application/json',
            HTTP_X_API_VERSION='v1',
        )
        self.assertEqual(response.status_code, 201)
        payload = response.json()
        meta_id = payload['link_meta']['id']
        legacy_param.refresh_from_db()
        self.assertEqual(legacy_param.link_meta_id, meta_id)
        self.assertTrue(legacy_param.is_managed_link_param)
        self.assertEqual(legacy_param.name, 'Связь: Роль legacy')

    @override_settings(DBM_DISABLE_LEGACY_LINKED_PARAMS=True)
    def test_link_meta_create_does_not_reuse_legacy_parameter_when_disabled(self):
        legacy_param = Parameter.objects.create(
            object=self.obj,
            name=f'Связь с {self.child_obj.name}',
            data_type='TXTS',
            linked_object=self.child_obj,
            order=101,
        )
        response = self.client.post(
            reverse('api_v1_object_links_meta', args=[self.obj.id]),
            data=json.dumps(
                {
                    "child_object_id": self.child_obj.id,
                    "code": "STRICT_LINK",
                    "display_name": "Строгая роль",
                    "link_type": "single",
                    "order": 12,
                }
            ),
            content_type='application/json',
            HTTP_X_API_VERSION='v1',
        )
        self.assertEqual(response.status_code, 201)
        payload = response.json()
        created_meta = payload['link_meta']
        self.assertIn('link_parameter', created_meta)
        self.assertIsNotNone(created_meta['link_parameter'])
        self.assertNotEqual(created_meta['link_parameter']['id'], legacy_param.id)

        legacy_param.refresh_from_db()
        self.assertIsNone(legacy_param.link_meta_id)
        self.assertFalse(legacy_param.is_managed_link_param)

    def test_link_meta_delete_cleans_links(self):
        create_response = self.client.post(
            reverse('api_v1_object_links_meta', args=[self.obj.id]),
            data=json.dumps(
                {
                    "child_object_id": self.child_obj.id,
                    "code": "FOR_DELETE",
                    "display_name": "Для удаления",
                    "link_type": "single",
                    "order": 11,
                }
            ),
            content_type='application/json',
            HTTP_X_API_VERSION='v1',
        )
        self.assertEqual(create_response.status_code, 201)
        meta_payload = create_response.json()['link_meta']
        meta_id = meta_payload['id']

        parent_record = ObjectRecord.objects.create(
            object=self.obj,
            record_uid='delete-parent',
            legacy_id_to_connect='delete-parent',
        )
        child_record = ObjectRecord.objects.create(
            object=self.child_obj,
            record_uid='delete-child',
            legacy_id_to_connect='delete-child',
        )
        created_meta = ObjectLinkMeta.objects.get(id=meta_id)
        ObjectLink_identificators.objects.create(
            object_link=created_meta.object_link,
            object_link_meta=created_meta,
            parent_object_identificator='delete-parent',
            object_identificator='delete-child',
        )
        RecordLink.objects.create(
            object_link=created_meta.object_link,
            object_link_meta=created_meta,
            parent_record=parent_record,
            child_record=child_record,
        )

        delete_response = self.client.delete(
            reverse('api_v1_object_links_meta_detail', args=[self.obj.id, meta_id]),
            data=json.dumps({}),
            content_type='application/json',
            HTTP_X_API_VERSION='v1',
        )
        self.assertEqual(delete_response.status_code, 200)
        delete_payload = delete_response.json()
        self.assertEqual(delete_payload['deleted'], 1)
        self.assertGreaterEqual(delete_payload['usage_count'], 1)
        self.assertFalse(ObjectLink_identificators.objects.filter(object_link_meta_id=meta_id).exists())
        self.assertFalse(RecordLink.objects.filter(object_link_meta_id=meta_id).exists())

    def test_v1_links_meta_cycle_guard(self):
        response = self.client.post(
            reverse('api_v1_object_links_meta', args=[self.child_obj.id]),
            data=json.dumps(
                {
                    "child_object_id": self.obj.id,
                    "code": "BACKREF",
                    "display_name": "Обратная связь",
                    "link_type": "single",
                }
            ),
            content_type='application/json',
            HTTP_X_API_VERSION='v1',
        )
        self.assertEqual(response.status_code, 400)
        payload = response.json()
        self.assertEqual(payload['error']['code'], 'VALIDATION_ERROR')
        self.assertIn('cycle', payload['error']['message'].lower())

    def test_v1_links_meta_allows_two_roles_for_same_child(self):
        first = self.client.post(
            reverse('api_v1_object_links_meta', args=[self.obj.id]),
            data=json.dumps(
                {
                    "child_object_id": self.child_obj.id,
                    "code": "APPLICANT",
                    "display_name": "Заявитель",
                    "link_type": "single",
                    "order": 5,
                }
            ),
            content_type='application/json',
            HTTP_X_API_VERSION='v1',
        )
        second = self.client.post(
            reverse('api_v1_object_links_meta', args=[self.obj.id]),
            data=json.dumps(
                {
                    "child_object_id": self.child_obj.id,
                    "code": "RESPONDENT",
                    "display_name": "Ответчик",
                    "link_type": "single",
                    "order": 6,
                }
            ),
            content_type='application/json',
            HTTP_X_API_VERSION='v1',
        )
        self.assertEqual(first.status_code, 201)
        self.assertEqual(second.status_code, 201)
        listed = self.client.get(reverse('api_v1_object_links_meta', args=[self.obj.id]), HTTP_X_API_VERSION='v1')
        self.assertEqual(listed.status_code, 200)
        payload = listed.json()
        role_names = {item['display_name'] for item in payload.get('links_meta', [])}
        self.assertTrue({'Заявитель', 'Ответчик'}.issubset(role_names))

    def test_view_document_contains_doc_token_index(self):
        profile = Profile.for_user(self.user)
        doc_type = DocType.objects.create(name='Тестовый тип', author=profile)
        document = DocumentsPattern.objects.create(
            name='Тестовый документ',
            type=doc_type,
            owner=profile,
            file='documents/user_1/test.docx',
            json={},
        )
        DocumentPattern_Objects.objects.create(document=document, object=self.obj)

        response = self.client.get(f'/document/view?id={document.id}')
        self.assertEqual(response.status_code, 200)
        index_payload = json.loads(response.context['doc_token_index_json'])
        self.assertEqual(index_payload['objects_by_name'][self.obj.name], self.obj.id)
        object_key = str(self.obj.id)
        self.assertEqual(
            index_payload['params_by_object_and_name'][object_key][self.param_ident.name],
            self.param_ident.id,
        )
        self.assertEqual(
            index_payload['links_meta_by_parent_and_display'][object_key][self.relation_meta.display_name][0],
            self.relation_meta.id,
        )
        relation_param = Parameter.objects.create(
            object=self.obj,
            name='Связь: Владелец',
            data_type='TXTS',
            linked_object=self.child_obj,
            link_meta=self.relation_meta,
            is_managed_link_param=True,
            order=200,
        )
        response = self.client.get(f'/document/view?id={document.id}')
        index_payload = json.loads(response.context['doc_token_index_json'])
        self.assertEqual(
            index_payload['link_param_by_meta_id'][str(self.relation_meta.id)],
            relation_param.id,
        )

    @override_settings(DBM_READ_FROM_SQL=True)
    def test_resolve_tokens_api_depth_two(self):
        grandchild_obj = Object.objects.create(name='Contracts', data='dataframes/contracts.json')
        grandchild_param = Parameter.objects.create(
            object=grandchild_obj,
            name='ContractName',
            data_type='TXT',
            order=1,
        )
        relation_child_grand = Object_ParentObject.objects.create(
            parent_object=self.child_obj,
            object=grandchild_obj,
            link_type='single',
        )
        relation_child_grand_meta = ObjectLinkMeta.objects.create(
            parent_object=self.child_obj,
            child_object=grandchild_obj,
            object_link=relation_child_grand,
            code='CONTRACT',
            display_name='Договор',
            link_type='single',
            order=0,
        )

        root_uid = 'root-sql-depth2'
        child_uid = 'child-sql-depth2'
        grand_uid = 'grand-sql-depth2'
        root_record = ObjectRecord.objects.create(object=self.obj, record_uid=root_uid, legacy_id_to_connect=root_uid)
        child_record = ObjectRecord.objects.create(object=self.child_obj, record_uid=child_uid, legacy_id_to_connect=child_uid)
        grand_record = ObjectRecord.objects.create(object=grandchild_obj, record_uid=grand_uid, legacy_id_to_connect=grand_uid)
        ParameterValue.objects.create(record=root_record, parameter=self.param_ident, value_text='CAR-ROOT')
        ParameterValue.objects.create(record=child_record, parameter=self.child_name, value_text='OWNER-ROOT')
        ParameterValue.objects.create(record=grand_record, parameter=grandchild_param, value_text='Contract-777')
        RecordLink.objects.create(
            object_link=self.relation,
            object_link_meta=self.relation_meta,
            parent_record=root_record,
            child_record=child_record,
        )
        RecordLink.objects.create(
            object_link=relation_child_grand,
            object_link_meta=relation_child_grand_meta,
            parent_record=child_record,
            child_record=grand_record,
        )

        document = self._create_document_pattern(
            payload=self._build_minimal_document_json(
                token_text='Plain text',
                context={str(self.obj.id): root_uid},
            )
        )
        response = self.client.post(
            reverse('api_v1_resolve_tokens'),
            data=json.dumps(
                {
                    "document_id": document.id,
                    "context": {str(self.obj.id): root_uid},
                    "tokens": [
                        "{:obj("
                        + str(self.obj.id)
                        + ").link("
                        + str(self.relation_meta.id)
                        + ").link("
                        + str(relation_child_grand_meta.id)
                        + ").param("
                        + str(grandchild_param.id)
                        + "):}"
                    ],
                    "options": {"maxDepth": 8, "aggregationMode": "first", "joiner": ", "},
                }
            ),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload['summary']['errors'], 0)
        self.assertEqual(payload['results'][0]['status'], 'ok')
        self.assertEqual(payload['results'][0]['value'], 'Contract-777')

    @override_settings(DBM_READ_FROM_SQL=True)
    def test_resolve_tokens_api_multiple_selector_all(self):
        self.relation.link_type = 'multiple'
        self.relation.save(update_fields=['link_type'])
        self.relation_meta.link_type = 'multiple'
        self.relation_meta.save(update_fields=['link_type'])

        root_uid = 'root-sql-multi'
        child_uid_1 = 'child-sql-multi-1'
        child_uid_2 = 'child-sql-multi-2'
        root_record = ObjectRecord.objects.create(object=self.obj, record_uid=root_uid, legacy_id_to_connect=root_uid)
        child_record_1 = ObjectRecord.objects.create(object=self.child_obj, record_uid=child_uid_1, legacy_id_to_connect=child_uid_1)
        child_record_2 = ObjectRecord.objects.create(object=self.child_obj, record_uid=child_uid_2, legacy_id_to_connect=child_uid_2)
        ParameterValue.objects.create(record=root_record, parameter=self.param_ident, value_text='CAR-MULTI')
        ParameterValue.objects.create(record=child_record_1, parameter=self.child_name, value_text='Alice Multi')
        ParameterValue.objects.create(record=child_record_2, parameter=self.child_name, value_text='Bob Multi')
        RecordLink.objects.create(
            object_link=self.relation,
            object_link_meta=self.relation_meta,
            parent_record=root_record,
            child_record=child_record_1,
        )
        RecordLink.objects.create(
            object_link=self.relation,
            object_link_meta=self.relation_meta,
            parent_record=root_record,
            child_record=child_record_2,
        )

        document = self._create_document_pattern(
            payload=self._build_minimal_document_json(
                token_text='Plain text',
                context={str(self.obj.id): root_uid},
            )
        )
        token = "{:obj(" + str(self.obj.id) + ").link(" + str(self.relation_meta.id) + ")[*].param(" + str(self.child_name.id) + "):}"
        response = self.client.post(
            reverse('api_v1_resolve_tokens'),
            data=json.dumps(
                {
                    "document_id": document.id,
                    "context": {str(self.obj.id): root_uid},
                    "tokens": [token],
                    "options": {"maxDepth": 8, "aggregationMode": "first", "joiner": ", "},
                }
            ),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload['results'][0]['status'], 'ok')
        self.assertIn('Alice Multi', payload['results'][0]['value'])
        self.assertIn('Bob Multi', payload['results'][0]['value'])

    @override_settings(DBM_READ_FROM_SQL=True)
    def test_resolve_tokens_api_human_depth_two(self):
        grandchild_obj = Object.objects.create(name='ContractsHuman', data='dataframes/contracts_human.json')
        grandchild_param = Parameter.objects.create(
            object=grandchild_obj,
            name='ContractHumanName',
            data_type='TXT',
            order=1,
        )
        relation_child_grand = Object_ParentObject.objects.create(
            parent_object=self.child_obj,
            object=grandchild_obj,
            link_type='single',
        )
        relation_child_grand_meta = ObjectLinkMeta.objects.create(
            parent_object=self.child_obj,
            child_object=grandchild_obj,
            object_link=relation_child_grand,
            code='CONTRACT_HUMAN',
            display_name='ДоговорЧеловек',
            link_type='single',
            order=0,
        )

        root_uid = 'root-human-depth2'
        child_uid = 'child-human-depth2'
        grand_uid = 'grand-human-depth2'
        root_record = ObjectRecord.objects.create(object=self.obj, record_uid=root_uid, legacy_id_to_connect=root_uid)
        child_record = ObjectRecord.objects.create(object=self.child_obj, record_uid=child_uid, legacy_id_to_connect=child_uid)
        grand_record = ObjectRecord.objects.create(object=grandchild_obj, record_uid=grand_uid, legacy_id_to_connect=grand_uid)
        ParameterValue.objects.create(record=root_record, parameter=self.param_ident, value_text='CAR-HUMAN')
        ParameterValue.objects.create(record=child_record, parameter=self.child_name, value_text='OWNER-HUMAN')
        ParameterValue.objects.create(record=grand_record, parameter=grandchild_param, value_text='Contract-Human-777')
        RecordLink.objects.create(
            object_link=self.relation,
            object_link_meta=self.relation_meta,
            parent_record=root_record,
            child_record=child_record,
        )
        RecordLink.objects.create(
            object_link=relation_child_grand,
            object_link_meta=relation_child_grand_meta,
            parent_record=child_record,
            child_record=grand_record,
        )

        document = self._create_document_pattern(
            payload=self._build_minimal_document_json(
                token_text='Plain text',
                context={str(self.obj.id): root_uid},
            )
        )
        human_token = "{: " + self.obj.name + "." + self.relation_meta.display_name + "." + relation_child_grand_meta.display_name + "." + grandchild_param.name + " :}"
        response = self.client.post(
            reverse('api_v1_resolve_tokens'),
            data=json.dumps(
                {
                    "document_id": document.id,
                    "context": {str(self.obj.id): root_uid},
                    "tokens": [human_token],
                    "options": {"maxDepth": 8, "aggregationMode": "first", "joiner": ", "},
                }
            ),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload['summary']['errors'], 0)
        self.assertEqual(payload['summary']['unresolved'], 0)
        self.assertEqual(payload['results'][0]['status'], 'ok')
        self.assertEqual(payload['results'][0]['value'], 'Contract-Human-777')
        self.assertIn('canonical_token', payload['results'][0])
        self.assertTrue(payload['results'][0]['canonical_token'].startswith('{:obj('))

    @override_settings(DBM_READ_FROM_SQL=True)
    def test_resolve_tokens_api_human_nbsp_and_case_insensitive_role(self):
        root_uid = 'root-human-nbsp-case'
        child_uid = 'child-human-nbsp-case'
        root_record = ObjectRecord.objects.create(object=self.obj, record_uid=root_uid, legacy_id_to_connect=root_uid)
        child_record = ObjectRecord.objects.create(object=self.child_obj, record_uid=child_uid, legacy_id_to_connect=child_uid)
        ParameterValue.objects.create(record=root_record, parameter=self.param_ident, value_text='CAR-NBSP')
        ParameterValue.objects.create(record=child_record, parameter=self.child_name, value_text='OWNER-NBSP')
        RecordLink.objects.create(
            object_link=self.relation,
            object_link_meta=self.relation_meta,
            parent_record=root_record,
            child_record=child_record,
        )
        document = self._create_document_pattern(
            payload=self._build_minimal_document_json(
                token_text='Plain text',
                context={str(self.obj.id): root_uid},
            )
        )
        human_token = "{:\u00a0" + self.obj.name + ". ВЛАДЕЛЕЦ ." + self.child_name.name + " \u00a0:}"
        response = self.client.post(
            reverse('api_v1_resolve_tokens'),
            data=json.dumps(
                {
                    "document_id": document.id,
                    "context": {str(self.obj.id): root_uid},
                    "tokens": [human_token],
                }
            ),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload['results'][0]['status'], 'ok')
        self.assertEqual(payload['results'][0]['value'], 'OWNER-NBSP')
        self.assertTrue(payload['results'][0]['canonical_token'].startswith('{:obj('))

    @override_settings(DBM_READ_FROM_SQL=True)
    def test_resolve_tokens_api_human_plural_object_name(self):
        root_uid = 'root-human-plural'
        child_uid = 'child-human-plural'
        root_record = ObjectRecord.objects.create(object=self.obj, record_uid=root_uid, legacy_id_to_connect=root_uid)
        child_record = ObjectRecord.objects.create(object=self.child_obj, record_uid=child_uid, legacy_id_to_connect=child_uid)
        ParameterValue.objects.create(record=root_record, parameter=self.param_ident, value_text='CAR-PLURAL')
        ParameterValue.objects.create(record=child_record, parameter=self.child_name, value_text='OWNER-PLURAL')
        RecordLink.objects.create(
            object_link=self.relation,
            object_link_meta=self.relation_meta,
            parent_record=root_record,
            child_record=child_record,
        )
        document = self._create_document_pattern(
            payload=self._build_minimal_document_json(
                token_text='Plain text',
                context={str(self.obj.id): root_uid},
            )
        )
        human_token = "{: Car." + self.relation_meta.display_name + "." + self.child_name.name + " :}"
        response = self.client.post(
            reverse('api_v1_resolve_tokens'),
            data=json.dumps(
                {
                    "document_id": document.id,
                    "context": {str(self.obj.id): root_uid},
                    "tokens": [human_token],
                }
            ),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload['results'][0]['status'], 'ok')
        self.assertEqual(payload['results'][0]['value'], 'OWNER-PLURAL')

    @override_settings(DBM_READ_FROM_SQL=True)
    def test_resolve_tokens_api_human_role_case_form(self):
        root_uid = 'root-human-case-form'
        child_uid = 'child-human-case-form'
        root_record = ObjectRecord.objects.create(object=self.obj, record_uid=root_uid, legacy_id_to_connect=root_uid)
        child_record = ObjectRecord.objects.create(object=self.child_obj, record_uid=child_uid, legacy_id_to_connect=child_uid)
        ParameterValue.objects.create(record=root_record, parameter=self.param_ident, value_text='CAR-CASE')
        ParameterValue.objects.create(record=child_record, parameter=self.child_name, value_text='OWNER-CASE')
        RecordLink.objects.create(
            object_link=self.relation,
            object_link_meta=self.relation_meta,
            parent_record=root_record,
            child_record=child_record,
        )
        document = self._create_document_pattern(
            payload=self._build_minimal_document_json(
                token_text='Plain text',
                context={str(self.obj.id): root_uid},
            )
        )
        human_token = "{: " + self.obj.name + ".Владельцем." + self.child_name.name + " :}"
        response = self.client.post(
            reverse('api_v1_resolve_tokens'),
            data=json.dumps(
                {
                    "document_id": document.id,
                    "context": {str(self.obj.id): root_uid},
                    "tokens": [human_token],
                }
            ),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload['results'][0]['status'], 'ok')
        self.assertEqual(payload['results'][0]['value'], 'OWNER-CASE')

    @override_settings(DBM_READ_FROM_SQL=True)
    def test_resolve_tokens_api_human_role_legacy_link_phrase(self):
        link_parameter = Parameter.objects.create(
            object=self.obj,
            name='Связь с Владелец',
            data_type='TXTS',
            linked_object=self.child_obj,
            link_meta=self.relation_meta,
            is_managed_link_param=True,
            order=90,
        )
        self.assertIsNotNone(link_parameter.id)
        root_uid = 'root-human-legacy-link-name'
        child_uid = 'child-human-legacy-link-name'
        root_record = ObjectRecord.objects.create(object=self.obj, record_uid=root_uid, legacy_id_to_connect=root_uid)
        child_record = ObjectRecord.objects.create(object=self.child_obj, record_uid=child_uid, legacy_id_to_connect=child_uid)
        ParameterValue.objects.create(record=root_record, parameter=self.param_ident, value_text='CAR-LEGACY-LINK-NAME')
        ParameterValue.objects.create(record=child_record, parameter=self.child_name, value_text='OWNER-LEGACY-LINK-NAME')
        RecordLink.objects.create(
            object_link=self.relation,
            object_link_meta=self.relation_meta,
            parent_record=root_record,
            child_record=child_record,
        )
        document = self._create_document_pattern(
            payload=self._build_minimal_document_json(
                token_text='Plain text',
                context={str(self.obj.id): root_uid},
            )
        )
        human_token = "{: " + self.obj.name + ".Связь с Владелец." + self.child_name.name + " :}"
        response = self.client.post(
            reverse('api_v1_resolve_tokens'),
            data=json.dumps(
                {
                    "document_id": document.id,
                    "context": {str(self.obj.id): root_uid},
                    "tokens": [human_token],
                }
            ),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload['results'][0]['status'], 'ok')
        self.assertEqual(payload['results'][0]['value'], 'OWNER-LEGACY-LINK-NAME')

    @override_settings(DBM_READ_FROM_SQL=True)
    def test_resolve_tokens_api_human_depth_three(self):
        grandchild_obj = Object.objects.create(name='ContractsDepth3', data='dataframes/contracts_depth3.json')
        great_obj = Object.objects.create(name='JudgesDepth3', data='dataframes/judges_depth3.json')
        grandchild_param = Parameter.objects.create(
            object=grandchild_obj,
            name='ContractDepth3Name',
            data_type='TXT',
            order=1,
        )
        great_param = Parameter.objects.create(
            object=great_obj,
            name='JudgeDepth3Name',
            data_type='TXT',
            order=1,
        )

        relation_child_grand = Object_ParentObject.objects.create(
            parent_object=self.child_obj,
            object=grandchild_obj,
            link_type='single',
        )
        relation_child_grand_meta = ObjectLinkMeta.objects.create(
            parent_object=self.child_obj,
            child_object=grandchild_obj,
            object_link=relation_child_grand,
            code='CONTRACT_DEPTH3',
            display_name='ДоговорDepth3',
            link_type='single',
            order=0,
        )
        relation_grand_great = Object_ParentObject.objects.create(
            parent_object=grandchild_obj,
            object=great_obj,
            link_type='single',
        )
        relation_grand_great_meta = ObjectLinkMeta.objects.create(
            parent_object=grandchild_obj,
            child_object=great_obj,
            object_link=relation_grand_great,
            code='JUDGE_DEPTH3',
            display_name='СудьяDepth3',
            link_type='single',
            order=0,
        )

        root_uid = 'root-human-depth3'
        child_uid = 'child-human-depth3'
        grand_uid = 'grand-human-depth3'
        great_uid = 'great-human-depth3'
        root_record = ObjectRecord.objects.create(object=self.obj, record_uid=root_uid, legacy_id_to_connect=root_uid)
        child_record = ObjectRecord.objects.create(object=self.child_obj, record_uid=child_uid, legacy_id_to_connect=child_uid)
        grand_record = ObjectRecord.objects.create(object=grandchild_obj, record_uid=grand_uid, legacy_id_to_connect=grand_uid)
        great_record = ObjectRecord.objects.create(object=great_obj, record_uid=great_uid, legacy_id_to_connect=great_uid)
        ParameterValue.objects.create(record=root_record, parameter=self.param_ident, value_text='CAR-HUMAN-DEPTH3')
        ParameterValue.objects.create(record=child_record, parameter=self.child_name, value_text='OWNER-HUMAN-DEPTH3')
        ParameterValue.objects.create(record=grand_record, parameter=grandchild_param, value_text='Contract-Human-Depth3')
        ParameterValue.objects.create(record=great_record, parameter=great_param, value_text='Judge-Human-Depth3')
        RecordLink.objects.create(
            object_link=self.relation,
            object_link_meta=self.relation_meta,
            parent_record=root_record,
            child_record=child_record,
        )
        RecordLink.objects.create(
            object_link=relation_child_grand,
            object_link_meta=relation_child_grand_meta,
            parent_record=child_record,
            child_record=grand_record,
        )
        RecordLink.objects.create(
            object_link=relation_grand_great,
            object_link_meta=relation_grand_great_meta,
            parent_record=grand_record,
            child_record=great_record,
        )

        document = self._create_document_pattern(
            payload=self._build_minimal_document_json(
                token_text='Plain text',
                context={str(self.obj.id): root_uid},
            )
        )
        human_token = (
            "{: "
            + self.obj.name
            + "."
            + self.relation_meta.display_name
            + "."
            + relation_child_grand_meta.display_name
            + "."
            + relation_grand_great_meta.display_name
            + "."
            + great_param.name
            + " :}"
        )
        response = self.client.post(
            reverse('api_v1_resolve_tokens'),
            data=json.dumps(
                {
                    "document_id": document.id,
                    "context": {str(self.obj.id): root_uid},
                    "tokens": [human_token],
                    "options": {"maxDepth": 8, "aggregationMode": "first", "joiner": ", "},
                }
            ),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload['summary']['errors'], 0)
        self.assertEqual(payload['summary']['unresolved'], 0)
        self.assertEqual(payload['results'][0]['status'], 'ok')
        self.assertEqual(payload['results'][0]['value'], 'Judge-Human-Depth3')
        self.assertIn('.link(', payload['results'][0]['canonical_token'])

    @override_settings(DBM_READ_FROM_SQL=True)
    def test_resolve_tokens_api_human_missing_param_is_unresolved(self):
        root_uid = 'root-human-missing-param'
        root_record = ObjectRecord.objects.create(object=self.obj, record_uid=root_uid, legacy_id_to_connect=root_uid)
        ParameterValue.objects.create(record=root_record, parameter=self.param_ident, value_text='CAR-HUMAN-MISSING')

        document = self._create_document_pattern(
            payload=self._build_minimal_document_json(
                token_text='Plain text',
                context={str(self.obj.id): root_uid},
            )
        )
        human_token = "{: " + self.obj.name + "." + self.relation_meta.display_name + ".НесуществующийПараметр :}"
        response = self.client.post(
            reverse('api_v1_resolve_tokens'),
            data=json.dumps(
                {
                    "document_id": document.id,
                    "context": {str(self.obj.id): root_uid},
                    "tokens": [human_token],
                }
            ),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload['results'][0]['status'], 'unresolved')
        self.assertEqual(payload['results'][0]['error']['code'], 'PARAM_NOT_FOUND')
        self.assertEqual(payload['summary']['unresolved'], 1)

    @override_settings(DBM_READ_FROM_SQL=True, DOC_TOKEN_HUMAN_STRICT=True)
    def test_resolve_tokens_api_human_strict_disables_fuzzy_role_fallback(self):
        root_uid = 'root-human-strict-role'
        child_uid = 'child-human-strict-role'
        root_record = ObjectRecord.objects.create(object=self.obj, record_uid=root_uid, legacy_id_to_connect=root_uid)
        child_record = ObjectRecord.objects.create(object=self.child_obj, record_uid=child_uid, legacy_id_to_connect=child_uid)
        ParameterValue.objects.create(record=root_record, parameter=self.param_ident, value_text='CAR-STRICT')
        ParameterValue.objects.create(record=child_record, parameter=self.child_name, value_text='OWNER-STRICT')
        RecordLink.objects.create(
            object_link=self.relation,
            object_link_meta=self.relation_meta,
            parent_record=root_record,
            child_record=child_record,
        )

        document = self._create_document_pattern(
            payload=self._build_minimal_document_json(
                token_text='Plain text',
                context={str(self.obj.id): root_uid},
            )
        )
        human_token = "{: " + self.obj.name + ".ВЛАДЕЛЕЦ." + self.child_name.name + " :}"
        response = self.client.post(
            reverse('api_v1_resolve_tokens'),
            data=json.dumps(
                {
                    "document_id": document.id,
                    "context": {str(self.obj.id): root_uid},
                    "tokens": [human_token],
                }
            ),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload['results'][0]['status'], 'unresolved')
        self.assertEqual(payload['results'][0]['error']['code'], 'LINK_ROLE_NOT_FOUND')
        self.assertEqual(payload['summary']['unresolved'], 1)

    @override_settings(DBM_READ_FROM_SQL=True)
    def test_resolve_tokens_api_human_ambiguous_role_name(self):
        ObjectLinkMeta.objects.create(
            parent_object=self.obj,
            child_object=self.child_obj,
            object_link=self.relation,
            code='OWNER_ALT_LOWER',
            display_name='владелец',
            link_type='single',
            order=10,
        )

        root_uid = 'root-human-ambiguous'
        root_record = ObjectRecord.objects.create(object=self.obj, record_uid=root_uid, legacy_id_to_connect=root_uid)
        ParameterValue.objects.create(record=root_record, parameter=self.param_ident, value_text='CAR-AMBIGUOUS')

        document = self._create_document_pattern(
            payload=self._build_minimal_document_json(
                token_text='Plain text',
                context={str(self.obj.id): root_uid},
            )
        )
        # Deliberately not exact-case to trigger case-insensitive ambiguity path.
        human_token = "{: " + self.obj.name + ".ВЛАДЕЛЕЦ." + self.child_name.name + " :}"
        response = self.client.post(
            reverse('api_v1_resolve_tokens'),
            data=json.dumps(
                {
                    "document_id": document.id,
                    "context": {str(self.obj.id): root_uid},
                    "tokens": [human_token],
                }
            ),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload['results'][0]['status'], 'unresolved')
        self.assertEqual(payload['results'][0]['error']['code'], 'LINK_ROLE_AMBIGUOUS')
        self.assertEqual(payload['summary']['unresolved'], 1)

    @override_settings(DBM_READ_FROM_SQL=True)
    def test_resolve_tokens_api_cycle_detected(self):
        reverse_relation = Object_ParentObject.objects.create(
            parent_object=self.child_obj,
            object=self.obj,
            link_type='single',
        )
        reverse_meta = ObjectLinkMeta.objects.create(
            parent_object=self.child_obj,
            child_object=self.obj,
            object_link=reverse_relation,
            code='BACK',
            display_name='Назад',
            link_type='single',
            order=0,
        )

        root_uid = 'root-sql-cycle'
        child_uid = 'child-sql-cycle'
        root_record = ObjectRecord.objects.create(object=self.obj, record_uid=root_uid, legacy_id_to_connect=root_uid)
        child_record = ObjectRecord.objects.create(object=self.child_obj, record_uid=child_uid, legacy_id_to_connect=child_uid)
        ParameterValue.objects.create(record=root_record, parameter=self.param_ident, value_text='CAR-CYCLE')
        RecordLink.objects.create(
            object_link=self.relation,
            object_link_meta=self.relation_meta,
            parent_record=root_record,
            child_record=child_record,
        )
        RecordLink.objects.create(
            object_link=reverse_relation,
            object_link_meta=reverse_meta,
            parent_record=child_record,
            child_record=root_record,
        )

        document = self._create_document_pattern(
            payload=self._build_minimal_document_json(
                token_text='Plain text',
                context={str(self.obj.id): root_uid},
            )
        )
        token = "{:obj(" + str(self.obj.id) + ").link(" + str(self.relation_meta.id) + ").link(" + str(reverse_meta.id) + ").param(" + str(self.param_ident.id) + "):}"
        response = self.client.post(
            reverse('api_v1_resolve_tokens'),
            data=json.dumps(
                {
                    "document_id": document.id,
                    "context": {str(self.obj.id): root_uid},
                    "tokens": [token],
                    "options": {"maxDepth": 8, "aggregationMode": "first", "joiner": ", "},
                }
            ),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload['results'][0]['status'], 'error')
        self.assertEqual(payload['results'][0]['error']['code'], 'CYCLE_DETECTED')

    @override_settings(DBM_READ_FROM_SQL=True)
    def test_prefetch_graph_endpoint_returns_records_and_links(self):
        root_uid = 'root-prefetch-graph'
        child_uid = 'child-prefetch-graph'
        root_record = ObjectRecord.objects.create(object=self.obj, record_uid=root_uid, legacy_id_to_connect=root_uid)
        child_record = ObjectRecord.objects.create(object=self.child_obj, record_uid=child_uid, legacy_id_to_connect=child_uid)
        ParameterValue.objects.create(record=root_record, parameter=self.param_ident, value_text='CAR-PREFETCH')
        ParameterValue.objects.create(record=child_record, parameter=self.child_name, value_text='OWNER-PREFETCH')
        RecordLink.objects.create(
            object_link=self.relation,
            object_link_meta=self.relation_meta,
            parent_record=root_record,
            child_record=child_record,
        )

        document = self._create_document_pattern(
            payload=self._build_minimal_document_json(
                token_text='Plain text',
                context={str(self.obj.id): root_uid},
            )
        )
        token = "{:obj(" + str(self.obj.id) + ").link(" + str(self.relation_meta.id) + ").param(" + str(self.child_name.id) + "):}"
        response = self.client.post(
            reverse('api_v1_prefetch_graph'),
            data=json.dumps(
                {
                    "document_id": document.id,
                    "context": {str(self.obj.id): root_uid},
                    "tokens": [token],
                    "options": {"maxDepth": 8},
                }
            ),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertIn('graph', payload)
        self.assertGreaterEqual(len(payload['graph']['records']), 2)
        link_entries = payload['graph']['links']
        self.assertTrue(any(item['link_meta_id'] == self.relation_meta.id for item in link_entries))

    def test_resolve_tokens_api_missing_root(self):
        document = self._create_document_pattern(
            payload=self._build_minimal_document_json(
                token_text='Plain text',
                context={},
            )
        )
        token = "{:obj(" + str(self.obj.id) + ").param(" + str(self.param_ident.id) + "):}"
        response = self.client.post(
            reverse('api_v1_resolve_tokens'),
            data=json.dumps(
                {
                    "document_id": document.id,
                    "context": {},
                    "tokens": [token],
                    "options": {"maxDepth": 8, "aggregationMode": "first", "joiner": ", "},
                }
            ),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload['results'][0]['status'], 'error')
        self.assertEqual(payload['results'][0]['error']['code'], 'MISSING_ROOT')

    def test_resolve_tokens_api_permission_denied(self):
        document = self._create_document_pattern(
            payload=self._build_minimal_document_json(token_text='Plain text')
        )
        user_model = get_user_model()
        other_user = user_model.objects.create_user(
            username='readonly_user',
            email='readonly_user@example.com',
            password='pass1234',
        )
        self.client.force_login(other_user)
        token = "{:obj(" + str(self.obj.id) + ").param(" + str(self.param_ident.id) + "):}"
        response = self.client.post(
            reverse('api_v1_resolve_tokens'),
            data=json.dumps(
                {
                    "document_id": document.id,
                    "context": {str(self.obj.id): self.parent_uids[0]},
                    "tokens": [token],
                }
            ),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()['error']['code'], 'PERMISSION_DENIED')
        self.client.force_login(self.user)

    @override_settings(DBM_READ_FROM_SQL=True)
    def test_docx_export_resolves_tokens_without_artifacts_and_keeps_json(self):
        root_uid = 'root-export-sql'
        child_uid = 'child-export-sql'
        root_record = ObjectRecord.objects.create(object=self.obj, record_uid=root_uid, legacy_id_to_connect=root_uid)
        child_record = ObjectRecord.objects.create(object=self.child_obj, record_uid=child_uid, legacy_id_to_connect=child_uid)
        ParameterValue.objects.create(record=root_record, parameter=self.param_ident, value_text='CAR-EXPORT')
        ParameterValue.objects.create(record=child_record, parameter=self.child_name, value_text='Alice Export')
        RecordLink.objects.create(
            object_link=self.relation,
            object_link_meta=self.relation_meta,
            parent_record=root_record,
            child_record=child_record,
        )

        token = "{:obj(" + str(self.obj.id) + ").link(" + str(self.relation_meta.id) + ").param(" + str(self.child_name.id) + "):}"
        payload = self._build_minimal_document_json(
            token_text=token,
            context={str(self.obj.id): root_uid},
        )
        document = self._create_document_pattern(payload=payload, file_path='documents/user_1/export_test.docx')

        response = self.client.get(f'/document/download?id={document.id}')
        self.assertEqual(response.status_code, 200)
        archive = zipfile.ZipFile(io.BytesIO(response.content))
        xml = archive.read('word/document.xml').decode('utf-8')
        self.assertIn('Alice Export', xml)
        self.assertNotIn('{:', xml)
        self.assertNotIn('data-token', xml)
        self.assertNotIn('data-invis', xml)
        self.assertNotIn('obj(', xml)
        self.assertNotIn('link(', xml)

        document.refresh_from_db()
        paragraph = json.loads(document.json['elements'][0])
        run = json.loads(paragraph['childs'][0])
        self.assertEqual(run['text'], token)

    @override_settings(DBM_READ_FROM_SQL=True)
    def test_docx_export_resolves_human_tokens_without_artifacts(self):
        root_uid = 'root-export-human'
        child_uid = 'child-export-human'
        root_record = ObjectRecord.objects.create(object=self.obj, record_uid=root_uid, legacy_id_to_connect=root_uid)
        child_record = ObjectRecord.objects.create(object=self.child_obj, record_uid=child_uid, legacy_id_to_connect=child_uid)
        ParameterValue.objects.create(record=root_record, parameter=self.param_ident, value_text='CAR-EXPORT-HUMAN')
        ParameterValue.objects.create(record=child_record, parameter=self.child_name, value_text='Alice Export Human')
        RecordLink.objects.create(
            object_link=self.relation,
            object_link_meta=self.relation_meta,
            parent_record=root_record,
            child_record=child_record,
        )

        token = "{: " + self.obj.name + "." + self.relation_meta.display_name + "." + self.child_name.name + " :}"
        payload = self._build_minimal_document_json(
            token_text=token,
            context={str(self.obj.id): root_uid},
        )
        document = self._create_document_pattern(payload=payload, file_path='documents/user_1/export_human_test.docx')

        response = self.client.get(f'/document/download?id={document.id}')
        self.assertEqual(response.status_code, 200)
        archive = zipfile.ZipFile(io.BytesIO(response.content))
        xml = archive.read('word/document.xml').decode('utf-8')
        self.assertIn('Alice Export Human', xml)
        self.assertNotIn('{:', xml)
        self.assertNotIn('obj(', xml)
        self.assertNotIn('link(', xml)

        document.refresh_from_db()
        paragraph = json.loads(document.json['elements'][0])
        run = json.loads(paragraph['childs'][0])
        self.assertEqual(run['text'], token)

    @override_settings(DBM_READ_FROM_SQL=True)
    def test_docx_export_keeps_partial_result_when_some_tokens_unresolved(self):
        root_uid = 'root-export-partial'
        child_uid = 'child-export-partial'
        root_record = ObjectRecord.objects.create(object=self.obj, record_uid=root_uid, legacy_id_to_connect=root_uid)
        child_record = ObjectRecord.objects.create(object=self.child_obj, record_uid=child_uid, legacy_id_to_connect=child_uid)
        ParameterValue.objects.create(record=root_record, parameter=self.param_ident, value_text='CAR-PARTIAL')
        ParameterValue.objects.create(record=child_record, parameter=self.child_name, value_text='Alice Partial')
        RecordLink.objects.create(
            object_link=self.relation,
            object_link_meta=self.relation_meta,
            parent_record=root_record,
            child_record=child_record,
        )
        good_token = "{: " + self.obj.name + "." + self.relation_meta.display_name + "." + self.child_name.name + " :}"
        bad_token = "{:\u00a0Дата принятия заявления  :}"
        payload = self._build_minimal_document_json(
            token_text=good_token + " | " + bad_token,
            context={str(self.obj.id): root_uid},
        )
        document = self._create_document_pattern(payload=payload, file_path='documents/user_1/export_fail.docx')

        response = self.client.get(f'/document/download?id={document.id}')
        self.assertEqual(response.status_code, 200)
        archive = zipfile.ZipFile(io.BytesIO(response.content))
        xml = archive.read('word/document.xml').decode('utf-8')
        self.assertIn('Alice Partial', xml)
        self.assertNotIn('{:', xml)

    def test_v1_error_format_for_validation_and_not_found(self):
        invalid_create_response = self.client.post(
            reverse('api_v1_object_records', args=[self.obj.id]),
            data=json.dumps({"record": {"fields": {"999999": {"type": "TXT", "value": "x"}}}}),
            content_type='application/json',
            HTTP_X_API_VERSION='v1',
        )
        self.assertEqual(invalid_create_response.status_code, 400)
        invalid_payload = invalid_create_response.json()
        self.assertIn('error', invalid_payload)
        self.assertEqual(invalid_payload['error']['code'], 'VALIDATION_ERROR')
        self.assertIn('message', invalid_payload['error'])
        self.assertIn('details', invalid_payload['error'])

        not_found_response = self.client.get(
            reverse('api_v1_object_record_detail', args=[self.obj.id, 'missing-record-uid']),
            HTTP_X_API_VERSION='v1',
        )
        self.assertEqual(not_found_response.status_code, 404)
        not_found_payload = not_found_response.json()
        self.assertEqual(not_found_payload['error']['code'], 'NOT_FOUND')
        self.assertIn('message', not_found_payload['error'])

    def test_v1_objects_list_endpoint(self):
        response = self.client.get(reverse('api_v1_objects_list'), HTTP_X_API_VERSION='v1')
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload.get('api_version'), 'v1')
        self.assertTrue(any(item['id'] == self.obj.id for item in payload.get('objects', [])))

    @override_settings(DBM_SQL_SOURCE_OF_TRUTH=True, DBM_READ_FROM_SQL=False, DBM_SQL_WRITE_FILE_SECONDARY=False)
    def test_cutover_check_fails_on_invalid_flag_combo(self):
        call_command('normalize_legacy_links_in_file', stdout=io.StringIO())
        call_command('backfill_records_to_sql', '--links', '--object-id', str(self.obj.id), stdout=io.StringIO())
        output = io.StringIO()
        with self.assertRaises(SystemExit) as exited:
            call_command('dbm_cutover_check', '--json', '--object-id', str(self.obj.id), stdout=output)
        self.assertEqual(exited.exception.code, 2)
        payload = json.loads(output.getvalue().strip().splitlines()[-1])
        self.assertFalse(payload['ok'])
        self.assertEqual(payload['exit_code'], 2)
        flags_check = next(item for item in payload['checks'] if item['check'] == 'flags')
        self.assertFalse(flags_check['ok'])

    @override_settings(DBM_SQL_WRITE_FILE_SECONDARY=False)
    def test_cutover_check_passes_for_clean_object_scope(self):
        call_command('normalize_legacy_links_in_file', stdout=io.StringIO())
        call_command('backfill_records_to_sql', '--links', '--object-id', str(self.obj.id), stdout=io.StringIO())

        output = io.StringIO()
        call_command('dbm_cutover_check', '--json', '--object-id', str(self.obj.id), stdout=output)
        payload = json.loads(output.getvalue().strip().splitlines()[-1])
        self.assertTrue(payload['ok'])
        self.assertEqual(payload['exit_code'], 0)

    @override_settings(
        DBM_READ_FROM_SQL=False,
        DBM_SQL_SOURCE_OF_TRUTH=False,
        DBM_SQL_WRITE_FILE_SECONDARY=True,
        DBM_DUAL_WRITE=False,
        DBM_FILE_FALLBACK_READ=True,
    )
    def test_cutover_check_warn_exit_code(self):
        call_command('normalize_legacy_links_in_file', stdout=io.StringIO())
        call_command('backfill_records_to_sql', '--links', '--object-id', str(self.obj.id), stdout=io.StringIO())
        output = io.StringIO()
        with self.assertRaises(SystemExit) as exited:
            call_command('dbm_cutover_check', '--json', '--object-id', str(self.obj.id), stdout=output)
        self.assertEqual(exited.exception.code, 1)
        payload = json.loads(output.getvalue().strip().splitlines()[-1])
        self.assertEqual(payload['exit_code'], 1)
        self.assertGreaterEqual(len(payload['warnings']), 1)

    @override_settings(
        DBM_READ_FROM_SQL=False,
        DBM_SQL_SOURCE_OF_TRUTH=False,
        DBM_SQL_WRITE_FILE_SECONDARY=True,
        DBM_DUAL_WRITE=False,
        DBM_FILE_FALLBACK_READ=True,
    )
    def test_cutover_check_strict_warnings_promotes_to_error(self):
        call_command('normalize_legacy_links_in_file', stdout=io.StringIO())
        call_command('backfill_records_to_sql', '--links', '--object-id', str(self.obj.id), stdout=io.StringIO())
        output = io.StringIO()
        with self.assertRaises(SystemExit) as exited:
            call_command(
                'dbm_cutover_check',
                '--json',
                '--object-id',
                str(self.obj.id),
                '--strict-warnings',
                stdout=output,
            )
        self.assertEqual(exited.exception.code, 2)
        payload = json.loads(output.getvalue().strip().splitlines()[-1])
        self.assertEqual(payload['exit_code'], 2)
        self.assertTrue(any(item['code'].startswith('STRICT_') for item in payload['errors']))

    def test_dbm_ui_smoke_http_command(self):
        output = io.StringIO()
        call_command(
            'dbm_ui_smoke_http',
            '--object-id',
            str(self.obj.id),
            '--skip-document',
            '--json',
            stdout=output,
        )
        payload = json.loads(output.getvalue().strip().splitlines()[-1])
        self.assertTrue(payload['ok'])
        self.assertGreaterEqual(payload['passed_steps'], 1)

    def test_document_dbm_tree_smoke_command(self):
        output = io.StringIO()
        call_command(
            'document_dbm_tree_smoke',
            '--object-id',
            str(self.obj.id),
            '--doc-id',
            str(self._create_document_pattern(payload=self._build_minimal_document_json(token_text='plain')).id),
            '--json',
            stdout=output,
        )
        payload = json.loads(output.getvalue().strip().splitlines()[-1])
        self.assertTrue(payload['ok'])
        self.assertGreaterEqual(payload['passed_steps'], 1)

    def test_cleanup_legacy_linked_params_command_marks_deprecated_only_on_apply(self):
        legacy_param = Parameter.objects.create(
            object=self.obj,
            name='Связь с legacy',
            data_type='TXTS',
            linked_object=self.child_obj,
            order=333,
        )
        dry_output = io.StringIO()
        call_command('cleanup_legacy_linked_params', '--object-id', str(self.obj.id), stdout=dry_output)
        legacy_param.refresh_from_db()
        self.assertFalse(legacy_param.is_legacy_link_param_deprecated)

        apply_output = io.StringIO()
        call_command(
            'cleanup_legacy_linked_params',
            '--object-id',
            str(self.obj.id),
            '--apply',
            stdout=apply_output,
        )
        legacy_param.refresh_from_db()
        self.assertTrue(legacy_param.is_legacy_link_param_deprecated)

    def test_legacy_endpoint_hits_are_logged(self):
        with self.assertLogs('database_manager.middleware', level='WARNING') as captured:
            response = self.client.get(reverse('get_object', args=[self.obj.id]))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(any('legacy_endpoint_hit' in line for line in captured.output))


class DatabaseManagerFlagGuardrailTests(TestCase):
    class _DummySettings:
        DBM_READ_FROM_SQL = False
        DBM_DUAL_WRITE = False
        DBM_DUAL_WRITE_STRICT_FOR_TESTS = False
        DBM_SQL_SOURCE_OF_TRUTH = False
        DBM_SQL_WRITE_FILE_SECONDARY = True
        DBM_UI_V1_ONLY = False
        DBM_UI_USE_API_FOR_MUTATIONS = True
        DBM_UI_LEGACY_FALLBACK = False

    def test_validate_dbm_flags_reports_errors(self):
        dummy = self._DummySettings()
        dummy.DBM_SQL_SOURCE_OF_TRUTH = True
        dummy.DBM_READ_FROM_SQL = False
        result = validate_dbm_flags(dummy)
        self.assertFalse(result.is_valid)
        self.assertTrue(any('DBM_SQL_SOURCE_OF_TRUTH=1 requires DBM_READ_FROM_SQL=1' in item for item in result.errors))
