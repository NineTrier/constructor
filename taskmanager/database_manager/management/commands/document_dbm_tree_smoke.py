import json
import uuid
from typing import Any, Dict, Optional

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.test import Client

from ...models import ObjectLinkMeta, Object_ParentObject, Parameter
from document.models import DocumentPattern_Objects, DocumentsPattern


class Command(BaseCommand):
    help = "Smoke-check document DBM tree pipeline: prefetch_graph + resolve_tokens."

    def add_arguments(self, parser):
        parser.add_argument("--object-id", type=int, default=None, dest="object_id")
        parser.add_argument("--doc-id", type=int, default=None, dest="doc_id")
        parser.add_argument("--json", action="store_true", default=False, dest="json_output")

    def handle(self, *args, **options):
        object_id = options.get("object_id")
        doc_id = options.get("doc_id")
        json_output = bool(options.get("json_output"))

        client = Client(HTTP_HOST=self._resolve_client_host())
        user = self._ensure_admin_user()
        client.force_login(user)

        report: Dict[str, Any] = {
            "ok": True,
            "steps": [],
            "meta": {
                "object_id": object_id,
                "doc_id": doc_id,
            },
        }

        objects_resp = client.get("/database/api/v1/objects/")
        self._step(report, name="objects_list", status=objects_resp.status_code, expected={200})
        if objects_resp.status_code != 200:
            self._finish(report, json_output)

        objects_payload = objects_resp.json() if objects_resp.content else {}
        objects = objects_payload.get("objects", []) if isinstance(objects_payload, dict) else []
        selected_object_id = int(object_id or (objects[0]["id"] if objects else 0))
        if not selected_object_id:
            self._step(report, name="select_object", status=0, expected={1}, ok=False, details={"reason": "No objects"})
            self._finish(report, json_output)

        records_resp = client.get(
            f"/database/api/v1/objects/{selected_object_id}/records/",
            {"limit": 1, "offset": 0, "include_schema": 1, "order": "identificator"},
        )
        self._step(report, name="records_list", status=records_resp.status_code, expected={200})
        if records_resp.status_code != 200:
            self._finish(report, json_output)

        records_payload = records_resp.json()
        records = records_payload.get("records", []) if isinstance(records_payload, dict) else []
        if not records:
            self._step(report, name="records_present", status=0, expected={1}, ok=False, details={"reason": "No records"})
            self._finish(report, json_output)
        root_uid = str(records[0].get("record_uid") or "").strip()
        if not root_uid:
            self._step(report, name="root_uid_present", status=0, expected={1}, ok=False)
            self._finish(report, json_output)

        selected_doc_id = self._pick_document_id(client=client, requested_doc_id=doc_id, object_id=selected_object_id)
        if not selected_doc_id:
            self._step(report, name="document_select", status=0, expected={1}, ok=False, details={"reason": "No document for object"})
            self._finish(report, json_output)

        token = self._build_token_for_object(selected_object_id)
        if not token:
            schema = records_payload.get("schema", {}) if isinstance(records_payload, dict) else {}
            params = (schema.get("parameters") or {}) if isinstance(schema, dict) else {}
            first_param_id = next(iter(params.keys()), None)
            if not first_param_id:
                self._step(report, name="token_build", status=0, expected={1}, ok=False, details={"reason": "No parameter for token"})
                self._finish(report, json_output)
            token = f"{{:obj({selected_object_id}).param({int(first_param_id)}):}}"

        context = {str(selected_object_id): root_uid}
        prefetch_resp = client.post(
            "/document/api/v1/prefetch_graph/",
            data=json.dumps(
                {
                    "document_id": selected_doc_id,
                    "context": context,
                    "tokens": [token],
                    "options": {"maxDepth": 8, "includeTrace": False},
                }
            ),
            content_type="application/json",
        )
        self._step(report, name="prefetch_graph", status=prefetch_resp.status_code, expected={200})

        resolve_resp = client.post(
            "/document/api/v1/resolve_tokens/",
            data=json.dumps(
                {
                    "document_id": selected_doc_id,
                    "context": context,
                    "tokens": [token],
                    "options": {"maxDepth": 8, "aggregationMode": "first", "joiner": ", "},
                }
            ),
            content_type="application/json",
        )
        self._step(report, name="resolve_tokens", status=resolve_resp.status_code, expected={200})
        if resolve_resp.status_code == 200:
            payload = resolve_resp.json()
            summary = payload.get("summary", {}) if isinstance(payload, dict) else {}
            has_critical = int(summary.get("errors", 0)) > 0
            self._step(
                report,
                name="resolve_tokens_no_errors",
                status=0 if has_critical else 1,
                expected={1},
                ok=not has_critical,
                details={"summary": summary},
            )

        self._finish(report, json_output)

    @staticmethod
    def _build_token_for_object(object_id: int) -> str:
        relation = Object_ParentObject.objects.filter(parent_object_id=object_id).order_by("id").first()
        if relation is None:
            return ""
        meta = ObjectLinkMeta.objects.filter(parent_object_id=object_id, object_link=relation).order_by("order", "id").first()
        if meta is None:
            return ""
        child_param = Parameter.objects.filter(object_id=relation.object_id, identificator=True).order_by("id").first()
        if child_param is None:
            child_param = Parameter.objects.filter(object_id=relation.object_id).order_by("id").first()
        if child_param is None:
            return ""
        return f"{{:obj({object_id}).link({meta.id}).param({child_param.id}):}}"

    @staticmethod
    def _pick_document_id(*, client: Client, requested_doc_id: Optional[int], object_id: int) -> Optional[int]:
        if requested_doc_id:
            return int(requested_doc_id)
        row = (
            DocumentPattern_Objects.objects.filter(object_id=object_id)
            .order_by("-id")
            .values_list("document_id", flat=True)
            .first()
        )
        if row:
            return int(row)
        document = DocumentsPattern.objects.order_by("-id").first()
        return int(document.id) if document is not None else None

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
        username = "dbm_tree_smoke_admin"
        admin = user_model.objects.filter(username=username).first()
        if admin is not None:
            return admin
        return user_model.objects.create_superuser(
            username=username,
            email="dbm-tree-smoke@example.com",
            password=uuid.uuid4().hex,
        )

    @staticmethod
    def _step(report: Dict[str, Any], *, name: str, status: int, expected: set, ok: Optional[bool] = None, details: Optional[Dict[str, Any]] = None):
        passed = (status in expected) if ok is None else bool(ok)
        payload = {
            "name": name,
            "status": status,
            "expected": sorted(list(expected)),
            "ok": passed,
            "details": details or {},
        }
        report["steps"].append(payload)
        if not passed:
            report["ok"] = False
        return payload

    def _finish(self, report: Dict[str, Any], json_output: bool):
        report["failed_steps"] = [step for step in report["steps"] if not step["ok"]]
        report["passed_steps"] = len(report["steps"]) - len(report["failed_steps"])
        if json_output:
            self.stdout.write(json.dumps(report, ensure_ascii=False, default=str))
        else:
            self.stdout.write(f"document_dbm_tree_smoke ok={report['ok']}")
            for step in report["steps"]:
                self.stdout.write(f"- {step['name']}: ok={step['ok']} status={step['status']}")
        if not report["ok"]:
            raise SystemExit(2)
