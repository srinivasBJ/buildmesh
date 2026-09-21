"""Evidence-first as-built project twin. No geometry or reconstruction claims."""
from __future__ import annotations

from collections import Counter
from typing import Any

from .types import Evidence, Severity


COMPONENT_STATES = {"NOT_STARTED", "IN_PROGRESS", "COMPLETE", "NOT_OBSERVED", "CONFLICTING", "NEEDS_REVIEW"}
EPISTEMIC_STATES = {"VERIFIED", "INFERRED", "ASSUMED", "UNKNOWN", "CONFLICTING", "STALE", "NEEDS_REVIEW"}


def require_state(state: str) -> str:
    if state not in COMPONENT_STATES:
        raise ValueError("component state is unsupported")
    return state


def hierarchy(store: Any, project_id: str, parent_id: str, kind: str, label: str, attributes: dict[str, Any] | None = None) -> dict[str, Any]:
    if kind not in {"site", "building", "floor", "zone", "component"}:
        raise ValueError("twin hierarchy kind is unsupported")
    parent = store.get_node(parent_id)
    if parent["project_id"] != project_id:
        raise ValueError("twin parent must be in the project")
    node = store.add_node(project_id, kind, label, attributes or {})
    store.add_edge(project_id, parent_id, node["id"], "contains")
    return node


def create_snapshot(store: Any, project_id: str, label: str, captured_at: str, source_evidence_ids: list[str], observations: list[dict[str, Any]]) -> dict[str, Any]:
    if not label.strip() or not source_evidence_ids or not isinstance(observations, list):
        raise ValueError("snapshot needs label, source evidence, and an observation list")
    seen = set()
    normalized = []
    for item in observations:
        if not isinstance(item, dict) or set(item) != {"component_id", "observed_state", "confidence", "epistemic_state", "zone_id", "fixture"}:
            raise ValueError("twin observation has an invalid schema")
        component = store.get_node(item["component_id"])
        if component["project_id"] != project_id or component["kind"] != "component":
            raise ValueError("twin observation references an unknown component")
        if item["component_id"] in seen:
            raise ValueError("snapshot cannot observe a component twice")
        seen.add(item["component_id"])
        require_state(item["observed_state"])
        if item["epistemic_state"] not in EPISTEMIC_STATES or not isinstance(item["confidence"], (int, float)) or not 0 <= item["confidence"] <= 1:
            raise ValueError("twin observation confidence or epistemic state is invalid")
        if item["zone_id"] is not None:
            zone = store.get_node(item["zone_id"])
            if zone["project_id"] != project_id or zone["kind"] != "zone":
                raise ValueError("twin observation references an unknown zone")
        normalized.append({**item, "confidence": float(item["confidence"])})
    for evidence_id in source_evidence_ids:
        if store.get_evidence(evidence_id)["project_id"] != project_id:
            raise ValueError("snapshot source evidence belongs to another project")
    snapshot = store.add_node(project_id, "snapshot", label, {"captured_at": captured_at, "source_evidence_ids": source_evidence_ids, "immutable": True, "observation_count": len(normalized)})
    store.add_edge(project_id, store.project_root(project_id)["id"], snapshot["id"], "contains")
    for evidence_id in source_evidence_ids:
        evidence_node = store.find_graph_node(project_id, "evidence_id", evidence_id)
        if evidence_node:
            store.add_edge(project_id, evidence_node["id"], snapshot["id"], "supports")
    for item in normalized:
        evidence = store.add_evidence(Evidence(project_id=project_id, kind="twin_observation", source=f"snapshot:{snapshot['id']}", payload={"snapshot_id": snapshot["id"], **item}, confidence=item["confidence"]), related_node_id=item["component_id"], relation="observes")
        store.add_edge(project_id, snapshot["id"], evidence["graph_node_id"], "contains")
    store.record_event(project_id, "twin_snapshot_created", {"snapshot_id": snapshot["id"], "source_evidence_ids": source_evidence_ids}, snapshot["id"])
    return snapshot


def snapshot_observations(store: Any, project_id: str, snapshot_id: str) -> list[dict[str, Any]]:
    snapshot = store.get_node(snapshot_id)
    if snapshot["project_id"] != project_id or snapshot["kind"] != "snapshot":
        raise ValueError("unknown snapshot")
    return [item for item in store.evidence(project_id) if item["kind"] == "twin_observation" and item["payload"].get("snapshot_id") == snapshot_id]


def twin_diff(store: Any, project_id: str, previous_id: str, current_id: str) -> list[dict[str, Any]]:
    previous = {item["payload"]["component_id"]: item for item in snapshot_observations(store, project_id, previous_id)}
    current = {item["payload"]["component_id"]: item for item in snapshot_observations(store, project_id, current_id)}
    changes = []
    for component_id in sorted(set(previous) | set(current)):
        before, after = previous.get(component_id), current.get(component_id)
        if before is None:
            changes.append({"type": "COMPONENT_APPEARED", "component_id": component_id, "evidence_ids": [after["id"]]})
        elif after is None:
            changes.append({"type": "NOT_OBSERVED_IN_CURRENT_SNAPSHOT", "component_id": component_id, "evidence_ids": [before["id"]], "claim": "absence of current observation; not confirmed removed"})
        elif before["payload"]["observed_state"] != after["payload"]["observed_state"]:
            changes.append({"type": "STATE_CHANGED", "component_id": component_id, "from": before["payload"]["observed_state"], "to": after["payload"]["observed_state"], "evidence_ids": [before["id"], after["id"]]})
    return changes


def completeness(store: Any, project_id: str, scope_id: str, snapshot_id: str) -> dict[str, Any]:
    graph = store.graph(project_id)
    descendants = {scope_id}
    changed = True
    while changed:
        changed = False
        for edge in graph["edges"]:
            if edge["relation"] == "contains" and edge["source_id"] in descendants and edge["target_id"] not in descendants:
                descendants.add(edge["target_id"])
                changed = True
    components = [node for node in graph["nodes"] if node["kind"] == "component" and node["id"] in descendants]
    observations = {item["payload"]["component_id"]: item for item in snapshot_observations(store, project_id, snapshot_id)}
    observed = [item for component, item in observations.items() if component in {node["id"] for node in components}]
    verified = [item for item in observed if item["payload"]["epistemic_state"] == "VERIFIED"]
    completed = [item for item in observed if item["payload"]["observed_state"] == "COMPLETE"]
    conflicting = [item for item in observed if item["payload"]["observed_state"] == "CONFLICTING"]
    expected = len(components)
    return {"scope_id": scope_id, "snapshot_id": snapshot_id, "expected_components": expected, "observed_components": len(observed), "verified_components": len(verified), "completion_estimate": round(len(completed) / expected, 4) if expected else None, "evidence_coverage": round(len(observed) / expected, 4) if expected else None, "confidence": round(sum(item["confidence"] or 0 for item in observed) / len(observed), 4) if observed else None, "missing_evidence_count": max(expected - len(observed), 0), "conflicting_evidence_count": len(conflicting), "formula": "completion=COMPLETE observations/expected components; coverage=observed/expected; not an engineering certification"}
