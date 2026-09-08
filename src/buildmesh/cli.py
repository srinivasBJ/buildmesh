from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from math import ceil
from pathlib import Path
from typing import Any

from .inference import CpuVisionEngine
from .service import BuildMeshService
from .validation import deployment_manifest, parse_qualcomm_result, validation_state
from .evaluation import evaluate


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
    else:
        import uvicorn
        from .api import create_app
        uvicorn.run(create_app(args.database), host=args.host, port=args.port)
