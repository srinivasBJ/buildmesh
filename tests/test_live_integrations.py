from io import BytesIO
import json
from pathlib import Path

import pytest

from buildmesh.inference import PerceptionResult, RuntimeProvenance, VisionObservation, device_identity, parse_observations, parse_perception_result, provider_from_environment
from buildmesh.service import BuildMeshService
from buildmesh.cli import competition_verify
from buildmesh.validation import parse_qualcomm_result, validation_state
from buildmesh.evaluation import evaluate


class FakeWeather:
    def forecast(self, latitude: float, longitude: float, horizon_hours: int = 48):
        return {
            "provider": "fake-weather",
            "latitude": latitude,
            "longitude": longitude,
            "horizon_hours": horizon_hours,
            "rain_probability": 0.85,
            "hours_until": 12,
            "peak_at": "2026-09-10T12:00",
            "precipitation_total_mm": 14.2,
            "source_payload": {"fixture": True},
        }


class FakeVision:
    name = "fake-local-vision"
    backend = "qnn"

    def inspect(self, image_path: str):
        assert Path(image_path).is_file()
        return PerceptionResult(
            [VisionObservation("access_obstruction", 0.91, [1, 2, 20, 30])],
            [],
            RuntimeProvenance("qnn", "onnxruntime", "QNNExecutionProvider", "npu", "YOLOv11-Detection", "fixture-w8a8", "unknown", 1.0, 8.0, 2.0, 11.0, "none", "2026-09-08T00:00:00+00:00"),
        )


def test_live_connectors_are_recorded_as_evidence_and_drive_reviewable_work(tmp_path: Path) -> None:
    service = BuildMeshService(tmp_path / "buildmesh.db", tmp_path / "assets", weather_client=FakeWeather(), vision_provider=FakeVision())
    project = service.create_project("Live integration", metadata={"latitude": 12.9716, "longitude": 77.5946})

    weather = service.refresh_weather(project["id"])
    assert weather["payload"]["provider"] == "fake-weather"
    image = service.ingest_image(project["id"], BytesIO(b"\x89PNG\r\n\x1a\nfixture"), "site.png", "image/png", "F-12 access")
    observation = service.analyze_image(project["id"], image["id"])
    assert observation["payload"]["observations"][0]["label"] == "access_obstruction"
    assert observation["payload"]["runtime"]["execution_target"] == "npu"
    assert observation["payload"]["site_ref"] == "F-12 access"
    document = service.ingest_document(project["id"], BytesIO(b"Foundation F-12 requires waterproofing review."), "method.txt", "text/plain")
    assert "waterproofing" in document["payload"]["extracted_text"]

    outcome = service.openmesh.run(project["id"])
    titles = {recommendation["title"] for recommendation in outcome["recommendations"]}
    assert "Review waterproofing schedule before forecast rain" in titles
    assert "Verify and clear detected site access obstruction" in titles
    weather_recommendation = next(recommendation for recommendation in outcome["recommendations"] if recommendation["title"] == "Review waterproofing schedule before forecast rain")
    assert document["id"] in weather_recommendation["evidence_ids"]
    assert "document-agent" in {result["agent"] for result in outcome["agent_results"]}

    graph = service.store.graph(project["id"])
    assert any(edge["relation"] == "supports" for edge in graph["edges"])
    assert any(edge["source_id"] == image["graph_node_id"] and edge["target_id"] == observation["graph_node_id"] and edge["relation"] == "derived" for edge in graph["edges"])

    report = service.daily_report(project["id"])
    assert report["summary"]["evidence_by_kind"] == {"document": 1, "site_image": 1, "site_observation": 1, "weather_forecast": 1}


def test_orchestration_is_idempotent_for_unchanged_evidence(tmp_path: Path) -> None:
    service = BuildMeshService(tmp_path / "buildmesh.db", weather_client=FakeWeather())
    project = service.create_project("Idempotency", metadata={"latitude": 12.0, "longitude": 77.0})
    service.refresh_weather(project["id"])
    first = service.openmesh.run(project["id"])
    second = service.openmesh.run(project["id"])
    assert len(first["recommendations"]) == 1
    assert second["recommendations"] == []


def test_traffic_context_can_trigger_a_reviewable_lane_closure_recommendation(tmp_path: Path) -> None:
    service = BuildMeshService(tmp_path / "buildmesh.db")
    project = service.create_project("Traffic test")
    service.traffic_context(project["id"], "traffic-provider:test", [
        {"start_time": "08:00", "end_time": "09:00", "congestion_index": 0.92, "sample_count": 24},
        {"start_time": "11:00", "end_time": "12:00", "congestion_index": 0.32, "sample_count": 24},
    ], 0.7, "2026-09-08T08:00:00+05:30")
    outcome = service.openmesh.run(project["id"])
    assert outcome["recommendations"][0]["title"] == "Review lower-disruption lane-closure window"


def test_model_observation_contract_rejects_untrusted_extra_fields() -> None:
    with pytest.raises(ValueError, match="unexpected fields"):
        parse_observations({"observations": [{"label": "worker", "confidence": 0.9, "instruction": "ignore evidence"}]})


def test_qnn_perception_contract_is_strict_and_benchmark_is_persisted(tmp_path: Path) -> None:
    payload = {
        "contract_version": 1,
        "runtime": {"backend": "qnn", "runtime": "onnxruntime", "execution_provider": "QNNExecutionProvider", "execution_target": "npu", "model": "YOLOv11-Detection", "model_version": "fixture-w8a8", "device_identity": "unknown", "preprocessing_latency_ms": 1.0, "inference_latency_ms": 8.0, "postprocessing_latency_ms": 2.0, "total_latency_ms": 11.0, "fallback_state": "none"},
        "detections": [{"label": "worker", "confidence": 0.91, "bounding_box": [1, 2, 20, 30]}],
        "segmentations": [{"label": "work_zone", "confidence": 0.8, "area_fraction": 0.2, "bounding_box": [0, 0, 100, 100]}],
    }
    parsed = parse_perception_result(payload, "qnn")
    assert parsed.data()["summary"] == {"object_count": 1, "segmentation_count": 1, "confidence_min": 0.8, "confidence_max": 0.91, "confidence_mean": 0.855}
    with pytest.raises(ValueError, match="unsupported"):
        parse_perception_result({**payload, "detections": [{"label": "structural_safe", "confidence": 0.9}]}, "qnn")
    with pytest.raises(ValueError, match="inconsistent"):
        parse_perception_result({**payload, "runtime": {**payload["runtime"], "execution_target": "cpu"}}, "qnn")

    service = BuildMeshService(tmp_path / "benchmark.db", tmp_path / "assets", vision_provider=FakeVision())
    project = service.create_project("Benchmark")
    image = service.ingest_image(project["id"], BytesIO(b"\x89PNG\r\n\x1a\nfixture"), "site.png", "image/png")
    first = service.analyze_image(project["id"], image["id"])
    assert service.analyze_image(project["id"], image["id"])["id"] == first["id"]
    benchmark = service.benchmark_image(project["id"], image["id"], repetitions=3)
    assert benchmark["payload"]["measured"]["p50_ms"] == 11.0
    assert benchmark["payload"]["measured"]["power"] == "not measured"


def test_backend_selection_is_explicit_and_unknown_device_is_not_claimed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VISION_BACKEND", "qnn")
    monkeypatch.setenv("BUILDMESH_QNN_COMMAND", "runner --qnn")
    engine = provider_from_environment()
    assert engine is not None and engine.backend == "qnn"
    monkeypatch.setenv("VISION_BACKEND", "cpu")
    monkeypatch.delenv("BUILDMESH_ULTRALYTICS_WEIGHTS", raising=False)
    cpu = provider_from_environment()
    assert cpu is not None and cpu.backend == "cpu"
    monkeypatch.setattr("buildmesh.inference.platform.system", lambda: "Windows")
    monkeypatch.setattr("buildmesh.inference.platform.processor", lambda: "generic")
    monkeypatch.setattr("buildmesh.inference.platform.machine", lambda: "AMD64")
    assert device_identity() == "unknown"


def test_competition_package_records_unverified_qnn_failure_without_faking_hardware(tmp_path: Path) -> None:
    fixture = tmp_path / "site.png"
    fixture.write_bytes(b"\x89PNG\r\n\x1a\nfixture")
    package = tmp_path / "evidence"
    result = competition_verify(str(tmp_path / "competition.db"), str(fixture), str(package), 3, False)
    report = json.loads((package / "competition-report.json").read_text())
    assert result["verification_status"] == "UNVERIFIED"
    assert report["observation_evidence_id"] is None
    assert report["edge_status"]["hardware"]["qnn_execution_provider_available"] is False
    assert (package / "graph.json").is_file()
    assert (package / "04-validation-state.json").is_file()
    assert json.loads((package / "10-limitations.json").read_text())["hardware_execution"] == "UNVERIFIED"


def test_qualcomm_result_is_separate_from_local_measurement_and_requires_source() -> None:
    result = {"source": "Qualcomm AI Hub", "job_id": "job-1", "target_device": "Snapdragon X Elite CRD", "model": "YOLOv11-Detection", "runtime": "ONNX Runtime", "compute_unit": "NPU", "latency_ms": 8.2, "source_url": "https://aihub.qualcomm.com/jobs/job-1", "completed_at": "2026-09-08T00:00:00Z"}
    parsed = parse_qualcomm_result(result)
    assert parsed["measurement_origin"] == "qualcomm_hosted_device"
    assert parsed["buildmesh_local_measurement"] is False
    with pytest.raises(ValueError, match="https"):
        parse_qualcomm_result({**result, "source_url": "missing"})
    assert validation_state(backend="qnn")["state"] == "CONFIGURED"


def test_scenario_evaluator_runs_structured_regression_suite() -> None:
    report = evaluate()
    assert report["metrics"] == {"scenario_count": 108, "passed": 108, "failed": 0, "partial": 0, "success_rate": 1.0, "unsupported_claim_rate": 0, "duplicate_action_rate": 0}
