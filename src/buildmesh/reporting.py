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
    open_items = [item for item in recommendations if item["status"] == "pending_review"]
    categories = {"design_conflicts": [], "environmental_risks": [], "material_variances": [], "reviews_required": open_items, "recommended_actions": open_items}
    for item in open_items:
        title = item["title"].casefold()
        if "design" in title or "spatial" in title or "as-built" in title: categories["design_conflicts"].append(item)
        if "environment" in title or "weather" in title or "traffic" in title: categories["environmental_risks"].append(item)
        if "material" in title: categories["material_variances"].append(item)
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
            "progress": {"completed_tasks": task_status.get("completed", 0), "in_progress_tasks": task_status.get("in_progress", 0)},
            "blocked_tasks": [node["id"] for node in task_nodes if node["attributes"].get("status") == "blocked"],
            **{key: value if key not in {"reviews_required", "recommended_actions"} else len(value) for key, value in categories.items()},
        },
        "operational_categories": categories,
        "open_recommendations": [item for item in recommendations if item["status"] == "pending_review"],
        "recent_events": events[:20],
        "provenance_note": "Recommendations are evidence-backed. Approval records are retained before proposed tasks are materialized.",
    }
