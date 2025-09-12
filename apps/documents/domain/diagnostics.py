from __future__ import annotations
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional

@dataclass
class PlaceholderIssue:
    path: str
    object_key: Optional[str]
    field_key: str
    reason: str

@dataclass
class RenderDiagnostics:
    started_at_ms: int
    finished_at_ms: int = 0
    placeholders_total: int = 0
    placeholders_replaced: int = 0
    issues: List[PlaceholderIssue] = field(default_factory=list)

    def add_issue(self, path: str, object_key: Optional[str], field_key: str, reason: str) -> None:
        self.issues.append(PlaceholderIssue(path=path, object_key=object_key, field_key=field_key, reason=reason))

    @property
    def duration_ms(self) -> int:
        return max(0, self.finished_at_ms - self.started_at_ms)
