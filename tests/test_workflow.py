from io import BytesIO
from pathlib import Path

import pytest

from buildmesh.service import BuildMeshService


def test_recommendation_requires_review_and_creates_task_after_approval(tmp_path: Path) -> None:
    service = BuildMeshService(tmp_path / "test.db")
    project = service.create_project("Test project", "Bengaluru")
    original_task = service.create_task(project["id"], "Foundation F-12", {"planned_quantity_m3": 42})
    service.progress_update(project["id"], original_task["id"], 85, 42, 35.5, "worker@example.com")
    service.weather_context(project["id"], "test-weather", 0.8, 24)

    outcome = service.openmesh.run(project["id"])
    assert outcome["human_approval_required"] is True
    assert len(outcome["recommendations"]) == 1
    recommendation = outcome["recommendations"][0]
    assert recommendation["status"] == "pending_review"

    reviewed = service.approve(recommendation["id"], "engineer@example.com", "approved", "Schedule checked")
    assert reviewed["status"] == "approved"
    assert reviewed["created_task"]["attributes"]["origin_recommendation_id"] == recommendation["id"]
    events = service.store.events(project["id"])
    assert any(event["kind"] == "recommendation_reviewed" for event in events)


def test_progress_conflict_becomes_a_review_recommendation(tmp_path: Path) -> None:
    service = BuildMeshService(tmp_path / "test.db")
    project = service.create_project("Test project")
    service.progress_update(project["id"], "F-12", 85, 42, 10, "worker@example.com")
    outcome = service.openmesh.run(project["id"])
    assert len(outcome["recommendations"]) == 1
    assert outcome["recommendations"][0]["severity"] == "medium"


def test_progress_evidence_is_linked_to_its_project_task(tmp_path: Path) -> None:
    service = BuildMeshService(tmp_path / "test.db")
    project = service.create_project("Task linkage")
    task = service.create_task(project["id"], "Foundation F-12", {"planned_quantity_m3": 42})

    progress = service.progress_update(project["id"], task["id"], 40, 42, 16.8, "worker@example.com")

    graph = service.store.graph(project["id"])
    evidence_node = next(node for node in graph["nodes"] if node["attributes"].get("evidence_id") == progress["id"])
    assert any(edge["source_id"] == evidence_node["id"] and edge["target_id"] == task["id"] and edge["relation"] == "reports_on" for edge in graph["edges"])


def test_approved_recommendation_is_not_recreated_from_same_evidence(tmp_path: Path) -> None:
    service = BuildMeshService(tmp_path / "test.db")
    project = service.create_project("Recommendation memory")
    service.weather_context(project["id"], "test-weather", 0.8, 24)
    recommendation = service.openmesh.run(project["id"])["recommendations"][0]

    service.approve(recommendation["id"], "engineer@example.com", "approved")

    assert service.openmesh.run(project["id"])["recommendations"] == []


def test_schedule_variance_requires_a_review_with_task_scoped_evidence(tmp_path: Path) -> None:
    service = BuildMeshService(tmp_path / "test.db")
    project = service.create_project("Schedule intelligence")
    task = service.create_task(project["id"], "Foundation F-12", {"planned_quantity_m3": 42})
    progress = service.progress_update(project["id"], task["id"], 50, 42, 21, "worker@example.com")
    schedule = service.schedule_context(project["id"], task["id"], 80, 5, "planner:weekly", "foundation")

    outcome = service.openmesh.run(project["id"])

    recommendation = next(item for item in outcome["recommendations"] if item["title"] == "Review schedule variance before next work window")
    assert recommendation["severity"] == "high"
    assert set(recommendation["evidence_ids"]) == {progress["id"], schedule["id"]}
    assert "schedule-agent" in {result["agent"] for result in outcome["agent_results"]}


def test_schedule_uses_the_latest_task_progress_update(tmp_path: Path) -> None:
    service = BuildMeshService(tmp_path / "test.db")
    project = service.create_project("Current schedule state")
    task = service.create_task(project["id"], "Foundation F-12", {})
    service.progress_update(project["id"], task["id"], 30, 42, 12.6, "worker@example.com")
    service.progress_update(project["id"], task["id"], 82, 42, 34.44, "worker@example.com")
    service.schedule_context(project["id"], task["id"], 90, 4, "planner:weekly")

    outcome = service.openmesh.run(project["id"])

    assert not any(item["title"] == "Review schedule variance before next work window" for item in outcome["recommendations"])


def test_material_use_variance_requires_manual_reconciliation(tmp_path: Path) -> None:
    service = BuildMeshService(tmp_path / "test.db")
    project = service.create_project("Material intelligence")
    task = service.create_task(project["id"], "Foundation F-12", {"planned_material_units": 100})
    progress = service.progress_update(project["id"], task["id"], 50, 42, 21, "worker@example.com", material_units=82)

    outcome = service.openmesh.run(project["id"])

    recommendation = next(item for item in outcome["recommendations"] if item["title"] == "Verify material use against reported progress")
    assert recommendation["severity"] == "medium"
    assert recommendation["evidence_ids"] == [progress["id"]]
    assert "material-agent" in {result["agent"] for result in outcome["agent_results"]}


def test_started_task_with_unfinished_prerequisite_requires_review(tmp_path: Path) -> None:
    service = BuildMeshService(tmp_path / "test.db")
    project = service.create_project("Dependency intelligence")
    excavation = service.create_task(project["id"], "Excavation", {})
    waterproofing = service.create_task(project["id"], "Waterproofing", {})
    service.add_task_dependency(project["id"], waterproofing["id"], excavation["id"])
    service.set_task_status(project["id"], waterproofing["id"], "in_progress", "engineer@example.com", "Crew started setup")

    outcome = service.openmesh.run(project["id"])

    recommendation = next(item for item in outcome["recommendations"] if item["title"] == "Verify unfinished prerequisite before continuing work")
    assert recommendation["severity"] == "high"
    assert len(recommendation["evidence_ids"]) == 2
    assert "dependency-agent" in {result["agent"] for result in outcome["agent_results"]}


def test_dependency_cycles_and_reopening_completed_tasks_are_rejected(tmp_path: Path) -> None:
    service = BuildMeshService(tmp_path / "test.db")
    project = service.create_project("Dependency validation")
    first = service.create_task(project["id"], "First", {})
    second = service.create_task(project["id"], "Second", {})
    service.add_task_dependency(project["id"], second["id"], first["id"])

    try:
        service.add_task_dependency(project["id"], first["id"], second["id"])
    except ValueError as error:
        assert "cycle" in str(error)
    else:
        raise AssertionError("cyclic task dependencies must be rejected")

    service.set_task_status(project["id"], first["id"], "completed", "engineer@example.com")
    try:
        service.set_task_status(project["id"], first["id"], "open", "engineer@example.com")
    except ValueError as error:
        assert "cannot move" in str(error)
    else:
        raise AssertionError("completed tasks must not be reopened")


def test_approval_rolls_back_if_approved_action_provenance_cannot_be_written(tmp_path: Path) -> None:
    service = BuildMeshService(tmp_path / "test.db")
    project = service.create_project("Atomic approval")
    service.weather_context(project["id"], "test-weather", 0.8, 24)
    recommendation = service.openmesh.run(project["id"])["recommendations"][0]
    with service.store.connection() as con:
        con.execute("""
            CREATE TRIGGER reject_approved_action
            BEFORE INSERT ON graph_edges
            WHEN NEW.relation = 'approved_action'
            BEGIN SELECT RAISE(ABORT, 'test approved-action failure'); END;
        """)

    with pytest.raises(Exception, match="test approved-action failure"):
        service.approve(recommendation["id"], "engineer@example.com", "approved")

    assert service.store.get_recommendation(recommendation["id"])["status"] == "pending_review"
    assert not any(event["kind"] == "recommendation_reviewed" for event in service.store.events(project["id"]))
    graph = service.store.graph(project["id"])
    assert not any(edge["relation"] == "approved_action" for edge in graph["edges"])
    with service.store.connection() as con:
        assert con.execute("SELECT COUNT(*) FROM recommendation_materializations").fetchone()[0] == 0


def test_reviewed_recommendation_graph_node_matches_persisted_decision(tmp_path: Path) -> None:
    service = BuildMeshService(tmp_path / "test.db")
    project = service.create_project("Review provenance")
    service.weather_context(project["id"], "test-weather", 0.8, 24)
    recommendation = service.openmesh.run(project["id"])["recommendations"][0]

    service.approve(recommendation["id"], "engineer@example.com", "rejected", "Not applicable")

    graph = service.store.graph(project["id"])
    node = next(node for node in graph["nodes"] if node["attributes"].get("recommendation_id") == recommendation["id"])
    assert node["attributes"]["status"] == "rejected"
    assert node["attributes"]["reviewer"] == "engineer@example.com"


def test_verified_plan_prerequisite_requires_review_when_dependent_work_starts(tmp_path: Path) -> None:
    service = BuildMeshService(tmp_path / "test.db", tmp_path / "assets")
    project = service.create_project("Plan constraints")
    excavation = service.create_task(project["id"], "Excavation", {})
    waterproofing = service.create_task(project["id"], "Waterproofing", {})
    document = service.ingest_document(project["id"], BytesIO(b"Excavation must be completed before waterproofing begins."), "method.txt", "text/plain")
    prerequisite = service.record_plan_prerequisite(project["id"], document["id"], excavation["id"], waterproofing["id"], "Excavation must be completed before waterproofing begins.")
    service.set_task_status(project["id"], waterproofing["id"], "in_progress", "engineer@example.com")

    outcome = service.openmesh.run(project["id"])

    recommendation = next(item for item in outcome["recommendations"] if item["title"] == "Verify documented prerequisite before continuing work")
    assert prerequisite["id"] in recommendation["evidence_ids"]
    assert "plan-constraint-agent" in {result["agent"] for result in outcome["agent_results"]}
    graph = service.store.graph(project["id"])
    assert any(edge["relation"] == "planned_before" and edge["source_id"] == excavation["id"] and edge["target_id"] == waterproofing["id"] for edge in graph["edges"])


def test_completed_plan_predecessor_suppresses_constraint_recommendation(tmp_path: Path) -> None:
    service = BuildMeshService(tmp_path / "test.db", tmp_path / "assets")
    project = service.create_project("Completed plan constraint")
    excavation = service.create_task(project["id"], "Excavation", {})
    waterproofing = service.create_task(project["id"], "Waterproofing", {})
    document = service.ingest_document(project["id"], BytesIO(b"Excavation before waterproofing."), "method.txt", "text/plain")
    service.record_plan_prerequisite(project["id"], document["id"], excavation["id"], waterproofing["id"], "Excavation before waterproofing.")
    service.set_task_status(project["id"], excavation["id"], "completed", "engineer@example.com")
    service.set_task_status(project["id"], waterproofing["id"], "in_progress", "engineer@example.com")

    outcome = service.openmesh.run(project["id"])

    assert not any(item["title"] == "Verify documented prerequisite before continuing work" for item in outcome["recommendations"])


def test_project_members_assign_tasks_and_resolve_reviewer_candidates(tmp_path: Path) -> None:
    service = BuildMeshService(tmp_path / "test.db")
    project = service.create_project("Project accountability")
    engineer = service.add_project_member(project["id"], "Asha Engineer", "asha@example.com", ["site_engineer", "project_manager"])
    task = service.create_task(project["id"], "Foundation F-12", {})
    assignment = service.assign_task(project["id"], task["id"], engineer["id"], "manager@example.com")
    service.weather_context(project["id"], "test-weather", 0.8, 24)
    recommendation = service.openmesh.run(project["id"])["recommendations"][0]

    assert assignment["relation"] == "assigned_to"
    assert service.recommendation_reviewer_candidates(recommendation["id"]) == [{"id": engineer["id"], "name": "Asha Engineer", "email": "asha@example.com", "roles": ["project_manager", "site_engineer"]}]


def test_project_member_email_is_unique_per_project(tmp_path: Path) -> None:
    service = BuildMeshService(tmp_path / "test.db")
    project = service.create_project("Member validation")
    service.add_project_member(project["id"], "Asha Engineer", "asha@example.com", ["site_engineer"])

    with pytest.raises(ValueError, match="already exists"):
        service.add_project_member(project["id"], "Other Asha", "ASHA@example.com", ["project_manager"])


def test_approval_assigns_derived_task_when_exactly_one_role_match_exists(tmp_path: Path) -> None:
    service = BuildMeshService(tmp_path / "test.db")
    project = service.create_project("Approval routing")
    engineer = service.add_project_member(project["id"], "Asha Engineer", "asha@example.com", ["site_engineer"])
    service.weather_context(project["id"], "test-weather", 0.8, 24)
    recommendation = service.openmesh.run(project["id"])["recommendations"][0]

    reviewed = service.approve(recommendation["id"], "reviewer@example.com", "approved")

    assert reviewed["created_task"]["attributes"]["assigned_member_id"] == engineer["id"]
    graph = service.store.graph(project["id"])
    assert any(edge["source_id"] == reviewed["created_task"]["id"] and edge["target_id"] == engineer["id"] and edge["relation"] == "assigned_to" for edge in graph["edges"])
