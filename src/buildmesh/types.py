from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import uuid4


def now() -> str:
    return datetime.now(UTC).isoformat()


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex}"


class RecommendationStatus(StrEnum):
    PENDING_REVIEW = "pending_review"
    APPROVED = "approved"
    REJECTED = "rejected"


class TaskStatus(StrEnum):
    OPEN = "open"
    IN_PROGRESS = "in_progress"
    BLOCKED = "blocked"
    COMPLETED = "completed"


class Severity(StrEnum):
    INFO = "info"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


@dataclass(frozen=True)
class Evidence:
    project_id: str
    kind: str
    source: str
    payload: dict[str, Any]
    captured_at: str = field(default_factory=now)
    confidence: float | None = None
    id: str = field(default_factory=lambda: new_id("evidence"))

    def data(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class Recommendation:
    project_id: str
    title: str
    rationale: str
    severity: Severity
    evidence_ids: list[str]
    proposed_task: dict[str, Any] | None = None
    status: RecommendationStatus = RecommendationStatus.PENDING_REVIEW
    id: str = field(default_factory=lambda: new_id("rec"))
    created_at: str = field(default_factory=now)

    def data(self) -> dict[str, Any]:
        result = asdict(self)
        result["severity"] = self.severity.value
        result["status"] = self.status.value
        return result
