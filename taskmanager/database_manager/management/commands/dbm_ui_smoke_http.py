import json
import uuid
from typing import Any, Dict, List, Optional

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.test import Client

from ...models import Object_ParentObject, Parameter


class Command(BaseCommand):
    help = "Run lightweight user-level HTTP smoke for DBM v1 APIs and key HTML pages."

    def add_arguments(self, parser):
        parser.add_argument("--object-id", type=int, default=None, dest="object_id")
        parser.add_argument("--doc-id", type=int, default=None, dest="doc_id")
        parser.add_argument("--json", action="store_true", default=False, dest="json_output")
        parser.add_argument("--skip-document", action="store_true", default=False, dest="skip_document")
        parser.add_argument("--skip-links", action="store_true", default=False, dest="skip_links")

    def handle(self, *args, **options):
        object_id = options["object_id"]
        doc_id = options["doc_id"]
        json_output = bool(options["json_output"])
        skip_document = bool(options["skip_document"])
        skip_links = bool(options["skip_links"])

        client = Client(HTTP_HOST=self._resolve_client_host())
        user = self._ensure_admin_user()
        client.force_login(user)

        report: Dict[str, Any] = {
            "ok": True,
            "steps": [],
            "meta": {
                "object_id": object_id,
                "doc_id": doc_id,
                "skip_document": skip_document,
                "skip_links": skip_links,
            },
        }

        objects_resp = self._step(
            report,
            name="v1_objects_list",
            status=client.get("/database/api/v1/objects/").status_code,
            expected={200},
        )
        if not objects_resp["ok"]:
            self._finish(report, json_output=json_output)

        list_payload = client.get("/database/api/v1/objects/").json()
        objects = list_payload.get("objects", []) if isinstance(list_payload, dict) else []
        selected_object_id = object_id or (objects[0]["id"] if objects else None)
        if selected_object_id is None:
            self._step(
                report,
                name="select_object",
                status=0,
                expected={1},
                ok=False,
                details={"reason": "No objects found and --object-id was not provided."},
            )
            self._finish(report, json_output=json_output)

        records_path = f"/database/api/v1/objects/{selected_object_id}/records/"
        records_resp = client.get(records_path, {"limit": 5, "offset": 0, "include_schema": 1})
        self._step(report, name="v1_records_list", status=records_resp.status_code, expected={200})
        if records_resp.status_code != 200:
            self._finish(report, json_output=json_output)
        records_payload = records_resp.json()
        schema = records_payload.get("schema", {}).get("parameters", {})
        if not schema:
            self._step(
                report,
                name="schema_present",
                status=0,
                expected={1},
                ok=False,
                details={"reason": "Schema is missing in records list response."},
            )
            self._finish(report, json_output=json_output)

        create_fields = self._build_create_fields(schema)
        create_resp = client.post(
            records_path,
            data=json.dumps({"record": {"fields": create_fields}}),
            content_type="application/json",
            HTTP_X_API_VERSION="v1",
        )
        self._step(report, name="v1_create_record", status=create_resp.status_code, expected={201})
        if create_resp.status_code != 201:
            self._finish(report, json_output=json_output)
        create_payload = create_resp.json()
        record_uid = create_payload.get("record", {}).get("record_uid")
        if not record_uid:
            self._step(
                report,
                name="record_uid_present",
                status=0,
                expected={1},
                ok=False,
                details={"reason": "record_uid not returned by create endpoint."},
            )
            self._finish(report, json_output=json_output)

        detail_path = f"/database/api/v1/objects/{selected_object_id}/records/{record_uid}/"
        get_resp = client.get(detail_path, HTTP_X_API_VERSION="v1")
        self._step(report, name="v1_get_record", status=get_resp.status_code, expected={200})

        patch_payload = self._build_patch_fields(schema)
        patch_resp = client.patch(
            detail_path,
            data=json.dumps({"record": {"fields": patch_payload}}),
            content_type="application/json",
            HTTP_X_API_VERSION="v1",
        )
        self._step(report, name="v1_patch_record", status=patch_resp.status_code, expected={200})

        if skip_links:
            self._step(report, name="v1_links", status=200, expected={200}, details={"skipped": True})
        else:
            self._run_link_steps(report, client, selected_object_id, record_uid)

        delete_resp = client.delete(
            detail_path,
            data=json.dumps({}),
            content_type="application/json",
            HTTP_X_API_VERSION="v1",
        )
        self._step(report, name="v1_delete_record", status=delete_resp.status_code, expected={200})

        if skip_document:
            self._step(report, name="document_view", status=200, expected={200}, details={"skipped": True})
        elif doc_id is None:
            self._step(report, name="document_view", status=200, expected={200}, details={"skipped": "doc_id_not_provided"})
        else:
            doc_resp = client.get("/document/view", {"id": doc_id})
            self._step(report, name="document_view", status=doc_resp.status_code, expected={200, 302})

        self._finish(report, json_output=json_output)

    def _run_link_steps(self, report: Dict[str, Any], client: Client, object_id: int, parent_uid: str) -> None:
        relation = Object_ParentObject.objects.filter(parent_object_id=object_id).order_by("id").first()
        if relation is None:
            self._step(report, name="v1_links", status=200, expected={200}, details={"skipped": "no_relation"})
            return
        child_records_resp = client.get(
            f"/database/api/v1/objects/{relation.object_id}/records/",
            {"limit": 1, "offset": 0, "include_schema": 0},
        )
        if child_records_resp.status_code != 200:
            self._step(
                report,
                name="v1_links_child_records",
                status=child_records_resp.status_code,
                expected={200},
                ok=False,
            )
            return
        child_records = child_records_resp.json().get("records", [])
        if not child_records:
            self._step(report, name="v1_links", status=200, expected={200}, details={"skipped": "no_child_records"})
            return
        child_uid = child_records[0].get("record_uid")
        if not child_uid:
            self._step(report, name="v1_links", status=200, expected={200}, details={"skipped": "child_uid_missing"})
            return

        links_path = f"/database/api/v1/objects/{object_id}/records/{parent_uid}/links/"
        create_link_resp = client.post(
            links_path,
            data=json.dumps({"link_meta_id": relation.id, "child_record_uid": child_uid}),
            content_type="application/json",
            HTTP_X_API_VERSION="v1",
        )
        self._step(report, name="v1_create_link", status=create_link_resp.status_code, expected={201})

        get_links_resp = client.get(links_path, HTTP_X_API_VERSION="v1")
        self._step(report, name="v1_get_links", status=get_links_resp.status_code, expected={200})

        delete_link_resp = client.delete(
            links_path,
            data=json.dumps({"link_meta_id": relation.id, "child_record_uid": child_uid}),
            content_type="application/json",
            HTTP_X_API_VERSION="v1",
        )
        self._step(report, name="v1_delete_link", status=delete_link_resp.status_code, expected={200})

    @staticmethod
    def _build_create_fields(schema: Dict[str, Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
        fields: Dict[str, Dict[str, Any]] = {}
        for index, (param_id, definition) in enumerate(schema.items()):
            data_type = str(definition.get("type") or "TXT")
            fields[param_id] = {
                "type": data_type,
                "value": Command._sample_value(data_type, seed=index),
            }
            if len(fields) >= 4:
                break
        return fields

    @staticmethod
    def _build_patch_fields(schema: Dict[str, Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
        patch_fields: Dict[str, Dict[str, Any]] = {}
        for index, (param_id, definition) in enumerate(schema.items()):
            data_type = str(definition.get("type") or "TXT")
            patch_fields[param_id] = {
                "type": data_type,
                "value": Command._sample_value(data_type, seed=index + 100),
            }
            break
        return patch_fields

    @staticmethod
    def _sample_value(data_type: str, *, seed: int) -> Any:
        normalized = str(data_type or "TXT").upper()
        if normalized in {"TXT", "TXTS"}:
            return f"smoke-{seed}-{uuid.uuid4().hex[:8]}"
        if normalized == "INT":
            return 1000 + seed
        if normalized == "DATE":
            return "2026-01-15"
        if normalized == "ARRAY":
            return [f"v{seed}", "smoke"]
        return f"smoke-{seed}"

    @staticmethod
    def _resolve_client_host() -> str:
        allowed_hosts = list(getattr(settings, "ALLOWED_HOSTS", []) or [])
        for host in allowed_hosts:
            host_value = str(host or "").strip()
            if not host_value or host_value == "*":
                continue
            if ":" in host_value:
                host_value = host_value.split(":", 1)[0]
            return host_value
        return "localhost"

    @staticmethod
    def _ensure_admin_user():
        user_model = get_user_model()
        admin = user_model.objects.filter(is_superuser=True).first()
        if admin is not None:
            return admin
        username = "dbm_smoke_admin"
        admin = user_model.objects.filter(username=username).first()
        if admin is not None:
            return admin
        return user_model.objects.create_superuser(
            username=username,
            email="dbm-smoke@example.com",
            password=uuid.uuid4().hex,
        )

    @staticmethod
    def _step(
        report: Dict[str, Any],
        *,
        name: str,
        status: int,
        expected: set,
        ok: Optional[bool] = None,
        details: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        passed = (status in expected) if ok is None else bool(ok)
        step_payload = {
            "name": name,
            "status": status,
            "expected": sorted(list(expected)),
            "ok": passed,
            "details": details or {},
        }
        report["steps"].append(step_payload)
        if not passed:
            report["ok"] = False
        return step_payload

    def _finish(self, report: Dict[str, Any], *, json_output: bool) -> None:
        report["failed_steps"] = [step for step in report["steps"] if not step["ok"]]
        report["passed_steps"] = len(report["steps"]) - len(report["failed_steps"])
        if json_output:
            self.stdout.write(json.dumps(report, ensure_ascii=False, default=str))
        else:
            self.stdout.write(f"dbm_ui_smoke_http ok={report['ok']}")
            for step in report["steps"]:
                self.stdout.write(f"- {step['name']}: ok={step['ok']} status={step['status']}")
        if not report["ok"]:
            raise SystemExit(2)
