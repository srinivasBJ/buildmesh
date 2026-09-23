from copy import deepcopy
import json
from pathlib import Path

import pytest

from buildmesh.service import BuildMeshService


def capture(capture_id: str = "capture_fixture_room", captured_at: str = "2026-09-23T10:00:00Z") -> dict:
    """Independently authored fixture; never represents a physical device capture."""
    value = json.loads((Path(__file__).parent / "fixtures" / "spatial-capture-room.json").read_text())
    value["capture_id"] = capture_id
    value["captured_at"] = captured_at
    return value


def manual_roomplan_style_capture() -> dict:
    value = capture("capture_manual_roomplan_style", "2026-09-25T09:30:00Z")
    value["source"] = {"kind": "manual", "device": "manual-roomplan-style-file", "format": "roomplan-style/manual-v1", "fixture": True, "provenance": "synthetic/manual test input"}
    return value


def service_project(tmp_path: Path):
    service = BuildMeshService(tmp_path / "capture.db")
    return service, service.create_project("Capture fixture")


def trusted_counts(service: BuildMeshService, project_id: str) -> tuple[int, int, int, int]:
    graph = service.store.graph(project_id)
    with service.store.connection() as con:
        captures = con.execute("SELECT COUNT(*) FROM spatial_captures WHERE project_id=?", (project_id,)).fetchone()[0]
    return captures, len(graph["nodes"]), len(graph["edges"]), len(service.store.evidence(project_id))


def test_spatial_capture_001_008_009_010_valid_capture_materializes_provenanced_snapshot(tmp_path: Path):
    service, project = service_project(tmp_path)
    result = service.ingest_spatial_capture(project["id"], capture())
    inspected = service.inspect_spatial_capture(project["id"], result["capture_id"])
    assert result["status"] == "created" and len(inspected["entities"]) == 3
    assert inspected["capture"]["payload"]["capture_id"] == result["capture_id"]
    assert inspected["snapshot"]["attributes"]["source_evidence_ids"] == [result["evidence_id"]]
    assert all(node["attributes"]["capture_evidence_id"] == result["evidence_id"] for node in inspected["entities"])
    assert any(edge["relation"] == "evidence_for" for edge in service.store.graph(project["id"])["edges"])


@pytest.mark.parametrize("mutate", [
    lambda value: value.update({"entities": "no"}),
    lambda value: value["source"].pop("device"),
    lambda value: value.update({"unexpected": True}),
    lambda value: value["entities"][0]["geometry"].update({"dimensions": [1, -1, 1]}),
    lambda value: value["entities"][0].update({"parent_scope": "missing"}),
    lambda value: value["entities"][0].update({"evidence_reference": "evidence_missing"}),
])
def test_spatial_capture_002_to_007_rejects_invalid_input_without_state(tmp_path: Path, mutate):
    service, project = service_project(tmp_path); value = capture(); mutate(value)
    before = trusted_counts(service, project["id"])
    with pytest.raises(ValueError):
        service.ingest_spatial_capture(project["id"], value)
    assert trusted_counts(service, project["id"]) == before


def test_spatial_capture_011_preserves_historical_snapshots(tmp_path: Path):
    service, project = service_project(tmp_path)
    first = service.ingest_spatial_capture(project["id"], capture())
    second = service.ingest_spatial_capture(project["id"], capture("capture_fixture_room_2", "2026-09-24T10:00:00Z"))
    assert first["snapshot_id"] != second["snapshot_id"]
    assert service.store.get_node(first["snapshot_id"])["attributes"]["captured_at"] == "2026-09-23T10:00:00+00:00"
    with pytest.raises(ValueError, match="immutable snapshots"):
        service.store.update_node_attributes(first["snapshot_id"], {"captured_at": "changed"})
    assert len([item for item in service.store.evidence(project["id"]) if item["kind"] == "twin_observation" and item["payload"]["snapshot_id"] == first["snapshot_id"]]) == 3


def test_manual_roomplan_style_input_is_fixture_only_contract_coverage(tmp_path: Path):
    service, project = service_project(tmp_path)
    result = service.ingest_spatial_capture(project["id"], manual_roomplan_style_capture())
    assert service.inspect_spatial_capture(project["id"], result["capture_id"])["capture"]["payload"]["source"]["fixture"] is True


def test_entity_can_retain_a_valid_additional_evidence_reference(tmp_path: Path):
    service, project = service_project(tmp_path)
    supplied = service.weather_context(project["id"], "fixture:metadata", .1, 24)
    value = capture(); value["entities"][0]["evidence_reference"] = supplied["id"]
    result = service.ingest_spatial_capture(project["id"], value)
    graph = service.store.graph(project["id"])
    component = next(node for node in graph["nodes"] if node["attributes"].get("source_entity_id") == "window-east-1")
    evidence_nodes = {node["attributes"].get("evidence_id"): node["id"] for node in graph["nodes"] if node["kind"] == "evidence"}
    links = {(edge["source_id"], edge["target_id"]) for edge in graph["edges"] if edge["relation"] == "evidence_for"}
    assert (evidence_nodes[result["evidence_id"]], component["id"]) in links
    assert (evidence_nodes[supplied["id"]], component["id"]) in links


def test_spatial_capture_012_is_persisted_idempotently_across_three_submissions(tmp_path: Path):
    service, project = service_project(tmp_path)
    first = service.ingest_spatial_capture(project["id"], capture())
    before = trusted_counts(service, project["id"])
    second = service.ingest_spatial_capture(project["id"], capture())
    third = service.ingest_spatial_capture(project["id"], capture())
    assert second["status"] == third["status"] == "idempotent"
    assert second["snapshot_id"] == first["snapshot_id"] and trusted_counts(service, project["id"]) == before


def test_spatial_capture_013_feeds_existing_design_reality_resolution(tmp_path: Path):
    service, project = service_project(tmp_path)
    fixture = Path(__file__).parent / "fixtures" / "rich-apartment.ifc"
    service.import_ifc(project["id"], fixture)
    planned = next(node for node in service.store.graph(project["id"])["nodes"] if node["attributes"].get("ifc_global_id") == "C$WinLiving")
    ingested = service.ingest_spatial_capture(project["id"], capture())
    match = service.resolve_spatial_match(project["id"], planned["id"], ingested["snapshot_id"], tolerance=.5)
    result = service.design_reality_analyze(project["id"], planned["id"], ingested["snapshot_id"], tolerance=.15)
    assert match["decision"] == "MATCHED" and result["snapshot_id"] == ingested["snapshot_id"]


@pytest.mark.parametrize("fail_at", ["evidence", "snapshot", "entity", "graph", "final"])
def test_spatial_capture_transaction_rolls_back_every_failure_point_and_retries(tmp_path: Path, fail_at: str):
    service, project = service_project(tmp_path); value = capture(f"capture_{fail_at}")
    before = trusted_counts(service, project["id"])
    with pytest.raises(RuntimeError, match="injected spatial capture"):
        service.ingest_spatial_capture(project["id"], value, fail_at=fail_at)
    assert trusted_counts(service, project["id"]) == before
    assert service.ingest_spatial_capture(project["id"], value)["status"] == "created"
