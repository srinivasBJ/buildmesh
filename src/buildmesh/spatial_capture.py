"""BuildMesh-owned, evidence-first spatial capture contract.

This module intentionally accepts a small normalized observation format.  It is
not a device SDK, mesh reconstruction pipeline, or claim of physical capture.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any


CAPTURE_FIELDS = {"capture_id", "source", "captured_at", "coordinate_frame", "scope", "entities"}
SOURCE_REQUIRED = {"kind", "device", "format", "fixture", "provenance"}
SOURCE_OPTIONAL = {"source_url", "source_license", "source_checksum", "source_captured_at", "derived_by", "evidence_state"}
FRAME_FIELDS = {"units", "axis"}
SCOPE_FIELDS = {"source_scope_id", "label", "kind", "parent_scope_id"}
ENTITY_REQUIRED = {"source_entity_id", "category", "semantic_type", "geometry", "parent_scope", "relationships", "evidence_reference"}
ENTITY_OPTIONAL = {"material", "confidence"}
GEOMETRY_FIELDS = {"kind", "position", "dimensions", "orientation"}
RELATION_FIELDS = {"type", "target_source_entity_id"}
SOURCE_KINDS = {"roomplan_style", "lidar", "manual", "synthetic", "external_visual", "other"}
CATEGORIES = {"structure", "opening", "furniture", "equipment", "component"}
SCOPE_KINDS = {"site", "building", "floor", "zone"}


def _fail(message: str) -> None:
    raise ValueError(f"spatial capture: {message}")


def _strict_object(value: Any, allowed: set[str], required: set[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        _fail(f"{label} must be an object")
    unknown, missing = set(value) - allowed, required - set(value)
    if unknown:
        _fail(f"{label} has unknown fields: {', '.join(sorted(unknown))}")
    if missing:
        _fail(f"{label} is missing fields: {', '.join(sorted(missing))}")
    return value


def _text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 240:
        _fail(f"{label} must be a non-empty string")
    return value


def _vector(value: Any, label: str, positive: bool = False) -> list[float]:
    if not isinstance(value, list) or len(value) != 3 or any(not isinstance(item, (int, float)) or isinstance(item, bool) for item in value):
        _fail(f"{label} must be a numeric three-vector")
    output = [float(item) for item in value]
    if any(abs(item) > 10_000 for item in output) or (positive and any(item <= 0 for item in output)):
        _fail(f"{label} is outside supported bounds")
    return output


def validate_capture(payload: Any) -> dict[str, Any]:
    """Return a normalized, strictly validated capture without mutating state."""
    data = _strict_object(payload, CAPTURE_FIELDS, CAPTURE_FIELDS, "capture")
    source = _strict_object(data["source"], SOURCE_REQUIRED | SOURCE_OPTIONAL, SOURCE_REQUIRED, "source")
    frame = _strict_object(data["coordinate_frame"], FRAME_FIELDS, FRAME_FIELDS, "coordinate_frame")
    scope = _strict_object(data["scope"], SCOPE_FIELDS, SCOPE_FIELDS, "scope")
    capture_id = _text(data["capture_id"], "capture_id")
    if source["kind"] not in SOURCE_KINDS or not isinstance(source["fixture"], bool):
        _fail("source kind or fixture flag is invalid")
    for field in ("device", "format", "provenance"):
        _text(source[field], f"source.{field}")
    for field in ("source_url", "source_license", "source_checksum", "derived_by", "evidence_state"):
        if field in source:
            _text(source[field], f"source.{field}")
    if "source_captured_at" in source:
        value = source["source_captured_at"]
        if value != "UNKNOWN":
            try:
                datetime.fromisoformat(_text(value, "source.source_captured_at").replace("Z", "+00:00"))
            except ValueError:
                _fail("source.source_captured_at must be ISO-8601 or UNKNOWN")
    if frame not in ({"units": "m", "axis": "right-handed-y-up"}, {"units": "arbitrary_scale", "axis": "colmap-world-unknown-up"}):
        _fail("coordinate_frame is unsupported")
    if scope["kind"] not in SCOPE_KINDS or not isinstance(scope["parent_scope_id"], (str, type(None))):
        _fail("scope kind or parent_scope_id is invalid")
    for field in ("source_scope_id", "label"):
        _text(scope[field], f"scope.{field}")
    try:
        captured_at = datetime.fromisoformat(_text(data["captured_at"], "captured_at").replace("Z", "+00:00")).isoformat()
    except ValueError:
        _fail("captured_at must be ISO-8601")
    if not isinstance(data["entities"], list) or not data["entities"] or len(data["entities"]) > 500:
        _fail("entities must contain 1 to 500 items")
    normalized: list[dict[str, Any]] = []
    source_ids: set[str] = set()
    for index, raw in enumerate(data["entities"]):
        entity = _strict_object(raw, ENTITY_REQUIRED | ENTITY_OPTIONAL, ENTITY_REQUIRED, f"entity[{index}]")
        source_entity_id = _text(entity["source_entity_id"], f"entity[{index}].source_entity_id")
        if source_entity_id in source_ids:
            _fail("source_entity_id values must be unique")
        source_ids.add(source_entity_id)
        if entity["category"] not in CATEGORIES:
            _fail("entity category is unsupported")
        semantic_type = _text(entity["semantic_type"], "semantic_type")
        geometry = _strict_object(entity["geometry"], GEOMETRY_FIELDS, GEOMETRY_FIELDS, "geometry")
        if geometry["kind"] != "oriented_bounding_box":
            _fail("geometry.kind must be oriented_bounding_box")
        parent_scope = _text(entity["parent_scope"], "parent_scope")
        evidence_reference = _text(entity["evidence_reference"], "evidence_reference")
        if not isinstance(entity["relationships"], list):
            _fail("relationships must be a list")
        relationships = []
        for relation in entity["relationships"]:
            item = _strict_object(relation, RELATION_FIELDS, RELATION_FIELDS, "relationship")
            if item["type"] != "contains":
                _fail("only explicit containment relationships are supported")
            relationships.append({"type": "contains", "target_source_entity_id": _text(item["target_source_entity_id"], "relationship target")})
        confidence = entity.get("confidence")
        if confidence is not None and (not isinstance(confidence, (int, float)) or isinstance(confidence, bool) or not 0 <= confidence <= 1):
            _fail("confidence must be a number from zero to one")
        material = entity.get("material")
        if material is not None:
            material = _text(material, "material")
        normalized.append({
            "source_entity_id": source_entity_id, "category": entity["category"], "semantic_type": semantic_type,
            "geometry": {"kind": "oriented_bounding_box", "position": _vector(geometry["position"], "geometry.position"), "dimensions": _vector(geometry["dimensions"], "geometry.dimensions", positive=True), "orientation": _vector(geometry["orientation"], "geometry.orientation")},
            "parent_scope": parent_scope, "relationships": relationships, "evidence_reference": evidence_reference,
            **({"material": material} if material is not None else {}),
            **({"confidence": float(confidence)} if confidence is not None else {}),
        })
    valid_scopes = {scope["source_scope_id"], *source_ids}
    for entity in normalized:
        if entity["parent_scope"] not in valid_scopes:
            _fail("entity parent_scope is not capture scope or an entity")
        for relation in entity["relationships"]:
            if relation["target_source_entity_id"] not in source_ids:
                _fail("relationship target is not an entity")
    return {"capture_id": capture_id, "source": dict(source), "captured_at": captured_at, "coordinate_frame": dict(frame), "scope": dict(scope), "entities": normalized}
