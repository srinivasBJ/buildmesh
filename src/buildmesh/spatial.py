"""Normalized, evidence-first spatial-plan foundation; not a CAD/BIM parser or editor."""
from __future__ import annotations

from typing import Any

SPATIAL_KINDS = {"building", "level", "zone", "room", "component", "opening", "system", "connection"}
GEOMETRY_TYPES = {"point", "line", "polygon", "bounding_box"}
ORIENTATIONS = {"north", "south", "east", "west", "UNKNOWN"}

def geometry_status(geometry: dict[str, Any] | None) -> str:
    if geometry is None: return "UNAVAILABLE"
    if geometry.get("type") != "bounding_box" or len(geometry.get("coordinates", [])) not in {4, 6}: return "UNSUPPORTED"
    return "VERIFIED"

def _box(geometry: dict[str, Any] | None) -> tuple[float, float, float, float] | None:
    if geometry_status(geometry) != "VERIFIED": return None
    values = geometry["coordinates"]
    try: return tuple(map(float, values[:4]))  # type: ignore[return-value]
    except (TypeError, ValueError): return None

def geometric_relationship(first: dict[str, Any] | None, second: dict[str, Any] | None) -> str | None:
    a, b = _box(first), _box(second)
    if not a or not b: return None
    ax1, ay1, ax2, ay2 = a; bx1, by1, bx2, by2 = b
    if bx1 >= ax1 and by1 >= ay1 and bx2 <= ax2 and by2 <= ay2: return "CONTAINS"
    if ax1 >= bx1 and ay1 >= by1 and ax2 <= bx2 and ay2 <= by2: return "WITHIN"
    if max(ax1, bx1) < min(ax2, bx2) and max(ay1, by1) < min(ay2, by2): return "OVERLAPS"
    if max(ax1, bx1) <= min(ax2, bx2) and max(ay1, by1) <= min(ay2, by2): return "ADJACENT_TO"
    return None


def validate_plan(payload: dict[str, Any]) -> list[dict[str, Any]]:
    if not isinstance(payload, dict) or set(payload) != {"schema_version", "source", "entities"} or payload["schema_version"] != "buildmesh-spatial-plan-v1":
        raise ValueError("spatial plan must use buildmesh-spatial-plan-v1")
    if not isinstance(payload["source"], dict) or not isinstance(payload["source"].get("reference"), str) or not payload["source"]["reference"].strip():
        raise ValueError("spatial plan source reference is required")
    entities = payload["entities"]
    if not isinstance(entities, list) or not entities:
        raise ValueError("spatial plan needs entities")
    ids = set()
    for entity in entities:
        required = {"id", "kind", "label", "parent_id", "attributes", "geometry", "orientation"}
        if not isinstance(entity, dict) or set(entity) != required or not isinstance(entity["id"], str) or not entity["id"].strip() or entity["id"] in ids or entity["kind"] not in SPATIAL_KINDS or not isinstance(entity["label"], str) or not entity["label"].strip() or not isinstance(entity["attributes"], dict):
            raise ValueError("spatial entity schema is invalid")
        ids.add(entity["id"])
        if entity["parent_id"] is not None and (not isinstance(entity["parent_id"], str) or not entity["parent_id"]):
            raise ValueError("spatial parent_id is invalid")
        if entity["orientation"] not in ORIENTATIONS:
            raise ValueError("spatial orientation is invalid")
        geometry = entity["geometry"]
        if geometry is not None:
            if not isinstance(geometry, dict) or set(geometry) != {"type", "coordinates", "coordinate_system", "dimensions"} or geometry["type"] not in GEOMETRY_TYPES or not isinstance(geometry["coordinates"], list) or not isinstance(geometry["coordinate_system"], str) or not isinstance(geometry["dimensions"], dict):
                raise ValueError("geometry reference schema is invalid")
    known = set(ids)
    if any(item["parent_id"] is not None and item["parent_id"] not in known for item in entities):
        raise ValueError("spatial parent_id must reference an imported entity")
    return entities


def planned_nodes(graph: dict[str, Any], scope_id: str) -> list[dict[str, Any]]:
    descendants = {scope_id}; changed = True
    while changed:
        changed = False
        for edge in graph["edges"]:
            if edge["relation"] == "part_of" and edge["target_id"] in descendants and edge["source_id"] not in descendants:
                descendants.add(edge["source_id"]); changed = True
    return [node for node in graph["nodes"] if node["id"] in descendants and node["kind"] == "planned_component"]
