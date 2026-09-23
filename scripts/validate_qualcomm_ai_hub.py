#!/usr/bin/env python3
"""Deliberately run the credential-free Qualcomm AI Hub evidence workflow.

The official SDK discovers its already-configured credentials itself. This
script neither accepts nor reads a token, and it writes only sanitized job and
model metadata to the requested evidence directory.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import platform
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from buildmesh.qualcomm_validation import contains_secret_marker, depth_comparison, evidence_is_complete, requirement_statuses


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write_json(path: Path, value: Any) -> None:
    if contains_secret_marker(value):
        raise ValueError(f"refusing to write a likely credential marker to {path.name}")
    path.write_text(json.dumps(value, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")


def safe_error(error: Exception) -> dict[str, str]:
    message = str(error)
    return {"type": type(error).__name__, "message": "redacted credential-like API error" if contains_secret_marker(message) else message}


def run(args: argparse.Namespace) -> dict[str, Any]:
    import numpy as np
    import onnxruntime as ort
    from PIL import Image
    import qai_hub as hub

    output = args.output.resolve()
    if output.exists() and any(output.iterdir()):
        raise ValueError("--output must be new or empty")
    for relative in ("model", "compile", "profile", "inference", "reference", "comparison", "logs"):
        (output / relative).mkdir(parents=True, exist_ok=True)
    model_dir, frame = args.model_dir.resolve(), args.frame.resolve()
    model_file, data_file = model_dir / "midas.onnx", model_dir / "midas.data"
    if not model_file.is_file() or not data_file.is_file() or not frame.is_file():
        raise ValueError("model directory must contain midas.onnx and midas.data, and --frame must exist")

    image = Image.open(frame).convert("RGB")
    source_dimensions = list(image.size)
    tensor = np.asarray(image.resize((256, 256), Image.Resampling.BILINEAR), dtype=np.float32).transpose(2, 0, 1)[None, ...] / 255.0
    reference = ort.InferenceSession(str(model_file), providers=["CPUExecutionProvider"]).run(["depth_estimates"], {"image": tensor})[0]
    client = hub.Client()
    device = hub.Device(args.device_name, args.device_os)
    matching = [item for item in client.get_devices() if item.name == args.device_name and item.os == args.device_os]
    if not matching:
        raise ValueError("requested device is not present in the current AI Hub inventory")
    frameworks = [{"name": item.name, "api_version": item.api_version, "full_version": item.full_version} for item in client.get_frameworks()]
    environment = {
        "python": platform.python_version(),
        "qai_hub_version": importlib.metadata.version("qai-hub"),
        "device": {"name": device.name, "os": device.os, "attributes": matching[0].attributes},
        "frameworks": frameworks,
    }
    model_metadata = {
        "catalog_id": "midas", "name": "Midas-V2", "source": "Qualcomm AI Hub Models MiDaS recipe", "license": "MIT",
        "model_sha256": sha256(model_file), "external_weights_sha256": sha256(data_file),
        "input": {"name": "image", "shape": [1, 3, 256, 256], "dtype": "float32", "range": [0.0, 1.0]},
        "output": {"name": "depth_estimates", "shape": [1, 1, 256, 256], "dtype": "float32"},
    }
    stages = {"upload": "NOT_RUN", "compile": "NOT_RUN", "profile": "NOT_RUN", "inference": "NOT_RUN"}
    failures: dict[str, str] = {}
    jobs: dict[str, Any] = {}
    try:
        uploaded = client.upload_model(str(model_dir), name="buildmesh-midas-v2-validation")
        stages["upload"] = "SUCCESS"
        compiled = client.submit_compile_job(uploaded, device=device, name="buildmesh-midas-v2-compile", input_specs={"image": ((1, 3, 256, 256), "float32")}, retry=False)
        compile_status = compiled.wait(timeout=args.timeout)
        jobs["compile"] = {"job_id": compiled.job_id, "url": compiled.url, "status": compile_status.code, "model_id": uploaded.model_id, "target_model_id": compiled.get_target_model().model_id if compile_status.success and compiled.get_target_model() else None, "options": compiled.options}
        stages["compile"] = compile_status.code
        if not compile_status.success:
            failures["QAI-004"] = compile_status.message or "compile job failed"
            raise RuntimeError("compile job did not succeed")
        target = compiled.get_target_model()
        assert target is not None
        profiled = client.submit_profile_job(target, device=device, name="buildmesh-midas-v2-profile", retry=False)
        profile_status = profiled.wait(timeout=args.timeout)
        profile_data = profiled.download_profile() if profile_status.success else {}
        jobs["profile"] = {"job_id": profiled.job_id, "url": profiled.url, "status": profile_status.code, "metrics": profile_data}
        stages["profile"] = profile_status.code
        if not profile_status.success:
            failures["QAI-005"] = profile_status.message or "profile job failed"
            raise RuntimeError("profile job did not succeed")
        inferred = client.submit_inference_job(target, device=device, inputs={"image": [tensor]}, name="buildmesh-midas-v2-inference", retry=False)
        inference_status = inferred.wait(timeout=args.timeout)
        output_data = inferred.download_output_data() if inference_status.success else None
        if not output_data:
            raise RuntimeError("inference job returned no output dataset")
        if len(output_data) != 1:
            raise RuntimeError("compiled target returned an unexpected multi-output schema")
        target_output_name = next(iter(output_data))
        observed = np.asarray(output_data[target_output_name][0])
        comparison = depth_comparison(reference.ravel().tolist(), observed.ravel().tolist(), reference.shape, observed.shape)
        jobs["inference"] = {"job_id": inferred.job_id, "url": inferred.url, "status": inference_status.code, "output": {"source_output_name": "depth_estimates", "target_output_name": target_output_name, "shape": list(observed.shape), "dtype": str(observed.dtype)}}
        stages["inference"] = inference_status.code
        if not inference_status.success or not comparison["passed"]:
            failures["QAI-006" if not inference_status.success else "QAI-007"] = inference_status.message or "numerical comparison failed"
    except Exception as error:
        jobs["failure"] = safe_error(error)
    passed = {"QAI-001", "QAI-002", "QAI-010"}
    if stages["upload"] == "SUCCESS": passed.add("QAI-003")
    if stages["compile"] == "SUCCESS": passed.add("QAI-004")
    if stages["profile"] == "SUCCESS": passed.update({"QAI-005", "QAI-008"})
    if stages["inference"] == "SUCCESS": passed.add("QAI-006")
    if "comparison" in locals() and comparison["passed"]: passed.add("QAI-007")
    if all(stages[item] == "SUCCESS" for item in stages): passed.add("QAI-009")
    requirements = requirement_statuses(passed, failures)
    summary = {
        "run_id": f"qai-midas-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}", "timestamp": datetime.now(UTC).isoformat(),
        "model": model_metadata["name"], "model_id": jobs.get("compile", {}).get("model_id"), "device": device.name, "os": device.os,
        "runtime": "AI Hub compiled target", "framework": "QAIRT selected by current AI Hub", "input": {"source_url": args.source_url, "frame": args.frame_id, "sha256": sha256(frame), "dimensions": source_dimensions, "file_size_bytes": frame.stat().st_size},
        "stages": stages, "compile": jobs.get("compile"), "profile": jobs.get("profile"), "inference": jobs.get("inference"),
        "comparison": locals().get("comparison", {"passed": False, "reason": jobs.get("failure")}), "requirements": requirements,
        "buildmesh_integration": {"classification": "validation evidence only", "chain": "TUM RGB frame -> MiDaS-V2 -> reference and hosted Qualcomm inference -> numerical comparison"},
        "limitations": ["No source image or tensor values are committed.", "Local reference timing is not a Qualcomm performance measurement.", "This depth result is not a semantic construction twin or a reconstruction claim."],
    }
    write_json(output / "environment.json", environment)
    write_json(output / "model" / "metadata.json", model_metadata)
    write_json(output / "compile" / "job.json", jobs.get("compile", {}))
    write_json(output / "profile" / "job.json", jobs.get("profile", {}))
    write_json(output / "inference" / "job.json", jobs.get("inference", {}))
    write_json(output / "reference" / "metadata.json", {"runtime": "onnxruntime", "version": ort.__version__, "preprocessing": "RGB; bilinear 256x256; NCHW float32; divide by 255.0", "output_shape": list(reference.shape), "output_dtype": str(reference.dtype)})
    write_json(output / "comparison" / "metrics.json", summary["comparison"])
    write_json(output / "logs" / "execution.json", {"stages": stages, "failure": jobs.get("failure")})
    summary["status"] = "COMPLETE" if evidence_is_complete(summary) else "PARTIAL"
    write_json(output / "summary.json", summary)
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-dir", type=Path, required=True)
    parser.add_argument("--frame", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--source-url", required=True)
    parser.add_argument("--frame-id", required=True)
    parser.add_argument("--device-name", default="Snapdragon 8 Elite Gen 5 QRD")
    parser.add_argument("--device-os", default="16")
    parser.add_argument("--timeout", type=int, default=1800)
    print(json.dumps({"status": run(parser.parse_args()).get("status")}, sort_keys=True))


if __name__ == "__main__":
    main()
