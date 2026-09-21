from pathlib import Path

import pytest

from buildmesh.service import BuildMeshService


def _hierarchy(service: BuildMeshService, project_id: str):
    root = service.store.project_root(project_id)
    site = service.create_twin_entity(project_id, root["id"], "site", "Site A")
    building = service.create_twin_entity(project_id, site["id"], "building", "Building A")
    floor = service.create_twin_entity(project_id, building["id"], "floor", "Floor 1")
    zone = service.create_twin_entity(project_id, floor["id"], "zone", "Zone 1")
    frame = service.create_twin_entity(project_id, zone["id"], "component", "Structural frame", {"planned_state": "COMPLETE"})
    walls = service.create_twin_entity(project_id, zone["id"], "component", "Walls", {"planned_state": "IN_PROGRESS"})
    return floor, zone, frame, walls


def test_twin_snapshots_have_provenance_diff_and_completeness(tmp_path: Path) -> None:
    service = BuildMeshService(tmp_path / "twin.db")
    project = service.create_project("Twin")
    floor, zone, frame, walls = _hierarchy(service, project["id"])
    source = service.weather_context(project["id"], "fixture", 0.2, 12)
    first = service.create_twin_snapshot(project["id"], "Snapshot 1", "2026-09-08T10:00:00Z", [source["id"]], [{"component_id": frame["id"], "observed_state": "IN_PROGRESS", "confidence": 0.8, "epistemic_state": "INFERRED", "zone_id": zone["id"], "fixture": True}])
    second = service.create_twin_snapshot(project["id"], "Snapshot 2", "2026-09-09T10:00:00Z", [source["id"]], [{"component_id": frame["id"], "observed_state": "COMPLETE", "confidence": 0.9, "epistemic_state": "VERIFIED", "zone_id": zone["id"], "fixture": True}, {"component_id": walls["id"], "observed_state": "IN_PROGRESS", "confidence": 0.8, "epistemic_state": "INFERRED", "zone_id": zone["id"], "fixture": True}])
    assert {item["type"] for item in service.twin_diff(project["id"], first["id"], second["id"])} == {"STATE_CHANGED", "COMPONENT_APPEARED"}
    status = service.twin_status(project["id"], floor["id"], second["id"])
    assert status["completion_estimate"] == 0.5 and status["evidence_coverage"] == 1.0


def test_twin_reconciliation_and_validation(tmp_path: Path) -> None:
    service = BuildMeshService(tmp_path / "twin.db")
    project = service.create_project("Twin")
    _, zone, frame, _ = _hierarchy(service, project["id"])
    source = service.weather_context(project["id"], "fixture", 0.2, 12)
    observation = {"component_id": frame["id"], "observed_state": "CONFLICTING", "confidence": 0.6, "epistemic_state": "CONFLICTING", "zone_id": zone["id"], "fixture": True}
    snapshot = service.create_twin_snapshot(project["id"], "Conflict", "2026-09-08T10:00:00Z", [source["id"]], [observation])
    recommendations = service.twin_reconcile(project["id"], snapshot["id"])
    assert len(recommendations) == 1 and service.twin_reconcile(project["id"], snapshot["id"]) == []
    assert service.approve(recommendations[0]["id"], "engineer@example.com", "approved")["created_task"]
    with pytest.raises(ValueError, match="twice"):
        service.create_twin_snapshot(project["id"], "Bad", "2026-09-08T10:00:00Z", [source["id"]], [observation, observation])
