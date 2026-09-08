from __future__ import annotations

import json
import os
import platform
import shlex
import subprocess
import time
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, Protocol


# Construction labels plus the corresponding common YOLO/COCO labels. Model output
# cannot introduce arbitrary instructions or engineering claims into project state.
SUPPORTED_LABELS = frozenset({
    "access_area", "access_obstruction", "barrier", "car", "construction_equipment",
    "excavation_zone", "person", "temporary_barrier", "traffic_cone", "truck",
    "vehicle", "worker", "work_zone",
})


@dataclass(frozen=True)
class VisionObservation:
    label: str
    confidence: float
    bounding_box: list[float] | None = None


@dataclass(frozen=True)
class SegmentationObservation:
    label: str
    confidence: float
    area_fraction: float
    bounding_box: list[float] | None = None


@dataclass(frozen=True)
class RuntimeProvenance:
    backend: Literal["qnn", "cpu"]
    runtime: str
    execution_provider: str
    execution_target: Literal["npu", "cpu"]
    model: str
    model_version: str
    device_identity: str
    preprocessing_latency_ms: float
    inference_latency_ms: float
    postprocessing_latency_ms: float
    total_latency_ms: float
    fallback_state: Literal["none", "development_cpu"]
    measured_at: str


@dataclass(frozen=True)
class PerceptionResult:
    detections: list[VisionObservation]
    segmentations: list[SegmentationObservation]
    runtime: RuntimeProvenance

    def data(self) -> dict[str, Any]:
        confidences = [item.confidence for item in [*self.detections, *self.segmentations]]
        return {
            "detections": [asdict(item) for item in self.detections],
            "segmentations": [asdict(item) for item in self.segmentations],
            "runtime": asdict(self.runtime),
            "summary": {
                "object_count": len(self.detections),
                "segmentation_count": len(self.segmentations),
                "confidence_min": round(min(confidences), 4) if confidences else None,
                "confidence_max": round(max(confidences), 4) if confidences else None,
                "confidence_mean": round(sum(confidences) / len(confidences), 4) if confidences else None,
            },
        }


class VisionEngine(Protocol):
    """A local engine; BuildMesh domain code never selects an execution provider."""

    name: str
    backend: Literal["qnn", "cpu"]

    def inspect(self, image_path: str) -> PerceptionResult: ...


# Compatibility alias for integrations that used the previous boundary name.
VisionProvider = VisionEngine


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def device_identity() -> str:
    """Report Snapdragon only from locally observed Windows platform identity."""
    if platform.system() != "Windows":
        return "unknown"
    fields = " ".join(value for value in (platform.processor(), platform.machine(), os.getenv("PROCESSOR_IDENTIFIER", "")) if value).casefold()
    return "qualcomm-snapdragon-windows" if "qualcomm" in fields or "snapdragon" in fields else "unknown"


def _number(value: object, field: str, *, minimum: float = 0.0, maximum: float = 120_000.0) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not minimum <= float(value) <= maximum:
        raise ValueError(f"{field} must be a number between {minimum:g} and {maximum:g}")
    return round(float(value), 4)


def _label(value: object) -> str:
    if not isinstance(value, str) or value.strip().casefold() not in SUPPORTED_LABELS:
        raise ValueError("vision observation label is unsupported")
    return value.strip().casefold()


def _box(value: object) -> list[float] | None:
    if value is None:
        return None
    if not isinstance(value, list) or len(value) != 4:
        raise ValueError("vision observation bounding_box must contain four numbers")
    parsed = [_number(item, "bounding_box", minimum=0.0, maximum=1_000_000.0) for item in value]
    if parsed[2] <= parsed[0] or parsed[3] <= parsed[1]:
        raise ValueError("vision observation bounding_box must have positive area")
    return parsed


def _detections(entries: object) -> list[VisionObservation]:
    if not isinstance(entries, list):
        raise ValueError("vision runner detections must be a list")
    output: list[VisionObservation] = []
    for item in entries:
        if not isinstance(item, dict) or set(item) - {"label", "confidence", "bounding_box"}:
            raise ValueError("vision observation has unexpected fields")
        output.append(VisionObservation(_label(item.get("label")), _number(item.get("confidence"), "confidence", maximum=1.0), _box(item.get("bounding_box"))))
    return output


def _segmentations(entries: object) -> list[SegmentationObservation]:
    if not isinstance(entries, list):
        raise ValueError("vision runner segmentations must be a list")
    output: list[SegmentationObservation] = []
    for item in entries:
        if not isinstance(item, dict) or set(item) - {"label", "confidence", "area_fraction", "bounding_box"}:
            raise ValueError("segmentation observation has unexpected fields")
        output.append(SegmentationObservation(_label(item.get("label")), _number(item.get("confidence"), "confidence", maximum=1.0), _number(item.get("area_fraction"), "area_fraction", maximum=1.0), _box(item.get("bounding_box"))))
    return output


def parse_observations(payload: object) -> list[VisionObservation]:
    """Compatibility parser for manually entered or legacy detection observations."""
    return _detections(payload.get("observations") if isinstance(payload, dict) else payload)


def parse_perception_result(payload: object, expected_backend: Literal["qnn", "cpu"] | None = None) -> PerceptionResult:
    """Validate the small, versioned stdout contract from a local model runner."""
    if not isinstance(payload, dict) or set(payload) != {"contract_version", "runtime", "detections", "segmentations"}:
        raise ValueError("vision runner output must contain only contract_version, runtime, detections, and segmentations")
    if payload["contract_version"] != 1:
        raise ValueError("vision runner contract_version must be 1")
    runtime = payload["runtime"]
    fields = {"backend", "runtime", "execution_provider", "execution_target", "model", "model_version", "device_identity", "preprocessing_latency_ms", "inference_latency_ms", "postprocessing_latency_ms", "total_latency_ms", "fallback_state"}
    if not isinstance(runtime, dict) or set(runtime) != fields:
        raise ValueError("vision runner runtime has an invalid schema")
    backend, target, fallback = runtime["backend"], runtime["execution_target"], runtime["fallback_state"]
    if backend not in {"qnn", "cpu"} or (expected_backend and backend != expected_backend):
        raise ValueError("vision runner reported an unexpected backend")
    if (backend == "qnn" and (target != "npu" or fallback != "none")) or (backend == "cpu" and (target != "cpu" or fallback != "development_cpu")):
        raise ValueError("vision runner backend, target, and fallback state are inconsistent")
    for field in ("runtime", "execution_provider", "model", "model_version", "device_identity"):
        if not isinstance(runtime[field], str) or not runtime[field].strip() or len(runtime[field].strip()) > 240:
            raise ValueError(f"vision runner {field} must be a short non-empty string")
    timings = {field: _number(runtime[field], field) for field in ("preprocessing_latency_ms", "inference_latency_ms", "postprocessing_latency_ms", "total_latency_ms")}
    if timings["total_latency_ms"] + 0.1 < sum(value for field, value in timings.items() if field != "total_latency_ms"):
        raise ValueError("vision runner total_latency_ms is less than component timings")
    provenance = RuntimeProvenance(backend, runtime["runtime"].strip(), runtime["execution_provider"].strip(), target, runtime["model"].strip(), runtime["model_version"].strip(), runtime["device_identity"].strip(), timings["preprocessing_latency_ms"], timings["inference_latency_ms"], timings["postprocessing_latency_ms"], timings["total_latency_ms"], fallback, utc_now())
    return PerceptionResult(_detections(payload["detections"]), _segmentations(payload["segmentations"]), provenance)


class QnnVisionEngine:
    """Runs a Windows-local ONNX Runtime/QNN wrapper and validates every byte it returns."""

    name = "qualcomm-qnn-onnxruntime"
    backend: Literal["qnn"] = "qnn"

    def __init__(self, command: list[str], timeout_seconds: int = 120) -> None:
        if not command:
            raise ValueError("a local QNN inference command is required")
        self.command, self.timeout_seconds = command, timeout_seconds

    def inspect(self, image_path: str) -> PerceptionResult:
        try:
            completed = subprocess.run([*self.command, image_path], check=True, capture_output=True, text=True, timeout=self.timeout_seconds)
        except (OSError, subprocess.SubprocessError) as exc:
            raise RuntimeError(f"QNN local inference failed: {exc}") from exc
        try:
            payload = json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            raise ValueError("QNN local inference did not return JSON") from exc
        return parse_perception_result(payload, expected_backend="qnn")


QnnCommandVisionProvider = QnnVisionEngine


class CpuVisionEngine:
    """Actual local CPU fallback; it never claims NPU use."""

    name = "ultralytics-cpu"
    backend: Literal["cpu"] = "cpu"

    def __init__(self, weights: str, confidence_threshold: float = 0.35) -> None:
        try:
            from ultralytics import YOLO
        except ImportError as exc:
            raise RuntimeError("install BuildMesh with the vision extra to enable CPU YOLO inference") from exc
        self.model, self.weights, self.confidence_threshold = YOLO(weights), Path(weights), confidence_threshold

    def inspect(self, image_path: str) -> PerceptionResult:
        started = time.perf_counter()
        result = self.model(image_path, conf=self.confidence_threshold, verbose=False, device="cpu")[0]
        total_ms = round((time.perf_counter() - started) * 1000, 4)
        detections = [VisionObservation(_label(str(result.names[int(box.cls.item())])), round(float(box.conf.item()), 4), [round(float(value), 2) for value in box.xyxy[0].tolist()]) for box in result.boxes]
        speed = getattr(result, "speed", {}) or {}
        preprocessing, inference, postprocessing = (round(float(speed.get(key, 0.0)), 4) for key in ("preprocess", "inference", "postprocess"))
        return PerceptionResult(detections, [], RuntimeProvenance("cpu", "ultralytics", "onnxruntime-cpu-fallback", "cpu", self.weights.stem, self.weights.name, device_identity(), preprocessing, inference or total_ms, postprocessing, max(total_ms, preprocessing + inference + postprocessing), "development_cpu", utc_now()))


UltralyticsVisionProvider = CpuVisionEngine


class DemoVisionProvider:
    """A deliberate test/demo fixture; environment selection never uses it."""

    name = "demo-fixture-not-a-model"
    backend: Literal["cpu"] = "cpu"

    def inspect(self, image_path: str) -> PerceptionResult:
        if Path(image_path).suffix.lower() not in {".jpg", ".jpeg", ".png", ".webp"}:
            raise ValueError("expected a supported image path")
        runtime = RuntimeProvenance("cpu", "demo-fixture", "demo-fixture", "cpu", "demo-fixture", "1", "unknown", 0, 0, 0, 0, "development_cpu", utc_now())
        return PerceptionResult([VisionObservation("temporary_barrier", 0.97), VisionObservation("access_obstruction", 0.88)], [], runtime)


class UnavailableVisionEngine:
    def __init__(self, backend: Literal["qnn", "cpu"], message: str) -> None:
        self.backend, self.name, self.message = backend, f"{backend}-unavailable", message

    def inspect(self, image_path: str) -> PerceptionResult:
        raise RuntimeError(self.message)


def provider_from_environment() -> VisionEngine | None:
    """Select explicitly; legacy variables remain supported for a smooth upgrade."""
    backend = os.getenv("VISION_BACKEND", "").strip().casefold()
    command, weights = os.getenv("BUILDMESH_QNN_COMMAND"), os.getenv("BUILDMESH_ULTRALYTICS_WEIGHTS")
    if backend == "qnn":
        return QnnVisionEngine(shlex.split(command)) if command else UnavailableVisionEngine("qnn", "VISION_BACKEND=qnn requires BUILDMESH_QNN_COMMAND for the local ONNX Runtime/QNN runner")
    if backend == "cpu":
        if not weights:
            return UnavailableVisionEngine("cpu", "VISION_BACKEND=cpu requires BUILDMESH_ULTRALYTICS_WEIGHTS")
        try:
            return CpuVisionEngine(weights)
        except RuntimeError as exc:
            return UnavailableVisionEngine("cpu", str(exc))
    if backend:
        return UnavailableVisionEngine("cpu", "VISION_BACKEND must be qnn or cpu")
    if command:
        return QnnVisionEngine(shlex.split(command))
    if weights:
        try:
            return CpuVisionEngine(weights)
        except RuntimeError as exc:
            return UnavailableVisionEngine("cpu", str(exc))
    return None
