"""Normalize bounded, externally derived sparse-scene summaries.

This adapter deliberately retains only a scene-bounds observation. It does not
assign building semantics to a point cloud or trajectory.
"""
from __future__ import annotations

from typing import Any

from .spatial_capture import validate_capture


SUMMARY_FIELDS = {
    "capture_id", "source_url", "source_license", "source_checksum", "source_captured_at",
    "ingested_at", "dataset", "device", "frame_count", "registered_images", "points3d_count",
    "bounds_center", "bounds_dimensions", "derived_by", "fixture",
}


def _text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"external spatial: {field} must be a non-empty string")
    return value


def _vector(value: Any, field: str, positive: bool = False) -> list[float]:
    if not isinstance(value, list) or len(value) != 3 or any(not isinstance(item, (int, float)) or isinstance(item, bool) for item in value):
        raise ValueError(f"external spatial: {field} must be a numeric three-vector")
    output = [float(item) for item in value]
    if positive and any(item <= 0 for item in output):
        raise ValueError(f"external spatial: {field} must be positive")
    return output


def normalize_external_spatial(summary: Any) -> dict[str, Any]:
    """Map an external sparse-reconstruction summary into the capture contract."""
    if not isinstance(summary, dict) or set(summary) != SUMMARY_FIELDS:
        raise ValueError("external spatial: summary fields are invalid")
    for field in ("capture_id", "source_url", "source_license", "source_checksum", "source_captured_at", "ingested_at", "dataset", "device", "derived_by"):
        _text(summary[field], field)
    if not isinstance(summary["fixture"], bool):
        raise ValueError("external spatial: fixture must be boolean")
    for field in ("frame_count", "registered_images", "points3d_count"):
        if not isinstance(summary[field], int) or isinstance(summary[field], bool) or summary[field] < 1:
            raise ValueError(f"external spatial: {field} must be a positive integer")
    center = _vector(summary["bounds_center"], "bounds_center")
    dimensions = _vector(summary["bounds_dimensions"], "bounds_dimensions", positive=True)
    source_scope_id = f"external-scene:{summary['capture_id']}"
    return validate_capture({
        "capture_id": summary["capture_id"],
        "source": {
            "kind": "external_visual",
            "device": summary["device"],
            "format": "pycolmap/sparse-reconstruction-v1",
            "fixture": summary["fixture"],
            "provenance": "public real-world visual sequence; locally derived sparse reconstruction",
            "source_url": summary["source_url"],
            "source_license": summary["source_license"],
            "source_checksum": summary["source_checksum"],
            "source_captured_at": summary["source_captured_at"],
            "derived_by": summary["derived_by"],
            "evidence_state": "DERIVED",
        },
        "captured_at": summary["ingested_at"],
        "coordinate_frame": {"units": "arbitrary_scale", "axis": "colmap-world-unknown-up"},
        "scope": {"source_scope_id": source_scope_id, "label": f"External scene: {summary['dataset']}", "kind": "zone", "parent_scope_id": None},
        "entities": [{
            "source_entity_id": "sparse-scene-bounds",
            "category": "component",
            "semantic_type": "UNKNOWN",
            "geometry": {"kind": "oriented_bounding_box", "position": center, "dimensions": dimensions, "orientation": [0.0, 0.0, 0.0]},
            "parent_scope": source_scope_id,
            "relationships": [],
            "evidence_reference": "capture",
        }],
    })
