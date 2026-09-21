from pathlib import Path
import pytest
from buildmesh.service import BuildMeshService


def plan():
    return {"schema_version": "buildmesh-spatial-plan-v1", "source": {"reference": "fixture:apartment", "fixture": True, "confidence": .8}, "entities": [
        {"id": "B1", "kind": "building", "label": "Building A", "parent_id": None, "attributes": {}, "geometry": None, "orientation": "north"},
        {"id": "L1", "kind": "level", "label": "Floor 1", "parent_id": "B1", "attributes": {}, "geometry": None, "orientation": "UNKNOWN"},
        {"id": "R1", "kind": "room", "label": "Bedroom 2", "parent_id": "L1", "attributes": {"room_type": "bedroom", "area_m2": 12}, "geometry": {"type": "bounding_box", "coordinates": [0, 0, 3, 4], "coordinate_system": "local", "dimensions": {"width_m": 3, "length_m": 4}}, "orientation": "east"},
        {"id": "W1", "kind": "opening", "label": "Window W-204", "parent_id": "R1", "attributes": {"expected_status": "PLANNED"}, "geometry": None, "orientation": "east"},
        {"id": "D1", "kind": "component", "label": "Door D-101", "parent_id": "R1", "attributes": {"expected_status": "PLANNED"}, "geometry": None, "orientation": "UNKNOWN"},
    ]}


def test_spatial_import_hierarchy_provenance_and_idempotence(tmp_path: Path):
    service = BuildMeshService(tmp_path / "spatial.db"); project = service.create_project("Spatial")
    imported = service.import_spatial_plan(project["id"], plan())
    assert imported["status"] == "imported" and service.import_spatial_plan(project["id"], plan())["status"] == "idempotent"
    assert service.spatial_status(project["id"])["count"] == 5
    with pytest.raises(ValueError): service.import_spatial_plan(project["id"], {"bad": True})


def test_spatial_diff_document_and_environment_links_are_explicit(tmp_path: Path):
    service = BuildMeshService(tmp_path / "spatial.db"); project = service.create_project("Spatial")
    ids = service.import_spatial_plan(project["id"], plan())["entity_ids"]; room, planned = ids[2], ids[3]
    root = service.store.project_root(project["id"]); zone = service.create_twin_entity(project["id"], root["id"], "zone", "Bedroom"); observed = service.create_twin_entity(project["id"], zone["id"], "component", "Window W-204")
    task = service.create_task(project["id"], "Window install", {}); document = service.ingest_document(project["id"], __import__("io").BytesIO(b"W-204 requires install"), "plan.txt", "text/plain")
    service.link_document_spatial(project["id"], document["id"], planned, task["id"]); service.link_planned_observed(project["id"], planned, observed["id"])
    snapshot = service.create_twin_snapshot(project["id"], "A", "2026-09-08T10:00:00Z", [document["id"]], [{"component_id": observed["id"], "observed_state": "IN_PROGRESS", "confidence": .8, "epistemic_state": "INFERRED", "zone_id": zone["id"], "fixture": True}])
    diff = service.spatial_diff(project["id"], ids[0], snapshot["id"])
    assert any(item["state"] == "MATCHED" for item in diff["differences"]) and any(item["state"] == "NOT_OBSERVED" for item in diff["differences"])
    solar = service.environmental_solar(project["id"], 12.97, 77.59, "2026-09-08"); service.link_environment_spatial(project["id"], solar["id"], room)
    assert service.spatial_analyze(project["id"], room)["orientation"] == "east"

def test_bbox_semantics_are_conservative_and_queryable(tmp_path: Path):
    service = BuildMeshService(tmp_path / "spatial.db"); project = service.create_project("Semantics")
    payload = plan(); payload["entities"][2]["geometry"] = {"type": "bounding_box", "coordinates": [0, 0, 10, 10], "coordinate_system": "local", "dimensions": {}}
    payload["entities"][3]["geometry"] = {"type": "bounding_box", "coordinates": [1, 1, 2, 2], "coordinate_system": "local", "dimensions": {}}
    ids = service.import_spatial_plan(project["id"], payload)["entity_ids"]
    semantic = service.spatial_semantics(project["id"])
    assert semantic["geometry_normalized_entity_count"] == 2 and semantic["relationships_created"] >= 1
    assert service.spatial_query(project["id"], "components_in_room", ids[2])["components"]
    assert service.spatial_query(project["id"], "planned_not_observed")["components"]
