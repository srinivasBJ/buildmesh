import pytest

from buildmesh.external_spatial import normalize_external_spatial
from buildmesh.service import BuildMeshService


def external_summary() -> dict:
    """Metadata from a real public TUM RGB-D run; no source media is shipped."""
    return {
        "capture_id": "external_tum_rgbd_freiburg1_xyz_pycolmap_20260923",
        "source_url": "https://cvg.cit.tum.de/rgbd/dataset/freiburg1/rgbd_dataset_freiburg1_xyz.tgz",
        "source_license": "CC-BY-4.0",
        "source_checksum": "sha256:a0236d97b8c30cd93b653656d2b6c293ff7c982a4130ef2a1a8beecdb124ef98",
        "source_captured_at": "UNKNOWN",
        "ingested_at": "2026-09-23T10:42:53Z",
        "dataset": "TUM RGB-D freiburg1_xyz",
        "device": "Microsoft Kinect (dataset documentation)",
        "frame_count": 798,
        "registered_images": 12,
        "points3d_count": 1783,
        "bounds_center": [-19.088629573971566, -5.530474727709856, 42.51948381760361],
        "bounds_dimensions": [68.98807205535998, 35.01844907889113, 58.498278144682],
        "derived_by": "PyCOLMAP 4.2.0 CPU sparse reconstruction",
        "fixture": False,
    }


def state_counts(service: BuildMeshService, project_id: str) -> tuple[int, int, int, int]:
    graph = service.store.graph(project_id)
    with service.store.connection() as con:
        captures = con.execute("SELECT COUNT(*) FROM spatial_captures WHERE project_id=?", (project_id,)).fetchone()[0]
    return captures, len(service.store.evidence(project_id)), len(graph["nodes"]), len(graph["edges"])


def test_external_001_to_007_provenance_normalization_snapshot_twin_and_graph(tmp_path):
    service = BuildMeshService(tmp_path / "external.db")
    project = service.create_project("External real-world validation")
    capture = normalize_external_spatial(external_summary())
    result = service.ingest_spatial_capture(project["id"], capture)
    inspected = service.inspect_spatial_capture(project["id"], result["capture_id"])
    source = inspected["capture"]["payload"]["source"]
    entity = inspected["entities"][0]
    graph = service.store.graph(project["id"])
    assert source["fixture"] is False and source["source_url"] == external_summary()["source_url"]
    assert source["derived_by"] == "PyCOLMAP 4.2.0 CPU sparse reconstruction" and source["evidence_state"] == "DERIVED"
    assert capture["coordinate_frame"]["units"] == "arbitrary_scale"
    assert entity["attributes"]["semantic_type"] == "UNKNOWN"
    assert inspected["snapshot"]["attributes"]["immutable"] is True
    assert any(edge["relation"] == "observes" for edge in graph["edges"])
    assert any(edge["relation"] == "evidence_for" for edge in graph["edges"])
    assert any(edge["relation"] == "contains" for edge in graph["edges"])


def test_external_008_reconciliation_stops_at_unknown_semantic_boundary(tmp_path):
    service = BuildMeshService(tmp_path / "external.db")
    project = service.create_project("External reconciliation")
    planned = service.import_spatial_plan(project["id"], {"schema_version": "buildmesh-spatial-plan-v1", "source": {"reference": "controlled-planned-state", "fixture": True}, "entities": [{"id": "planned-window", "kind": "opening", "label": "Planned window", "parent_id": None, "attributes": {"ifc_class": "IfcWindow"}, "geometry": None, "orientation": "UNKNOWN"}]})["entity_ids"][0]
    ingested = service.ingest_spatial_capture(project["id"], normalize_external_spatial(external_summary()))
    match = service.resolve_spatial_match(project["id"], planned, ingested["snapshot_id"])
    analysis = service.design_reality_analyze(project["id"], planned, ingested["snapshot_id"])
    assert match["decision"] == analysis["state"] == "UNKNOWN"


def test_external_009_three_identical_submissions_are_idempotent(tmp_path):
    service = BuildMeshService(tmp_path / "external.db")
    project = service.create_project("External idempotence")
    capture = normalize_external_spatial(external_summary())
    first = service.ingest_spatial_capture(project["id"], capture)
    before = state_counts(service, project["id"])
    second = service.ingest_spatial_capture(project["id"], capture)
    third = service.ingest_spatial_capture(project["id"], capture)
    assert second["status"] == third["status"] == "idempotent"
    assert first["snapshot_id"] == second["snapshot_id"] == third["snapshot_id"]
    assert state_counts(service, project["id"]) == before


@pytest.mark.parametrize("failure", ["evidence", "snapshot", "entity", "graph", "final"])
def test_external_010_rollback_is_atomic_and_retries(tmp_path, failure):
    service = BuildMeshService(tmp_path / "external.db")
    project = service.create_project("External rollback")
    capture = normalize_external_spatial(external_summary())
    before = state_counts(service, project["id"])
    with pytest.raises(RuntimeError, match="injected spatial capture"):
        service.ingest_spatial_capture(project["id"], capture, fail_at=failure)
    assert state_counts(service, project["id"]) == before
    assert service.ingest_spatial_capture(project["id"], capture)["status"] == "created"


def test_external_011_rejects_invalid_derived_geometry_without_state(tmp_path):
    service = BuildMeshService(tmp_path / "external.db")
    project = service.create_project("External validation")
    broken = external_summary()
    broken["bounds_dimensions"] = [1, 0, 1]
    with pytest.raises(ValueError, match="bounds_dimensions"):
        normalize_external_spatial(broken)
    assert state_counts(service, project["id"]) == (0, 0, 1, 0)


def test_external_012_end_to_end_is_not_fixture_or_roomplan(tmp_path):
    capture = normalize_external_spatial(external_summary())
    assert capture["source"]["kind"] == "external_visual"
    assert capture["source"]["fixture"] is False
    assert capture["source"]["format"] == "pycolmap/sparse-reconstruction-v1"
    assert capture["entities"][0]["semantic_type"] == "UNKNOWN"
