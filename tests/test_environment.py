from pathlib import Path
from datetime import UTC, datetime

import pytest

from buildmesh.service import BuildMeshService


def _context(service: BuildMeshService, project_id: str, **extra):
    base = {"kind": "weather_forecast", "source": "fixture:weather", "source_type": "fixture", "retrieved_at": "2026-09-08T10:00:00Z", "observed_at": "2026-09-08T10:00:00Z", "latitude": 12.97, "longitude": 77.59, "values": {"rain_probability": 0.8, "precipitation_mm": 8}, "units": {"rain_probability": "probability", "precipitation_mm": "mm"}, "fixture": True, "confidence": 0.8}
    return service.environmental_context(project_id, **{**base, **extra})


def test_environmental_context_has_graph_provenance_and_fixture_discipline(tmp_path: Path) -> None:
    service = BuildMeshService(tmp_path / "environment.db")
    project = service.create_project("Context")
    context = _context(service, project["id"])
    payload = context["payload"]
    assert payload["source_type"] == "fixture" and payload["epistemic_state"] == "VERIFIED"
    assert service.environment_status(project["id"])["fixture_count"] == 1
    graph = service.store.graph(project["id"])
    assert any(edge["target_id"] == context["graph_node_id"] and edge["relation"] == "observed_at" for edge in graph["edges"])
    with pytest.raises(ValueError, match="source_type"):
        _context(service, project["id"], source_type="live")


def test_stale_context_is_explicit_and_multifactor_fusion_requires_review(tmp_path: Path) -> None:
    service = BuildMeshService(tmp_path / "environment.db")
    project = service.create_project("Context")
    drainage = service.create_task(project["id"], "Drainage", {"activity": "excavation"})
    excavation = service.create_task(project["id"], "Foundation excavation", {"activity": "excavation"})
    service.add_task_dependency(project["id"], excavation["id"], drainage["id"])
    service.set_task_status(project["id"], excavation["id"], "in_progress", "engineer@example.com")
    context = _context(service, project["id"])
    plan = service.environment_plan(project["id"], excavation["id"])
    assert plan["environmental_score"]["suitability_score"] == 0
    outcome = service.openmesh.run(project["id"])
    recommendation = next(item for item in outcome["recommendations"] if item["title"] == "Review environmental context and prerequisite before continuing work")
    assert context["id"] in recommendation["evidence_ids"] and recommendation["status"] == "pending_review"
    stale = _context(service, project["id"], retrieved_at="2020-01-01T00:00:00Z")
    assert stale["payload"]["epistemic_state"] == "STALE"


def test_historical_solar_and_work_window_outputs_are_explicit_about_limits(tmp_path: Path) -> None:
    service = BuildMeshService(tmp_path / "environment.db")
    project = service.create_project("Context")
    task = service.create_task(project["id"], "Road work", {"activity": "road_work"})
    _context(service, project["id"])
    historical = service.historical_climate_plan(project["id"], [{"label": "October", "data_period": "2010-2020", "precipitation_tendency": 0.7, "temperature_c": "20-28", "daylight_hours": 11.5, "data_coverage": 0.8, "source": "fixture:historical", "confidence": 0.7}])
    assert historical["evidence"]["payload"]["classification"] == "HISTORICAL_NOT_FORECAST"
    solar = service.environmental_solar(project["id"], 12.97, 77.59, "2026-09-08")
    assert solar["payload"]["kind"] == "solar_context"
    windows = service.candidate_work_windows(project["id"], task["id"], [{"start": "09:00", "end": "12:00"}])
    assert "no predicted time-of-day" in windows["formula"]


def test_live_refresh_conflict_unknown_and_window_specific_traffic(tmp_path: Path) -> None:
    fresh = datetime.now(UTC).isoformat()
    class Weather:
        def forecast(self, latitude, longitude, horizon_hours=48):
            return {"provider": "mock-open-meteo", "retrieved_at": fresh, "latitude": latitude, "longitude": longitude, "rain_probability": 0.1, "peak_at": fresh, "precipitation_total_mm": 0}
    service = BuildMeshService(tmp_path / "environment.db", weather_client=Weather())
    project = service.create_project("Live", metadata={"latitude": 12.97, "longitude": 77.59})
    task = service.create_task(project["id"], "Road work", {"activity": "road_work"})
    live = service.refresh_environment(project["id"])
    observed = service.environmental_context(project["id"], kind="weather_observation", source="manual:rain", source_type="manual", retrieved_at=fresh, observed_at=fresh, latitude=12.97, longitude=77.59, values={"precipitation_mm": 2}, units={"precipitation_mm": "mm"})
    assert service.reconcile_environment(project["id"])[0]["payload"]["epistemic_state"] == "CONFLICTING"
    unknown = service.unknown_environment(project["id"], "soil_context", 12.97, 77.59, "soil unavailable")
    assert unknown["payload"]["epistemic_state"] == "UNKNOWN"
    traffic = []
    for start, end, index in [("08:00", "10:00", .9), ("11:00", "13:00", .1)]:
        traffic.append(service.environmental_context(project["id"], kind="traffic_context", source=f"fixture:{start}", source_type="fixture", retrieved_at="2026-09-08T10:00:00Z", observed_at="2026-09-08T10:00:00Z", latitude=12.97, longitude=77.59, values={"congestion_index": index, "window_start": start, "window_end": end}, units={"congestion_index": "index"}, fixture=True))
    windows = service.candidate_work_windows(project["id"], task["id"], [{"start": "08:00", "end": "10:00"}, {"start": "11:00", "end": "13:00"}])
    assert windows["ranked_windows"][0]["score"] < windows["ranked_windows"][1]["score"]
    assert traffic[0]["id"] in windows["ranked_windows"][0]["evidence_ids"] and traffic[1]["id"] not in windows["ranked_windows"][0]["evidence_ids"]
    assert live["payload"]["source_type"] == "live" and observed["payload"]["source_type"] == "manual"


def test_fusion_cites_as_built_observation_with_environment_and_dependency(tmp_path: Path) -> None:
    service = BuildMeshService(tmp_path / "environment.db")
    project = service.create_project("Twin fusion")
    root = service.store.project_root(project["id"])
    site = service.create_twin_entity(project["id"], root["id"], "site", "Site")
    zone = service.create_twin_entity(project["id"], site["id"], "zone", "Excavation")
    component = service.create_twin_entity(project["id"], zone["id"], "component", "Exposed excavation")
    drainage = service.create_task(project["id"], "Drainage", {})
    task = service.create_task(project["id"], "Foundation excavation", {"activity": "excavation"})
    link = service.link_task_component(project["id"], task["id"], component["id"])
    service.add_task_dependency(project["id"], task["id"], drainage["id"])
    service.set_task_status(project["id"], task["id"], "in_progress", "engineer@example.com")
    source = service.weather_context(project["id"], "fixture", .2, 12)
    snapshot = service.create_twin_snapshot(project["id"], "Observed", "2026-09-08T10:00:00Z", [source["id"]], [{"component_id": component["id"], "observed_state": "IN_PROGRESS", "confidence": .8, "epistemic_state": "INFERRED", "zone_id": zone["id"], "fixture": True}])
    weather = _context(service, project["id"])
    result = service.openmesh.run(project["id"])
    rec = next(item for item in result["recommendations"] if item["title"] == "Review environmental context and prerequisite before continuing work")
    twin_evidence = next(item for item in service.store.evidence(project["id"]) if item["kind"] == "twin_observation")
    assert weather["id"] in rec["evidence_ids"] and twin_evidence["id"] in rec["evidence_ids"] and snapshot["id"]
    trace = next(f for f in result["agent_results"] if f["agent"] == "context-fusion-agent")["findings"][0]["reasoning_trace"]
    assert link["relation"] == "affects_component" and trace["affected_scope"] == [component["id"]]
