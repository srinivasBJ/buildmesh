from io import BytesIO
from pathlib import Path

import pytest

from buildmesh.service import BuildMeshService
from buildmesh.inference import parse_observations
from buildmesh.agents import AgentResult


def _project(tmp_path: Path):
    service = BuildMeshService(tmp_path / "ops.db")
    project = service.create_project("Operations")
    task = service.create_task(project["id"], "Excavation", {"planned_material_units": 100})
    service.set_task_status(project["id"], task["id"], "in_progress", "fixture:operator")
    return service, project, task


def test_ops_001_normal_operation_has_specialists_and_report(tmp_path: Path):
    service, project, _ = _project(tmp_path)
    result = service.openmesh.run(project["id"])
    names = {item["agent"] for item in result["agent_results"]}
    assert {"schedule-agent", "material-agent", "risk-agent", "escalation-agent", "reporting-agent"} <= names


def test_ops_002_delayed_task_recovery_is_four_unselected_options(tmp_path: Path):
    service, project, task = _project(tmp_path)
    result = service.recovery_options(project["id"], task["id"])
    assert {item["id"] for item in result["options"]} == {"RESEQUENCE", "SHIFT_WINDOW", "COMPLETE_PREREQUISITE", "MITIGATE_AND_REVIEW"}
    assert result["selected"] is None and all(item["uncertainty"] for item in result["options"])


def test_ops_003_dependency_is_retained_in_recovery(tmp_path: Path):
    service, project, task = _project(tmp_path)
    prerequisite = service.create_task(project["id"], "Drainage", {})
    service.add_task_dependency(project["id"], task["id"], prerequisite["id"])
    assert prerequisite["id"] in service.recovery_options(project["id"], task["id"])["options"][2]["dependencies"]


def test_ops_004_material_variance_creates_evidence_backed_review(tmp_path: Path):
    service, project, task = _project(tmp_path)
    evidence = service.progress_update(project["id"], task["id"], 80, 100, 80, "fixture:worker", material_units=55)
    result = service.openmesh.run(project["id"])
    recommendation = next(item for item in result["recommendations"] if "material" in item["title"].lower())
    assert evidence["id"] in recommendation["evidence_ids"] and recommendation["status"] == "pending_review"


def test_ops_005_design_deviation_uses_real_reconciliation(tmp_path: Path):
    fixture = Path(__file__).parent / "fixtures" / "rich-apartment.ifc"
    service = BuildMeshService(tmp_path / "design.db"); project = service.create_project("Design")
    imported = service.import_ifc(project["id"], fixture); planned = next(n for n in service.store.graph(project["id"])["nodes"] if n["attributes"].get("ifc_global_id") == "C$WinLiving")
    root = service.store.project_root(project["id"]); zone = service.create_twin_entity(project["id"], root["id"], "zone", "Living")
    observed = service.create_twin_entity(project["id"], zone["id"], "component", "Observed", {"ifc_class": "IfcWindow", "placement": {"x": 3, "y": 1}})
    source = service.weather_context(project["id"], "fixture", .1, 1); snap = service.create_twin_snapshot(project["id"], "S", "2026-09-22T10:00:00Z", [source["id"]], [{"component_id": observed["id"], "observed_state": "IN_PROGRESS", "confidence": .9, "epistemic_state": "VERIFIED", "zone_id": zone["id"], "fixture": True}])
    service.resolve_spatial_match(project["id"], planned["id"], snap["id"], tolerance=2)
    assert service.design_reality_analyze(project["id"], planned["id"], snap["id"], tolerance=.15)["state"] == "SPATIAL_DEVIATION"


def test_ops_006_environmental_risk_is_reviewable(tmp_path: Path):
    service, project, _ = _project(tmp_path); service.weather_context(project["id"], "fixture", .9, 2)
    assert service.openmesh.run(project["id"])["recommendations"]


def test_ops_007_conflicting_evidence_is_not_silently_resolved(tmp_path: Path):
    service, project, _ = _project(tmp_path); service.progress_update(project["id"], "task-ref", 80, 100, 10, "fixture")
    assert service.openmesh.run(project["id"])["recommendations"]


def test_ops_008_escalation_is_persisted_and_bounded(tmp_path: Path):
    service, project, _ = _project(tmp_path); service.weather_context(project["id"], "fixture", .9, 2)
    for _ in range(3): service.openmesh.run(project["id"])
    escalations = service.store.escalations(project["id"]); events = [event for event in service.store.events(project["id"]) if event["kind"] == "escalation_recorded"]
    assert len(escalations) == len(events) == 1 and escalations[0]["level"] in {"REVIEW", "ESCALATE"} and escalations[0]["evidence_ids"]


def test_ops_009_notification_is_persisted_once(tmp_path: Path):
    service, project, _ = _project(tmp_path); service.weather_context(project["id"], "fixture", .9, 2); recommendation = service.openmesh.run(project["id"])["recommendations"][0]
    service.notify_reviewer(recommendation["id"], "fixture:site-engineer"); retry = service.notify_reviewer(recommendation["id"], "fixture:site-engineer")
    events = [event for event in service.store.events(project["id"]) if event["kind"] == "review_notification"]
    assert retry["status"] == "idempotent" and len(events) == 1 and events[0]["payload"]["evidence_ids"] == recommendation["evidence_ids"]


def test_ops_010_duplicate_approval_does_not_duplicate_action(tmp_path: Path):
    service, project, _ = _project(tmp_path); service.weather_context(project["id"], "fixture", .9, 2); recommendation = service.openmesh.run(project["id"])["recommendations"][0]
    service.approve(recommendation["id"], "fixture:reviewer", "approved")
    with pytest.raises(ValueError): service.approve(recommendation["id"], "fixture:reviewer", "approved")
    assert len(service.store.graph(project["id"])["edges"]) > 0


def test_ops_011_malformed_agent_input_is_rejected_without_state(tmp_path: Path):
    with pytest.raises(ValueError): parse_observations({"observations": [{"label": "worker", "confidence": .9, "unknown": True}]})


def test_ops_012_closed_loop_has_approval_action_and_second_reasoning(tmp_path: Path):
    service, project, _ = _project(tmp_path); service.weather_context(project["id"], "fixture", .9, 2); recommendation = service.openmesh.run(project["id"])["recommendations"][0]
    approved = service.approve(recommendation["id"], "fixture:reviewer", "approved"); service.notify_reviewer(recommendation["id"], "fixture:site-engineer"); second = service.openmesh.run(project["id"])
    assert approved["created_task"]["id"] and second["agent_results"] and any(event["kind"] == "task_created_from_approval" for event in service.store.events(project["id"]))


def test_risk_agent_consumes_persisted_design_reality_deviation(tmp_path: Path):
    fixture = Path(__file__).parent / "fixtures" / "rich-apartment.ifc"
    service = BuildMeshService(tmp_path / "risk-design.db"); project = service.create_project("Design risk")
    service.import_ifc(project["id"], fixture); planned = next(n for n in service.store.graph(project["id"])["nodes"] if n["attributes"].get("ifc_global_id") == "C$WinLiving")
    root = service.store.project_root(project["id"]); zone = service.create_twin_entity(project["id"], root["id"], "zone", "Living")
    observed = service.create_twin_entity(project["id"], zone["id"], "component", "Observed", {"ifc_class": "IfcWindow", "placement": {"x": 3, "y": 1}})
    source = service.weather_context(project["id"], "fixture", .1, 1); snapshot = service.create_twin_snapshot(project["id"], "S", "2026-09-22T10:00:00Z", [source["id"]], [{"component_id": observed["id"], "observed_state": "IN_PROGRESS", "confidence": .9, "epistemic_state": "VERIFIED", "zone_id": zone["id"], "fixture": True}])
    service.resolve_spatial_match(project["id"], planned["id"], snapshot["id"], tolerance=2); service.design_reality_analyze(project["id"], planned["id"], snapshot["id"], tolerance=.15)
    risks = next(item for item in service.openmesh.run(project["id"])["agent_results"] if item["agent"] == "risk-agent")["findings"]
    assert any(item["type"] == "design_reality_risk" and item["affected_scope"] == planned["id"] for item in risks)


@pytest.mark.parametrize("finding", [
    {"type": "schedule_progress_variance"},
    {"type": "schedule_progress_variance", "severity": "medium", "task_ref": "x", "planned_percent": 80, "reported_percent": 50, "variance_percent": 30, "days_remaining": 2, "evidence_id": "missing", "evidence_ids": ["missing"]},
    {"type": "schedule_progress_variance", "severity": "medium", "task_ref": "x", "planned_percent": "80", "reported_percent": 50, "variance_percent": 30, "days_remaining": 2, "evidence_id": "missing", "evidence_ids": ["missing"]},
    {"type": "schedule_progress_variance", "severity": "invalid", "task_ref": "x", "planned_percent": 80, "reported_percent": 50, "variance_percent": 30, "days_remaining": 2, "evidence_id": "missing", "evidence_ids": ["missing"], "unknown": True},
])
def test_operational_schema_rejects_invalid_schedule_result_before_recommendation(tmp_path: Path, finding: dict):
    service, project, _ = _project(tmp_path); before = len(service.store.recommendations(project["id"]))
    service.openmesh.schedule_agent.run = lambda *_: AgentResult("schedule-agent", [finding], [])
    with pytest.raises(ValueError): service.openmesh.run(project["id"])
    assert len(service.store.recommendations(project["id"])) == before


@pytest.mark.parametrize(("agent_name", "finding"), [
    ("material_agent", {"type": "material_progress_variance", "severity": "high", "task_ref": "x", "reported_percent": "80", "planned_material_units": 100, "actual_material_units": 90, "expected_material_units": 80, "variance_units": 10, "evidence_id": "missing"}),
    ("risk_agent", {"type": "weather_window_risk", "severity": "critical", "evidence_id": "missing", "unknown": True}),
    ("escalation_agent", {"type": "escalation", "recommendation_id": "rec_missing", "level": "BLOCKED", "evidence_ids": [], "requires_human_review": True}),
    ("reporting_agent", {"type": "daily_project_brief", "project_id": 42, "progress": 0, "blocked": 0, "reviews_required": 0, "recommended_actions": 0, "evidence_ids": []}),
])
def test_each_specialist_schema_rejects_invalid_output_before_recommendations(tmp_path: Path, agent_name: str, finding: dict):
    service, project, _ = _project(tmp_path)
    agent = getattr(service.openmesh, agent_name)
    agent.run = lambda *_: AgentResult(agent.name, [finding], [])
    with pytest.raises(ValueError):
        service.openmesh.run(project["id"])
    assert service.store.recommendations(project["id"]) == []


def test_recommendation_schema_rejects_unsupported_action_before_persistence(tmp_path: Path):
    service, project, _ = _project(tmp_path)
    class InvalidRecommendation:
        def data(self):
            return {"id": "rec_bad", "project_id": project["id"], "title": "bad", "rationale": "bad", "severity": "high", "evidence_ids": ["missing"], "proposed_task": {"title": "bad", "action": "execute", "requires_human_confirmation": False}, "status": "pending_review", "created_at": "now"}
    service.openmesh.recommendation_agent.run = lambda *args: [args[-1](InvalidRecommendation())]
    with pytest.raises(ValueError, match="Recommendation|recommendation"):
        service.openmesh.run(project["id"])
    assert service.store.recommendations(project["id"]) == []


def test_persisted_operational_timeline_links_observation_to_resulting_action(tmp_path: Path):
    service, project, _ = _project(tmp_path)
    observation = service.weather_context(project["id"], "fixture", .9, 2)
    recommendation = service.openmesh.run(project["id"])["recommendations"][0]
    approved = service.approve(recommendation["id"], "fixture:reviewer", "approved")
    service.notify_reviewer(recommendation["id"], "fixture:site-engineer")
    timeline = service.operational_timeline(recommendation["id"])
    event_by_kind = {event["kind"]: event for event in timeline["events"]}
    assert observation["id"] in timeline["evidence_ids"]
    assert timeline["observations"][0]["id"] == observation["id"]
    assert any(event["kind"] == "evidence_recorded" for event in timeline["source_events"])
    assert {"risk-agent", "recommendation-agent"} <= {run["agent_name"] for run in timeline["agent_runs"]}
    assert {"recommendation_created", "recommendation_reviewed", "task_created_from_approval", "review_notification"} <= set(event_by_kind)
    assert timeline["action_task_id"] == approved["created_task"]["id"] == event_by_kind["task_created_from_approval"]["subject_id"]
    assert event_by_kind["review_notification"]["payload"]["action_task_id"] == timeline["action_task_id"]
    assert timeline["state_evidence"][0]["payload"]["task_id"] == timeline["action_task_id"]
    graph = service.store.graph(project["id"])
    assert any(edge["relation"] == "states" and edge["target_id"] == timeline["action_task_id"] for edge in graph["edges"])


def _trusted_counts(service: BuildMeshService, project_id: str) -> dict[str, int]:
    graph = service.store.graph(project_id)
    with service.store.connection() as con:
        materializations = con.execute("SELECT COUNT(*) FROM recommendation_materializations").fetchone()[0]
    events = service.store.events(project_id)
    return {"recommendations": len(service.store.recommendations(project_id)), "escalations": len(service.store.escalations(project_id)), "tasks": sum(node["kind"] == "task" for node in graph["nodes"]), "edges": len(graph["edges"]), "events": len(events), "provenance": sum(edge["relation"] == "supports" for edge in graph["edges"]), "materializations": materializations}


@pytest.mark.parametrize(("trigger_name", "when"), [
    ("reject_recommendation", "NEW.id LIKE 'rec_%'"),
    ("reject_recommendation_provenance", "NEW.relation = 'supports'"),
    ("reject_recommendation_audit", "NEW.kind = 'recommendation_created'"),
])
def test_recommendation_transaction_rolls_back_all_trusted_state(tmp_path: Path, trigger_name: str, when: str):
    service, project, _ = _project(tmp_path)
    service.weather_context(project["id"], "fixture", .9, 2)
    before = _trusted_counts(service, project["id"])
    table = "recommendations" if trigger_name == "reject_recommendation" else "graph_edges" if "provenance" in trigger_name else "events"
    with service.store.connection() as con:
        con.execute(f"CREATE TRIGGER {trigger_name} BEFORE INSERT ON {table} WHEN {when} BEGIN SELECT RAISE(ABORT, 'injected failure'); END;")
    with pytest.raises(Exception, match="injected failure"):
        service.openmesh.run(project["id"])
    assert _trusted_counts(service, project["id"]) == before


def test_notification_audit_failure_leaves_no_notification_record_and_retry_is_safe(tmp_path: Path):
    service, project, _ = _project(tmp_path)
    service.weather_context(project["id"], "fixture", .9, 2)
    recommendation = service.openmesh.run(project["id"])["recommendations"][0]
    with service.store.connection() as con:
        con.execute("CREATE TRIGGER reject_notification BEFORE INSERT ON events WHEN NEW.kind = 'review_notification' BEGIN SELECT RAISE(ABORT, 'injected notification failure'); END;")
    with pytest.raises(Exception, match="injected notification failure"):
        service.notify_reviewer(recommendation["id"], "fixture:site-engineer")
    assert not any(event["kind"] == "review_notification" for event in service.store.events(project["id"]))
    with service.store.connection() as con:
        con.execute("DROP TRIGGER reject_notification")
    assert service.notify_reviewer(recommendation["id"], "fixture:site-engineer")["status"] == "not_configured"


def test_action_materialization_failure_rolls_back_review_state_and_retry_is_safe(tmp_path: Path):
    service, project, _ = _project(tmp_path)
    service.weather_context(project["id"], "fixture", .9, 2)
    recommendation = service.openmesh.run(project["id"])["recommendations"][0]
    before = _trusted_counts(service, project["id"])
    with service.store.connection() as con:
        con.execute("CREATE TRIGGER reject_action_task BEFORE INSERT ON graph_nodes WHEN NEW.kind = 'task' BEGIN SELECT RAISE(ABORT, 'injected action failure'); END;")
    with pytest.raises(Exception, match="injected action failure"):
        service.approve(recommendation["id"], "fixture:reviewer", "approved")
    assert _trusted_counts(service, project["id"]) == before
    assert service.store.get_recommendation(recommendation["id"])["status"] == "pending_review"
    with service.store.connection() as con:
        con.execute("DROP TRIGGER reject_action_task")
    assert service.approve(recommendation["id"], "fixture:reviewer", "approved")["created_task"]


def test_final_orchestration_failure_leaves_trusted_state_unchanged_and_retry_is_safe(tmp_path: Path):
    service, project, _ = _project(tmp_path)
    service.weather_context(project["id"], "fixture", .9, 2)
    before = _trusted_counts(service, project["id"])
    service.openmesh.reporting_agent.run = lambda *_: (_ for _ in ()).throw(RuntimeError("injected final failure"))
    with pytest.raises(RuntimeError, match="injected final failure"):
        service.openmesh.run(project["id"])
    assert _trusted_counts(service, project["id"]) == before
    from buildmesh.agents import ReportingAgent
    service.openmesh.reporting_agent = ReportingAgent()
    assert service.openmesh.run(project["id"])["recommendations"]
    assert len(service.store.escalations(project["id"])) == 1
