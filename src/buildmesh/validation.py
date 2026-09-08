"""Qualcomm deployment metadata and evidence-gated validation state."""
from __future__ import annotations

import hashlib
import json
import os
import platform
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .hardware import hardware_metadata


STATES = ("NOT_AVAILABLE", "CONFIGURED", "MODEL_AVAILABLE", "QNN_AVAILABLE", "DEVICE_VERIFIED", "NPU_EXECUTED", "BENCHMARK_COMPLETE")


def deployment_manifest() -> dict[str, Any]:
    return json.loads((Path(__file__).with_name("qualcomm-deployment.json")).read_text(encoding="utf-8"))


def model_integrity(model_path: str | None = None) -> dict[str, Any]:
    selected = model_path or os.getenv("BUILDMESH_QNN_MODEL")
    if not selected or not Path(selected).is_file():
        return {"artifact_status": "unavailable", "artifact_path": selected or "unknown", "sha256": None, "file_size_bytes": None, "acquisition_source": "unknown", "acquisition_timestamp": None}
    path = Path(selected)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return {"artifact_status": "available", "artifact_path": str(path.resolve()), "sha256": digest, "file_size_bytes": path.stat().st_size, "acquisition_source": os.getenv("BUILDMESH_QNN_MODEL_SOURCE", "unknown"), "acquisition_timestamp": os.getenv("BUILDMESH_QNN_MODEL_ACQUIRED_AT", "unknown")}


def validation_state(backend: str | None = None, model_path: str | None = None, qnn_executed: bool = False, benchmark_complete: bool = False) -> dict[str, Any]:
    backend = backend or os.getenv("VISION_BACKEND", "").casefold()
    hardware, artifact = hardware_metadata(), model_integrity(model_path)
    state = "NOT_AVAILABLE"
    evidence: list[str] = []
    if backend == "qnn":
        state, evidence = "CONFIGURED", ["VISION_BACKEND=qnn"]
    if state == "CONFIGURED" and artifact["artifact_status"] == "available":
        state, evidence = "MODEL_AVAILABLE", [*evidence, "local model artifact integrity"]
    if state == "MODEL_AVAILABLE" and hardware["qnn_execution_provider_available"]:
        state, evidence = "QNN_AVAILABLE", [*evidence, "QNNExecutionProvider discovery"]
    device_verified = hardware["os"] == "Windows" and hardware["soc"] == "qualcomm-snapdragon"
    if state == "QNN_AVAILABLE" and device_verified:
        state, evidence = "DEVICE_VERIFIED", [*evidence, "locally observed Windows Qualcomm/Snapdragon identity"]
    if state == "DEVICE_VERIFIED" and qnn_executed:
        state, evidence = "NPU_EXECUTED", [*evidence, "validated QNN result with execution_target=npu"]
    if state == "NPU_EXECUTED" and benchmark_complete:
        state, evidence = "BENCHMARK_COMPLETE", [*evidence, "persisted benchmark evidence"]
    return {"state": state, "states": list(STATES), "transition_evidence": evidence, "backend": backend or "not_configured", "hardware": hardware, "model": artifact, "qnn_executed": qnn_executed, "benchmark_complete": benchmark_complete, "captured_at": datetime.now(UTC).isoformat()}


def qnn_readiness() -> dict[str, Any]:
    backend = os.getenv("VISION_BACKEND", "").casefold()
    runtime = hardware_metadata()
    return {"target_backend": backend or "not_configured", "os": runtime["os"], "architecture": runtime["machine"], "python_version": platform.python_version(), "onnxruntime_version": runtime["onnxruntime_version"], "qnn_execution_provider_available": runtime["qnn_execution_provider_available"], "qnn_library_path": os.getenv("BUILDMESH_QNN_BACKEND_PATH", "unknown"), "device": {key: runtime[key] for key in ("device_manufacturer", "device_model", "soc")}, "model": model_integrity(), "validation": validation_state(backend), "no_cpu_fallback": backend == "qnn"}


def parse_qualcomm_result(payload: object) -> dict[str, Any]:
    required = {"source", "job_id", "target_device", "model", "runtime", "compute_unit", "latency_ms", "source_url", "completed_at"}
    if not isinstance(payload, dict) or set(payload) != required:
        raise ValueError("Qualcomm result must contain exactly the required evidence fields")
    if payload["source"] != "Qualcomm AI Hub":
        raise ValueError("Qualcomm result source must be 'Qualcomm AI Hub'")
    for field in required - {"latency_ms"}:
        if not isinstance(payload[field], str) or not payload[field].strip():
            raise ValueError(f"Qualcomm result {field} must be a non-empty string")
    if not payload["source_url"].startswith("https://"):
        raise ValueError("Qualcomm result source_url must be an https URL")
    if isinstance(payload["latency_ms"], bool) or not isinstance(payload["latency_ms"], (int, float)) or payload["latency_ms"] <= 0:
        raise ValueError("Qualcomm result latency_ms must be positive")
    try:
        datetime.fromisoformat(payload["completed_at"].replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("Qualcomm result completed_at must be ISO-8601") from exc
    return {**payload, "latency_ms": float(payload["latency_ms"]), "measurement_origin": "qualcomm_hosted_device", "buildmesh_local_measurement": False}
