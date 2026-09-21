"""Bounded planned-versus-observed reconciliation; not engineering certification."""
from __future__ import annotations
from typing import Any

TYPE_COMPATIBILITY = {"IfcWindow": {"IfcWindow", "window"}, "IfcDoor": {"IfcDoor", "door"}, "IfcWall": {"IfcWall", "wall"}}
SCOPE_LEVELS = ("project", "building", "floor", "zone", "room", "component")

def compare_scope_hierarchy(planned: dict[str, Any] | None, observed: dict[str, Any] | None) -> dict[str, Any]:
    """Compare explicit scope IDs without inferring missing hierarchy."""
    planned = planned or {}; observed = observed or {}
    for level in SCOPE_LEVELS:
        left, right = planned.get(level), observed.get(level)
        if left is None or right is None:
            return {"decision": "UNKNOWN", "scope_conflict_level": None, "planned": planned, "observed": observed, "epistemic_state": "UNKNOWN"}
        if left != right:
            return {"decision": "SCOPE_CONFLICT", "scope_conflict_level": level.upper(), "planned": planned, "observed": observed, "epistemic_state": "NEEDS_REVIEW"}
    return {"decision": "CONSISTENT", "scope_conflict_level": None, "planned": planned, "observed": observed, "epistemic_state": "VERIFIED"}

def reconcile(planned: dict[str, Any], observed: dict[str, Any] | None, match: dict[str, Any] | None, task_ids: list[str], environment_ids: list[str], tolerance: float, planned_scope_id: str | None = None, observed_scope_id: str | None = None) -> dict[str, Any]:
    if tolerance <= 0: raise ValueError("spatial tolerance must be positive")
    evidence = (match or {}).get("observation_evidence_ids", [])
    decision = (match or {}).get("decision", "UNKNOWN")
    if decision == "NOT_OBSERVED":
        return {"state": "NOT_OBSERVED", "deviation": None, "evidence_sufficiency": "LOW", "requires_review": False, "trace": {"planned_component_id": planned["id"], "match": match, "task_ids": task_ids, "environment_evidence_ids": environment_ids, "evidence_ids": evidence}}
    if not observed or decision in {"UNKNOWN", "CONFLICTING"}:
        return {"state": decision, "deviation": None, "evidence_sufficiency": "REVIEW_REQUIRED", "requires_review": decision == "CONFLICTING", "trace": {"planned_component_id": planned["id"], "match": match, "task_ids": task_ids, "environment_evidence_ids": environment_ids, "evidence_ids": evidence}}
    planned_type, observed_type = planned["attributes"].get("ifc_class"), observed["attributes"].get("ifc_class") or observed["attributes"].get("component_type")
    if planned_scope_id and observed_scope_id and planned_scope_id != observed_scope_id:
        state, deviation = "SCOPE_CONFLICT", {"planned_scope_id": planned_scope_id, "observed_scope_id": observed_scope_id}
    elif planned_type and observed_type not in TYPE_COMPATIBILITY.get(planned_type, {planned_type}):
        state, deviation = "TYPE_CONFLICT", {"planned_type": planned_type, "observed_type": observed_type}
    else:
        pp, op = planned["attributes"].get("placement", {}), observed["attributes"].get("placement", {})
        if all(isinstance(v, (int, float)) for v in (pp.get("x"), pp.get("y"), op.get("x"), op.get("y"))):
            distance = round(((pp["x"]-op["x"])**2 + (pp["y"]-op["y"])**2) ** .5, 4)
            state, deviation = ("SPATIAL_DEVIATION", {"distance": distance, "tolerance": tolerance}) if distance > tolerance else ("MATCHED", {"distance": distance, "tolerance": tolerance})
        else: state, deviation = "UNKNOWN", None
    sufficiency = "HIGH" if match.get("matching_method") == "IDENTITY" else "MEDIUM" if match.get("matching_method") == "SPATIAL" else "LOW"
    return {"state": state, "deviation": deviation, "evidence_sufficiency": sufficiency, "requires_review": state in {"SPATIAL_DEVIATION", "TYPE_CONFLICT", "SCOPE_CONFLICT"}, "trace": {"planned_component_id": planned["id"], "observed_component_id": observed["id"], "match": match, "task_ids": task_ids, "environment_evidence_ids": environment_ids, "evidence_ids": evidence}}
