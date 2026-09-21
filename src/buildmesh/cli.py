from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from datetime import UTC, datetime
from math import ceil
from pathlib import Path
from typing import Any

from .inference import CpuVisionEngine
from .service import BuildMeshService
from .validation import deployment_manifest, parse_qualcomm_result, validation_state
from .evaluation import evaluate
from .evaluation import _design_multi


def seed_demo(database: str, site_image: str | None = None) -> dict[str, object]:
    database_path = Path(database)
    service = BuildMeshService(database_path, database_path.parent / f"{database_path.stem}.assets")
    project = service.create_project("Bengaluru Corridor Expansion", "Bengaluru, Karnataka", {"phase": "foundation"})
    task = service.create_task(project["id"], "Foundation F-12 excavation", {"external_ref": "F-12", "planned_quantity_m3": 42, "assigned_crew": "Crew B"})
    service.set_task_status(project["id"], task["id"], "in_progress", "demo.system", "active demo work package")
    service.progress_update(project["id"], task["id"], 85, 42, 35.5, "worker.raj@example.com", 60)
    service.weather_context(project["id"], "weather-provider:demo", 0.72, 30, "Elevated rain likelihood during planned waterproofing window")
    image_id = None
    if site_image:
        fixture = Path(site_image)
        if not fixture.is_file():
            raise ValueError("--site-image must name an existing construction-site image")
        with fixture.open("rb") as stream:
            image = service.ingest_image(project["id"], stream, fixture.name, _image_media_type(fixture), "F-12")
        image_id = image["id"]
        service.analyze_image(project["id"], image_id)
    result = service.openmesh.run(project["id"])
    return {"project_id": project["id"], "task_id": task["id"], "image_evidence_id": image_id, "recommendation_ids": [r["id"] for r in result["recommendations"]]}


def reset_demo(database: str, site_image: str | None = None) -> dict[str, object]:
    database_path = Path(database).resolve()
    if database_path.parent == database_path or database_path.parent == Path("/"):
        raise ValueError("refusing to reset a database at filesystem root")
    if database_path.exists():
        database_path.unlink()
    asset_root = database_path.parent / f"{database_path.stem}.assets"
    if asset_root.is_dir():
        shutil.rmtree(asset_root)
    return seed_demo(str(database_path), site_image)


def _write_package(output: Path, name: str, value: Any) -> None:
    (output / name).write_text(json.dumps(value, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")


def _image_media_type(path: Path) -> str:
    return {".png": "image/png", ".webp": "image/webp"}.get(path.suffix.lower(), "image/jpeg")


def _benchmark_engine(engine: Any, fixture: Path, repetitions: int) -> dict[str, Any]:
    cold, warmup = engine.inspect(str(fixture)), engine.inspect(str(fixture))
    runs = [engine.inspect(str(fixture)) for _ in range(repetitions)]
    samples = sorted(item.runtime.total_latency_ms for item in runs)
    mean = sum(samples) / len(samples)
    return {"status": "measured", "backend": cold.runtime.backend, "model": cold.runtime.model, "model_version": cold.runtime.model_version, "device_identity": cold.runtime.device_identity, "cold_start_ms": cold.runtime.total_latency_ms, "warm_up_ms": warmup.runtime.total_latency_ms, "p50_ms": samples[(len(samples) - 1) // 2], "p95_ms": samples[max(0, ceil(len(samples) * 0.95) - 1)], "throughput_fps": round(1000 / mean, 4) if mean else None, "repetitions": repetitions}


def competition_verify(database: str, site_image: str, output: str, repetitions: int, run_tests: bool, cpu_weights: str | None = None) -> dict[str, Any]:
    """Produce evidence from an attempted real QNN flow; failures are evidence too."""
    fixture = Path(site_image).resolve()
    package = Path(output).resolve()
    if not 3 <= repetitions <= 100:
        raise ValueError("--repetitions must be between 3 and 100")
    if not fixture.is_file():
        raise ValueError("competition-verify requires an existing --site-image")
    if package.exists() and any(package.iterdir()):
        raise ValueError("competition evidence output directory must be empty")
    package.mkdir(parents=True, exist_ok=True)
    baseline = reset_demo(database)
    database_path = Path(database).resolve()
    service = BuildMeshService(database_path, database_path.parent / f"{database_path.stem}.assets")
    project_id = str(baseline["project_id"])
    image = None
    observation = benchmark = approval = None
    recommendation = None
    error = None
    cpu_comparison: dict[str, Any] = {"status": "not_available", "reason": "--cpu-weights was not supplied"}
    try:
        with fixture.open("rb") as stream:
            image = service.ingest_image(project_id, stream, fixture.name, _image_media_type(fixture), "F-12")
        observation = service.analyze_image(project_id, image["id"])
        benchmark = service.benchmark_image(project_id, image["id"], repetitions)
        outcome = service.openmesh.run(project_id)
        recommendation = next((item for item in outcome["recommendations"] if observation["id"] in item["evidence_ids"]), None)
        if recommendation:
            approval = service.approve(recommendation["id"], "competition.reviewer@example.com", "approved", "competition evidence package")
    except (RuntimeError, ValueError, OSError) as exc:
        error = str(exc)
    if cpu_weights:
        try:
            cpu_comparison = _benchmark_engine(CpuVisionEngine(cpu_weights), fixture, repetitions)
        except (RuntimeError, ValueError, OSError) as exc:
            cpu_comparison = {"status": "failed", "error": str(exc)}
    test_result: dict[str, Any] = {"status": "not_run"}
    if run_tests:
        completed = subprocess.run([sys.executable, "-m", "pytest", "-q"], capture_output=True, text=True, cwd=Path.cwd(), timeout=180)
        test_result = {"status": "passed" if completed.returncode == 0 else "failed", "returncode": completed.returncode, "output": completed.stdout[-12000:], "errors": completed.stderr[-4000:]}
    status = "VERIFIED" if observation and observation["payload"]["runtime"]["backend"] == "qnn" and observation["payload"]["runtime"]["execution_target"] == "npu" else "UNVERIFIED"
    report = {
        "verification_status": status,
        "failure": error,
        "edge_status": service.edge_status(),
        "qualcomm_reference": {"status": "not_retrieved", "source_url": "https://aihub.qualcomm.com/models/yolov11_det", "note": "Qualcomm reference data is intentionally separate from BuildMesh measurements."},
        "fixture": {"path": str(fixture), "image_evidence_id": image["id"] if image else None},
        "observation_evidence_id": observation["id"] if observation else None,
        "benchmark_evidence_id": benchmark["id"] if benchmark else None,
        "recommendation_id": recommendation["id"] if recommendation else None,
        "approval": approval,
        "cpu_comparison": cpu_comparison,
        "tests": test_result,
    }
    validation = validation_state(qnn_executed=bool(observation and observation["payload"]["runtime"]["execution_target"] == "npu"), benchmark_complete=benchmark is not None)
    limitations = {"hardware_execution": "VERIFIED" if validation["state"] in {"NPU_EXECUTED", "BENCHMARK_COMPLETE"} else "UNVERIFIED", "reason": error or "QNN/NPU execution has not been evidenced on this host", "power": "not measured", "memory": "not measured"}
    _write_package(package, "competition-report.json", report)
    _write_package(package, "01-device.json", service.edge_status()["hardware"])
    _write_package(package, "02-runtime.json", service.qnn_readiness())
    _write_package(package, "03-model.json", {"manifest": deployment_manifest(), "integrity": validation["model"]})
    _write_package(package, "04-validation-state.json", validation)
    _write_package(package, "05-benchmark.json", benchmark or {"status": "NOT_AVAILABLE", "reason": error or "no local QNN benchmark"})
    _write_package(package, "06-project-lineage.json", {"graph": service.store.graph(project_id), "timeline": service.store.events(project_id)})
    _write_package(package, "07-recommendation.json", recommendation or {"status": "NOT_AVAILABLE", "reason": error or "no QNN-grounded recommendation"})
    _write_package(package, "08-approval.json", approval or {"status": "NOT_AVAILABLE", "reason": error or "no QNN-grounded approval"})
    _write_package(package, "09-tests.json", test_result)
    _write_package(package, "10-limitations.json", limitations)
    _write_package(package, "hardware.json", service.edge_status()["hardware"])
    _write_package(package, "graph.json", service.store.graph(project_id))
    _write_package(package, "timeline.json", service.store.events(project_id))
    if observation:
        _write_package(package, "observation.json", observation)
    if benchmark:
        _write_package(package, "benchmark.json", benchmark)
    if recommendation:
        _write_package(package, "recommendation.json", recommendation)
    if approval:
        _write_package(package, "approval.json", approval)
    _write_package(package, "cpu-comparison.json", cpu_comparison)
    return {**report, "package": str(package)}


def import_qualcomm_result(database: str, project_id: str, file_path: str) -> dict[str, Any]:
    payload = json.loads(Path(file_path).read_text(encoding="utf-8"))
    parsed = parse_qualcomm_result(payload)
    database_path = Path(database)
    service = BuildMeshService(database_path, database_path.parent / f"{database_path.stem}.assets")
    return service.import_qualcomm_result(project_id, parsed)


def spec_status() -> dict[str, Any]:
    candidates = [Path.cwd() / "docs" / "buildmesh-spec" / "atomic-requirements.yaml", Path(__file__).parents[2] / "docs" / "buildmesh-spec" / "atomic-requirements.yaml"]
    spec = next((candidate for candidate in candidates if candidate.is_file()), None)
    if spec is None:
        raise RuntimeError("atomic requirements are unavailable; run from a BuildMesh source checkout")
    requirements, current = [], {}
    for raw in spec.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line.startswith("- id: "):
            if current:
                requirements.append(current)
            current = {"id": line.removeprefix("- id: ")}
        elif current and ": " in line:
            key, value = line.split(": ", 1)
            current[key] = value
    if current:
        requirements.append(current)
    counts: dict[str, int] = {}
    for requirement in requirements:
        counts[requirement.get("status", "UNKNOWN")] = counts.get(requirement.get("status", "UNKNOWN"), 0) + 1
    return {"requirements": requirements, "counts": counts, "without_verification": [item["id"] for item in requirements if item.get("verification") in {None, "future scenario"}]}


def twin_status_command(database: str, project_id: str, scope_id: str, snapshot_id: str) -> dict[str, Any]:
    return BuildMeshService(database).twin_status(project_id, scope_id, snapshot_id)


def twin_diff_command(database: str, project_id: str, previous_snapshot_id: str, current_snapshot_id: str) -> list[dict[str, Any]]:
    return BuildMeshService(database).twin_diff(project_id, previous_snapshot_id, current_snapshot_id)


def twin_reconcile_command(database: str, project_id: str, snapshot_id: str) -> list[dict[str, Any]]:
    return BuildMeshService(database).twin_reconcile(project_id, snapshot_id)


def twin_demo(database: str) -> dict[str, Any]:
    """Deterministic logical three-floor fixture; observations are explicitly fixtures."""
    database_path = Path(database)
    service = BuildMeshService(database_path, database_path.parent / f"{database_path.stem}.assets")
    project = service.create_project("Building A as-built fixture")
    root = service.store.project_root(project["id"])
    site = service.create_twin_entity(project["id"], root["id"], "site", "Fixture Site")
    building = service.create_twin_entity(project["id"], site["id"], "building", "Building A")
    floors: list[dict[str, Any]] = []
    for index in range(1, 4):
        floor = service.create_twin_entity(project["id"], building["id"], "floor", f"Floor {index}")
        zone = service.create_twin_entity(project["id"], floor["id"], "zone", f"Floor {index} core")
        components = {name: service.create_twin_entity(project["id"], zone["id"], "component", name, {"planned_state": "COMPLETE"}) for name in ("structural frame", "walls", "electrical rough-in", "plumbing rough-in", "windows")}
        floors.append({"floor": floor, "zone": zone, "components": components})
    source = service.weather_context(project["id"], "fixture:twin-evidence", 0.1, 24, "Fixture-only source evidence")
    snapshots = []
    stages = [("Snapshot 1", ["structural frame"]), ("Snapshot 2", ["structural frame", "walls"]), ("Snapshot 3", ["structural frame", "walls", "electrical rough-in", "plumbing rough-in"])]
    for number, (label, visible) in enumerate(stages, start=1):
        observed = []
        for floor in floors:
            for component_name in visible:
                observed.append({"component_id": floor["components"][component_name]["id"], "observed_state": "COMPLETE" if component_name != "walls" or number > 2 else "IN_PROGRESS", "confidence": 0.8, "epistemic_state": "INFERRED", "zone_id": floor["zone"]["id"], "fixture": True})
        snapshots.append(service.create_twin_snapshot(project["id"], label, f"2026-09-0{number}T10:00:00Z", [source["id"]], observed))
    return {"project_id": project["id"], "building_id": building["id"], "floor_ids": [item["floor"]["id"] for item in floors], "snapshot_ids": [item["id"] for item in snapshots], "diff": service.twin_diff(project["id"], snapshots[1]["id"], snapshots[2]["id"]), "fixture": True}


def environment_demo(database: str) -> dict[str, Any]:
    service = BuildMeshService(database)
    project = service.create_project("Road foundation environmental fixture", "Bengaluru", {"latitude": 12.9716, "longitude": 77.5946})
    drainage = service.create_task(project["id"], "Drainage", {"activity": "excavation"})
    excavation = service.create_task(project["id"], "Foundation excavation", {"activity": "excavation"})
    service.add_task_dependency(project["id"], excavation["id"], drainage["id"])
    service.set_task_status(project["id"], excavation["id"], "in_progress", "fixture.system")
    now = datetime.now(UTC).isoformat()
    root = service.store.project_root(project["id"])
    zone = service.create_twin_entity(project["id"], root["id"], "zone", "Foundation zone")
    component = service.create_twin_entity(project["id"], zone["id"], "component", "Exposed foundation excavation")
    service.link_task_component(project["id"], excavation["id"], component["id"])
    source = service.weather_context(project["id"], "fixture:perception-source", .8, 12)
    snapshot = service.create_twin_snapshot(project["id"], "Fixture as-built observation", now, [source["id"]], [{"component_id": component["id"], "observed_state": "IN_PROGRESS", "confidence": .8, "epistemic_state": "INFERRED", "zone_id": zone["id"], "fixture": True}])
    weather = service.environmental_context(project["id"], kind="weather_forecast", source="fixture:weather", source_type="fixture", retrieved_at=now, observed_at=now, latitude=12.9716, longitude=77.5946, values={"rain_probability": 0.8, "precipitation_mm": 8, "wind_speed_kph": 15}, units={"rain_probability": "probability", "precipitation_mm": "mm", "wind_speed_kph": "km/h"}, fixture=True, confidence=0.8)
    traffic = service.environmental_context(project["id"], kind="traffic_context", source="fixture:traffic", source_type="fixture", retrieved_at=now, observed_at=now, latitude=12.9716, longitude=77.5946, values={"congestion_index": 0.85, "window_start": "08:00", "window_end": "10:00"}, units={"congestion_index": "index"}, fixture=True, confidence=0.7)
    event = service.environmental_context(project["id"], kind="local_event", source="fixture:local-event", source_type="fixture", retrieved_at=now, observed_at=now, latitude=12.9716, longitude=77.5946, values={"disruption_level": .7, "window_start": "08:00", "window_end": "10:00"}, units={"disruption_level": "index"}, fixture=True, confidence=.7)
    outcome = service.openmesh.run(project["id"])
    return {"project_id": project["id"], "active_task_id": excavation["id"], "component_id": component["id"], "snapshot_id": snapshot["id"], "environmental_summary": service.environment_status(project["id"]), "project_context": service.environment_plan(project["id"], excavation["id"]), "candidate_windows": service.candidate_work_windows(project["id"], excavation["id"], [{"start": "08:00", "end": "10:00"}]), "evidence_ids": [weather["id"], traffic["id"], event["id"]], "recommendations": outcome["recommendations"], "fixture": True}


def main() -> None:
    parser = argparse.ArgumentParser(prog="buildmesh")
    parser.add_argument("--database", default="buildmesh.db")
    commands = parser.add_subparsers(dest="command", required=True)
    demo = commands.add_parser("demo")
    demo.add_argument("--site-image")
    reset = commands.add_parser("demo-reset")
    reset.add_argument("--site-image")
    verify = commands.add_parser("competition-verify")
    verify.add_argument("--site-image", required=True)
    verify.add_argument("--output", default="competition-evidence")
    verify.add_argument("--repetitions", type=int, default=20)
    verify.add_argument("--run-tests", action="store_true")
    verify.add_argument("--cpu-weights")
    imported = commands.add_parser("import-qualcomm-result")
    imported.add_argument("project_id")
    imported.add_argument("file")
    spec = commands.add_parser("spec-status")
    evaluation = commands.add_parser("evaluate")
    evaluation.add_argument("--scenario", default="all", choices=["all", *__import__("buildmesh.evaluation", fromlist=["SCENARIOS"]).SCENARIOS])
    evaluation.add_argument("--json", action="store_true")
    twin_status_parser = commands.add_parser("twin-status")
    twin_status_parser.add_argument("project_id")
    twin_status_parser.add_argument("scope_id")
    twin_status_parser.add_argument("snapshot_id")
    twin_diff_parser = commands.add_parser("twin-diff")
    twin_diff_parser.add_argument("project_id")
    twin_diff_parser.add_argument("previous_snapshot_id")
    twin_diff_parser.add_argument("current_snapshot_id")
    twin_reconcile_parser = commands.add_parser("twin-reconcile")
    twin_reconcile_parser.add_argument("project_id")
    twin_reconcile_parser.add_argument("snapshot_id")
    commands.add_parser("twin-demo")
    commands.add_parser("design-reality-demo")
    env_demo = commands.add_parser("environment-demo")
    environment_status_parser = commands.add_parser("environment-status")
    environment_status_parser.add_argument("project_id")
    environment_plan_parser = commands.add_parser("environment-plan")
    environment_plan_parser.add_argument("project_id")
    environment_plan_parser.add_argument("task_id")
    environment_refresh_parser = commands.add_parser("environment-refresh")
    environment_refresh_parser.add_argument("project_id")
    context_analyze_parser = commands.add_parser("context-analyze")
    context_analyze_parser.add_argument("project_id")
    context_analyze_parser.add_argument("task_id")
    spatial_import_parser = commands.add_parser("spatial-import")
    spatial_import_parser.add_argument("project_id"); spatial_import_parser.add_argument("file")
    ifc_import_parser = commands.add_parser("ifc-import"); ifc_import_parser.add_argument("project_id"); ifc_import_parser.add_argument("file")
    spatial_status_parser = commands.add_parser("spatial-status"); spatial_status_parser.add_argument("project_id")
    spatial_diff_parser = commands.add_parser("spatial-diff"); spatial_diff_parser.add_argument("project_id"); spatial_diff_parser.add_argument("planned_id"); spatial_diff_parser.add_argument("snapshot_id")
    spatial_analyze_parser = commands.add_parser("spatial-analyze"); spatial_analyze_parser.add_argument("project_id"); spatial_analyze_parser.add_argument("scope_id")
    spatial_semantics_parser = commands.add_parser("spatial-semantics"); spatial_semantics_parser.add_argument("project_id")
    spatial_query_parser = commands.add_parser("spatial-query"); spatial_query_parser.add_argument("project_id"); spatial_query_parser.add_argument("query"); spatial_query_parser.add_argument("--scope-id"); spatial_query_parser.add_argument("--component-type")
    serve = commands.add_parser("serve")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    if args.command == "demo":
        print(json.dumps(seed_demo(args.database, args.site_image), indent=2))
    elif args.command == "demo-reset":
        print(json.dumps(reset_demo(args.database, args.site_image), indent=2))
    elif args.command == "competition-verify":
        print(json.dumps(competition_verify(args.database, args.site_image, args.output, args.repetitions, args.run_tests, args.cpu_weights), indent=2))
    elif args.command == "import-qualcomm-result":
        print(json.dumps(import_qualcomm_result(args.database, args.project_id, args.file), indent=2))
    elif args.command == "spec-status":
        print(json.dumps(spec_status(), indent=2))
    elif args.command == "evaluate":
        result = evaluate(args.scenario)
        print(json.dumps(result, indent=2) if args.json else "\n".join(f"{item['scenario']} {item['status']} — {item['title']}" for item in result["scenarios"]))
    elif args.command == "twin-status":
        print(json.dumps(twin_status_command(args.database, args.project_id, args.scope_id, args.snapshot_id), indent=2))
    elif args.command == "twin-diff":
        print(json.dumps(twin_diff_command(args.database, args.project_id, args.previous_snapshot_id, args.current_snapshot_id), indent=2))
    elif args.command == "twin-reconcile":
        print(json.dumps(twin_reconcile_command(args.database, args.project_id, args.snapshot_id), indent=2))
    elif args.command == "twin-demo":
        print(json.dumps(twin_demo(args.database), indent=2))
    elif args.command == "design-reality-demo":
        from tempfile import TemporaryDirectory
        with TemporaryDirectory(prefix="buildmesh-design-demo-") as directory:
            result = _design_multi(directory)
        print(json.dumps({"snapshots": result["snapshots"], "timeline": result["timeline"], "recommendation_count": len(result["recommendations"]), "fixture": True}, indent=2))
    elif args.command == "environment-demo":
        print(json.dumps(environment_demo(args.database), indent=2))
    elif args.command == "environment-status":
        print(json.dumps(BuildMeshService(args.database).environment_status(args.project_id), indent=2))
    elif args.command == "environment-plan":
        print(json.dumps(BuildMeshService(args.database).environment_plan(args.project_id, args.task_id), indent=2))
    elif args.command == "environment-refresh":
        print(json.dumps(BuildMeshService(args.database).refresh_environment(args.project_id), indent=2))
    elif args.command == "context-analyze":
        service = BuildMeshService(args.database)
        print(json.dumps({"plan": service.environment_plan(args.project_id, args.task_id), "recommendations": service.openmesh.run(args.project_id)["recommendations"]}, indent=2))
    elif args.command == "spatial-import":
        print(json.dumps(BuildMeshService(args.database).import_spatial_plan(args.project_id, json.loads(Path(args.file).read_text())), indent=2))
    elif args.command == "ifc-import":
        print(json.dumps(BuildMeshService(args.database).import_ifc(args.project_id, args.file), indent=2))
    elif args.command == "spatial-status":
        print(json.dumps(BuildMeshService(args.database).spatial_status(args.project_id), indent=2))
    elif args.command == "spatial-diff":
        print(json.dumps(BuildMeshService(args.database).spatial_diff(args.project_id, args.planned_id, args.snapshot_id), indent=2))
    elif args.command == "spatial-analyze":
        print(json.dumps(BuildMeshService(args.database).spatial_analyze(args.project_id, args.scope_id), indent=2))
    elif args.command == "spatial-semantics":
        print(json.dumps(BuildMeshService(args.database).spatial_semantics(args.project_id), indent=2))
    elif args.command == "spatial-query":
        print(json.dumps(BuildMeshService(args.database).spatial_query(args.project_id, args.query, args.scope_id, args.component_type), indent=2))
    else:
        import uvicorn
        from .api import create_app
        uvicorn.run(create_app(args.database), host=args.host, port=args.port)
