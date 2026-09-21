"""IFC adapter using IfcOpenShell; normalized output only, no geometry reconstruction."""
from __future__ import annotations
from hashlib import sha256
from pathlib import Path
from typing import Any
from math import atan2, degrees

SUPPORTED = {"IfcSite": "zone", "IfcBuilding": "building", "IfcBuildingStorey": "level", "IfcSpace": "room", "IfcWall": "component", "IfcWallStandardCase": "component", "IfcSlab": "component", "IfcDoor": "opening", "IfcWindow": "opening", "IfcRoof": "component", "IfcColumn": "component", "IfcBeam": "component", "IfcStair": "component"}

def parse(path: str | Path) -> dict[str, Any]:
    try:
        import ifcopenshell
    except ImportError as exc:
        raise RuntimeError("IfcOpenShell is required for IFC import; install buildmesh[ifc]") from exc
    raw = Path(path).read_bytes(); digest = sha256(raw).hexdigest()
    try: model = ifcopenshell.open(str(path))
    except Exception as exc: raise ValueError("malformed or unsupported IFC file") from exc
    entities, unsupported = [], []
    relationships: list[dict[str, str]] = []
    for entity in model:
        kind = entity.is_a()
        if kind == "IfcProject":
            continue
        if kind not in SUPPORTED:
            if kind.startswith("Ifc") and kind not in {"IfcOwnerHistory", "IfcApplication", "IfcPerson", "IfcOrganization", "IfcRelAggregates", "IfcRelContainedInSpatialStructure", "IfcCartesianPoint", "IfcAxis2Placement3D", "IfcLocalPlacement", "IfcUnitAssignment"}: unsupported.append(kind)
            continue
        gid = getattr(entity, "GlobalId", None) or f"ifc-step-{entity.id()}"
        parent = None
        for relation in getattr(entity, "Decomposes", []) or []:
            parent = getattr(getattr(relation, "RelatingObject", None), "GlobalId", None)
        for relation in getattr(entity, "ContainedInStructure", []) or []:
            parent = getattr(getattr(relation, "RelatingStructure", None), "GlobalId", None)
        name = getattr(entity, "Name", None) or gid
        placement, orientation = {}, "UNKNOWN"
        try:
            import ifcopenshell.util.placement
            matrix = ifcopenshell.util.placement.get_local_placement(entity.ObjectPlacement)
            placement = {"local_transform": [[round(float(v), 6) for v in row] for row in matrix.tolist()], "x": round(float(matrix[0][3]), 6), "y": round(float(matrix[1][3]), 6), "z": round(float(matrix[2][3]), 6)}
            azimuth = (degrees(atan2(float(matrix[1][0]), float(matrix[0][0]))) + 360) % 360
            placement["azimuth_degrees"] = round(azimuth, 2)
            orientation = ("east" if 45 <= azimuth < 135 else "south" if 135 <= azimuth < 225 else "west" if 225 <= azimuth < 315 else "north")
        except Exception:
            pass
        entities.append({"id": gid, "kind": SUPPORTED[kind], "label": str(name), "parent_id": parent, "attributes": {"ifc_class": kind, "ifc_global_id": gid, "expected_status": "PLANNED", "placement": placement, "geometry_status": "UNAVAILABLE"}, "geometry": None, "orientation": orientation})
    ids = {item["id"] for item in entities}
    for item in entities:
        if item["parent_id"] not in ids: item["parent_id"] = None
    if not any(item["kind"] == "building" for item in entities): raise ValueError("IFC import requires a building hierarchy")
    for relation in list(model.by_type("IfcRelAggregates")) + list(model.by_type("IfcRelContainedInSpatialStructure")):
        source = getattr(relation, "RelatingObject", None) or getattr(relation, "RelatingStructure", None)
        targets = getattr(relation, "RelatedObjects", None) or getattr(relation, "RelatedElements", None) or []
        source_id = getattr(source, "GlobalId", None)
        for target in targets:
            target_id = getattr(target, "GlobalId", None)
            if source_id and target_id: relationships.append({"source_spatial_id": source_id, "target_spatial_id": target_id, "relation": "contains", "derivation_method": "EXPLICIT_IFC_RELATION", "ifc_relation_class": relation.is_a()})
    return {"schema_version": "buildmesh-spatial-plan-v1", "source": {"reference": f"ifc:{Path(path).name}", "filename": Path(path).name, "content_sha256": digest, "parser": "IfcOpenShell", "parser_version": getattr(ifcopenshell, "version", "0.8.5"), "ifc_schema": model.schema, "fixture": False}, "entities": entities, "relationships": relationships, "unsupported_entity_classes": sorted(set(unsupported)), "file_size_bytes": len(raw)}
