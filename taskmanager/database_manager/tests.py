import io
import json
import tempfile
import uuid
from unittest.mock import patch

import pandas as pd
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse

from .models import (
    Object,
    ObjectLink_identificators,
    ObjectRecord,
    Object_ParentObject,
    Parameter,
    ParameterValue,
    RecordLink,
)
from .views import _safe_load_dataframe, _write_dataframe
from .application.flag_guardrails import validate_dbm_flags


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
        ObjectLink_identificators.objects.create(
            object_link=self.relation,
            parent_object_identificator=self.parent_uids[0],
            object_identificator=self.child_uid,
        )

    def _csv_file(self, text: str, name: str = "rows.csv"):
        return SimpleUploadedFile(name, text.encode("utf-8"), content_type="text/csv")

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
        self.assertEqual(payload['page']['total'], 3)
        self.assertIn('record_uid', payload['records'][0])
        self.assertIn('fields', payload['records'][0])

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
            {'limit': 10, 'offset': 0, 'order': 'record_uid', 'q': 'SQL-CAR-1'},
        )
        self.assertEqual(sql_response.status_code, 200)
        sql_payload = sql_response.json()
        self.assertEqual(sql_payload['page']['total'], 1)
        self.assertEqual(sql_payload['records'][0]['record_uid'], 'sql-record-1')

        ObjectRecord.objects.filter(object=self.obj).delete()
        with self.assertLogs('database_manager.views', level='WARNING') as captured:
            fallback_response = self.client.get(
                reverse('api_v1_object_records', args=[self.obj.id]),
                {'limit': 10, 'offset': 0, 'order': 'identificator'},
            )
        self.assertEqual(fallback_response.status_code, 200)
        fallback_payload = fallback_response.json()
        self.assertGreaterEqual(fallback_payload['page']['total'], 1)
        self.assertTrue(any('sql_miss' in line for line in captured.output))

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
                    "link_meta_id": self.relation.id,
                    "child_record_uid": self.child_uid,
                }
            ),
            content_type='application/json',
            HTTP_X_API_VERSION='v1',
        )
        self.assertEqual(create_link_response.status_code, 201)
        create_payload = create_link_response.json()
        self.assertEqual(create_payload['link']['link_meta_id'], self.relation.id)

        get_links_response = self.client.get(
            reverse('api_v1_record_links', args=[self.obj.id, parent_uid]),
            HTTP_X_API_VERSION='v1',
        )
        self.assertEqual(get_links_response.status_code, 200)
        get_payload = get_links_response.json()
        link_entry = next((item for item in get_payload['links'] if item['link_meta_id'] == self.relation.id), None)
        self.assertIsNotNone(link_entry)
        self.assertIn(self.child_uid, link_entry['child_record_uids'])

        delete_link_response = self.client.delete(
            reverse('api_v1_record_links', args=[self.obj.id, parent_uid]),
            data=json.dumps(
                {
                    "link_meta_id": self.relation.id,
                    "child_record_uid": self.child_uid,
                }
            ),
            content_type='application/json',
            HTTP_X_API_VERSION='v1',
        )
        self.assertEqual(delete_link_response.status_code, 200)
        self.assertGreaterEqual(delete_link_response.json()['deleted'], 1)

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
