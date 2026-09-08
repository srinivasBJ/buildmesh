from __future__ import annotations

from collections import Counter
from typing import Any

from .storage import Store


def build_daily_report(store: Store, project_id: str) -> dict[str, Any]:
    """Creates a structured report payload ready for a PDF/email presentation adapter."""
    graph = store.graph(project_id)
    evidence = store.evidence(project_id)
    recommendations = store.recommendations(project_id)
    events = store.events(project_id)
    task_nodes = [node for node in graph["nodes"] if node["kind"] == "task"]
    task_status = Counter(str(node["attributes"].get("status", "unknown")) for node in task_nodes)
    dependencies = [edge for edge in graph["edges"] if edge["relation"] == "depends_on"]
    members = [node for node in graph["nodes"] if node["kind"] == "project_member" and node["attributes"].get("active")]
    assignments = [edge for edge in graph["edges"] if edge["relation"] == "assigned_to"]
    by_kind = Counter(item["kind"] for item in evidence)
    by_status = Counter(item["status"] for item in recommendations)
    return {
        "report_type": "daily_project_intelligence",
        "project": graph["project"],
        "summary": {
            "task_count": len(task_nodes),
            "task_status": dict(task_status),
            "task_dependency_count": len(dependencies),
            "active_member_count": len(members),
            "task_assignment_count": len(assignments),
            "evidence_count": len(evidence),
            "recommendation_status": dict(by_status),
            "evidence_by_kind": dict(by_kind),
            "event_count": len(events),
        },
        "open_recommendations": [item for item in recommendations if item["status"] == "pending_review"],
        "recent_events": events[:20],
        "provenance_note": "Recommendations are evidence-backed. Approval records are retained before proposed tasks are materialized.",
    }
