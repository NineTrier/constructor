from __future__ import annotations

class TemplateError(Exception):
    """Base error for template processing."""

class ObjectNotFoundError(TemplateError):
    def __init__(self, object_key: str):
        super().__init__(f"Object not found: {object_key}")
        self.object_key = object_key

class RecordNotFoundError(TemplateError):
    def __init__(self, object_key: str, record_id: str):
        super().__init__(f"Record not found in {object_key}: {record_id}")
        self.object_key = object_key
        self.record_id = record_id
