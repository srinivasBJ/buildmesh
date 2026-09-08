"""Local Qualcomm QNN runner for BuildMesh's strict perception contract.

Run with ``python -m buildmesh.qnn_runner path\\to\\site.jpg`` on a supported
Windows Snapdragon environment. It deliberately writes only JSON to stdout; setup
and inference failures go to stderr so the parent service fails closed.
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Any

from .inference import SUPPORTED_LABELS, device_identity


# COCO classes emitted by the standard Ultralytics YOLO ONNX export. BuildMesh
# preserves only labels that its bounded construction interpretation understands.
COCO_LABELS = [
    "person", "bicycle", "car", "motorcycle", "airplane", "bus", "train", "truck", "boat", "traffic light",
    "fire hydrant", "stop sign", "parking meter", "bench", "bird", "cat", "dog", "horse", "sheep", "cow",
    "elephant", "bear", "zebra", "giraffe", "backpack", "umbrella", "handbag", "tie", "suitcase", "frisbee",
    "skis", "snowboard", "sports ball", "kite", "baseball bat", "baseball glove", "skateboard", "surfboard",
    "tennis racket", "bottle", "wine glass", "cup", "fork", "knife", "spoon", "bowl", "banana", "apple",
    "sandwich", "orange", "broccoli", "carrot", "hot dog", "pizza", "donut", "cake", "chair", "couch",
    "potted plant", "bed", "dining table", "toilet", "tv", "laptop", "mouse", "remote", "keyboard", "cell phone",
    "microwave", "oven", "toaster", "sink", "refrigerator", "book", "clock", "vase", "scissors", "teddy bear",
    "hair drier", "toothbrush",
]


def _env_int(name: str, default: int) -> int:
    value = os.getenv(name, str(default))
    try:
        parsed = int(value)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be an integer") from exc
    if not 32 <= parsed <= 4096:
        raise RuntimeError(f"{name} must be between 32 and 4096")
    return parsed


def _nms(boxes: Any, scores: Any, threshold: float = 0.45) -> list[int]:
    """Small NumPy NMS to keep QNN deployment free from an OpenCV dependency."""
    import numpy as np

    if not len(boxes):
        return []
    order, keep = scores.argsort()[::-1], []
    while order.size:
        index = int(order[0])
        keep.append(index)
        if order.size == 1:
            break
        rest = order[1:]
        x1, y1 = np.maximum(boxes[index, 0], boxes[rest, 0]), np.maximum(boxes[index, 1], boxes[rest, 1])
        x2, y2 = np.minimum(boxes[index, 2], boxes[rest, 2]), np.minimum(boxes[index, 3], boxes[rest, 3])
        intersection = np.maximum(0, x2 - x1) * np.maximum(0, y2 - y1)
        area_current = (boxes[index, 2] - boxes[index, 0]) * (boxes[index, 3] - boxes[index, 1])
        area_rest = (boxes[rest, 2] - boxes[rest, 0]) * (boxes[rest, 3] - boxes[rest, 1])
        order = rest[intersection / np.maximum(area_current + area_rest - intersection, 1e-9) <= threshold]
    return keep


def _preprocess(path: str, size: int) -> tuple[Any, tuple[int, int], float]:
    import numpy as np
    from PIL import Image

    started = time.perf_counter()
    image = Image.open(path).convert("RGB")
    original = image.size
    resized = image.resize((size, size))
    tensor = np.asarray(resized, dtype=np.float32).transpose(2, 0, 1)[None] / 255.0
    return tensor, original, (time.perf_counter() - started) * 1000


def _decode(output: Any, image_size: int, original: tuple[int, int], confidence: float) -> list[dict[str, Any]]:
    """Decode standard YOLO detect head shape [1, 84, anchors] or [1, anchors, 84]."""
    import numpy as np

    values = np.asarray(output).squeeze(0)
    if values.ndim != 2:
        raise RuntimeError("YOLO output must be a 2-D detection tensor")
    if values.shape[0] in {84, 85} or values.shape[0] > values.shape[1]:
        values = values.T
    if values.shape[1] < 84:
        raise RuntimeError("YOLO output has fewer than 80 class channels")
    xywh, class_scores = values[:, :4], values[:, 4:84]
    classes, scores = class_scores.argmax(axis=1), class_scores.max(axis=1)
    valid = scores >= confidence
    xywh, classes, scores = xywh[valid], classes[valid], scores[valid]
    if not len(scores):
        return []
    boxes = np.column_stack((xywh[:, 0] - xywh[:, 2] / 2, xywh[:, 1] - xywh[:, 3] / 2, xywh[:, 0] + xywh[:, 2] / 2, xywh[:, 1] + xywh[:, 3] / 2))
    scale_x, scale_y = original[0] / image_size, original[1] / image_size
    boxes[:, [0, 2]] *= scale_x
    boxes[:, [1, 3]] *= scale_y
    boxes[:, [0, 2]] = np.clip(boxes[:, [0, 2]], 0, original[0])
    boxes[:, [1, 3]] = np.clip(boxes[:, [1, 3]], 0, original[1])
    output_rows: list[dict[str, Any]] = []
    for index in _nms(boxes, scores):
        label = COCO_LABELS[int(classes[index])] if int(classes[index]) < len(COCO_LABELS) else ""
        if label not in SUPPORTED_LABELS:
            continue
        output_rows.append({"label": label, "confidence": round(float(scores[index]), 4), "bounding_box": [round(float(value), 2) for value in boxes[index].tolist()]})
    return output_rows


def run(image_path: str) -> dict[str, Any]:
    try:
        import numpy  # noqa: F401  # imported here to produce a clear local error
        import onnxruntime as ort
    except ImportError as exc:
        raise RuntimeError("install the BuildMesh snapdragon extra (onnxruntime-qnn, numpy, Pillow)") from exc
    model_path = os.getenv("BUILDMESH_QNN_MODEL")
    if not model_path or not Path(model_path).is_file():
        raise RuntimeError("BUILDMESH_QNN_MODEL must name a local Qualcomm AI Hub-exported ONNX model")
    qnn_backend = os.getenv("BUILDMESH_QNN_BACKEND_PATH", "QnnHtp.dll")
    session = ort.InferenceSession(model_path, providers=[("QNNExecutionProvider", {"backend_path": qnn_backend})])
    if "QNNExecutionProvider" not in session.get_providers():
        raise RuntimeError("QNNExecutionProvider was not activated; refusing CPU fallback for VISION_BACKEND=qnn")
    size, threshold = _env_int("BUILDMESH_QNN_INPUT_SIZE", 640), float(os.getenv("BUILDMESH_QNN_CONFIDENCE", "0.35"))
    if not 0 <= threshold <= 1:
        raise RuntimeError("BUILDMESH_QNN_CONFIDENCE must be between 0 and 1")
    tensor, original, preprocessing = _preprocess(image_path, size)
    started = time.perf_counter()
    outputs = session.run(None, {session.get_inputs()[0].name: tensor})
    inference = (time.perf_counter() - started) * 1000
    post_started = time.perf_counter()
    detections = _decode(outputs[0], size, original, threshold)
    postprocessing = (time.perf_counter() - post_started) * 1000
    total = preprocessing + inference + postprocessing
    return {
        "contract_version": 1,
        "runtime": {
            "backend": "qnn", "runtime": "onnxruntime", "execution_provider": "QNNExecutionProvider", "execution_target": "npu",
            "model": os.getenv("BUILDMESH_QNN_MODEL_ID", "YOLOv11-Detection"), "model_version": os.getenv("BUILDMESH_QNN_MODEL_VERSION", Path(model_path).name),
            "device_identity": device_identity(), "preprocessing_latency_ms": round(preprocessing, 4), "inference_latency_ms": round(inference, 4),
            "postprocessing_latency_ms": round(postprocessing, 4), "total_latency_ms": round(total, 4), "fallback_state": "none",
        },
        "detections": detections,
        # A detection export has no mask head. A segmentation export should use a
        # dedicated runner once selected; it must report this same schema.
        "segmentations": [],
    }


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("usage: python -m buildmesh.qnn_runner SITE_IMAGE")
    try:
        print(json.dumps(run(sys.argv[1]), separators=(",", ":")))
    except Exception as exc:
        print(f"BuildMesh QNN runner: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc


if __name__ == "__main__":
    main()
