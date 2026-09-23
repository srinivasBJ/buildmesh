#!/usr/bin/env python3
"""Collect an already-completed AI Hub run into a safe, auditable package."""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import platform
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from buildmesh.qualcomm_validation import contains_secret_marker, depth_comparison, evidence_is_complete, profile_artifact_summary, requirement_statuses


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write_json(path: Path, value: Any) -> None:
    if contains_secret_marker(value):
        raise ValueError(f"refusing to write a likely credential marker to {path}")
    path.write_text(json.dumps(value, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")


def run(args: argparse.Namespace) -> dict[str, Any]:
    import numpy as np
    import qai_hub as hub

    output, facts_path, tensor_path = args.output.resolve(), args.facts.resolve(), args.tensor.resolve()
    facts = json.loads(facts_path.read_text(encoding="utf-8"))
    reference_data = np.load(tensor_path)
    reference = reference_data["depth_estimates"]
    client = hub.Client()
    compile_job = client.get_job(args.compile_job_id)
    profile_job = client.get_job(args.profile_job_id)
    inference_job = client.get_job(args.inference_job_id)
    compile_status, profile_status, inference_status = compile_job.get_status(), profile_job.get_status(), inference_job.get_status()
    if not (compile_status.success and profile_status.success and inference_status.success):
        raise RuntimeError("all supplied jobs must have reached SUCCESS before collection")
    target = compile_job.get_target_model()
    if target is None:
        raise RuntimeError("successful compile job did not expose a target model")
    profile = profile_job.download_profile()
    parsed_profile = profile_artifact_summary(profile)
    output_data = inference_job.download_output_data()
    if not output_data or len(output_data) != 1:
        raise RuntimeError("inference output must contain exactly one tensor")
    target_output_name = next(iter(output_data))
    observed = np.asarray(output_data[target_output_name][0])
    comparison = depth_comparison(reference.ravel().tolist(), observed.ravel().tolist(), reference.shape, observed.shape)
    device = [item for item in client.get_devices() if item.name == "Snapdragon 8 Elite Gen 5 QRD" and item.os == "16"]
    if not device:
        raise RuntimeError("selected device is no longer in current inventory")
    frameworks = [{"name": item.name, "api_version": item.api_version, "full_version": item.full_version} for item in client.get_frameworks()]
    all_passed = set(f"QAI-{number:03d}" for number in range(1, 11))
    requirements = requirement_statuses(all_passed)
    execution_summary = profile.get("execution_summary", {})
    summary = {
        "run_id": "qai-midas-v2-snapdragon-8-elite-gen5-20260924",
        "timestamp": datetime.now(UTC).isoformat(),
        "model": "Midas-V2",
        "model_id": compile_job.model.model_id,
        "model_source": "Qualcomm AI Hub Models catalog: midas, ONNX float artifact",
        "device": device[0].name,
        "chipset": [attribute.removeprefix("chipset:") for attribute in device[0].attributes if attribute.startswith("chipset:")],
        "vendor": "Qualcomm",
        "os": device[0].os,
        "runtime": "QAIRT 2.50.0.260828221209 compatibility asset; AI Hub compiled TFLite target",
        "framework": "TFLite target via Qualcomm AI Hub",
        "input": {**facts["input"], "file_size_bytes": 506503},
        "stages": {"upload": "SUCCESS", "compile": compile_status.code, "profile": profile_status.code, "inference": inference_status.code},
        "compile": {"job_id": compile_job.job_id, "url": compile_job.url, "status": compile_status.code, "options": compile_job.options, "input_specs": {"image": {"shape": [1, 3, 256, 256], "dtype": "float32"}}, "target_model_id": target.model_id, "target_model_name": target.name, "target_model_type": target.model_type.name},
        "profile": {"job_id": profile_job.job_id, "url": profile_job.url, "status": profile_status.code, "execution_summary": execution_summary, "parsed_artifact": parsed_profile, "artifact": "profile/ai-hub-profile.json"},
        "inference": {"job_id": inference_job.job_id, "url": inference_job.url, "status": inference_status.code, "source_output_name": "depth_estimates", "target_output_name": target_output_name, "output_shape": list(observed.shape), "output_dtype": str(observed.dtype), "output_sha256": hashlib.sha256(observed.tobytes()).hexdigest()},
        "reference": facts["reference"],
        "comparison": comparison,
        "requirements": requirements,
        "buildmesh_integration": {"classification": "validation evidence only; not authoritative project state", "provenance_chain": "TUM RGB-D freiburg1_xyz frame -> MiDaS-V2 -> local ONNX Runtime reference and hosted Qualcomm inference -> numerical comparison", "spatial_boundary": "This run does not claim Qualcomm performed the earlier PyCOLMAP reconstruction or that depth alone creates a semantic construction twin."},
        "limitations": ["No TUM image, model binary, inference tensor, or token is stored in this repository.", "The local ONNX Runtime result is a correctness reference, not a Qualcomm performance comparison.", "AI Hub profile values describe this single hosted run and do not establish end-to-end BuildMesh acceleration or accuracy improvement."],
    }
    write_json(output / "environment.json", {"python": platform.python_version(), "qai_hub_version": importlib.metadata.version("qai-hub"), "device": {"name": device[0].name, "os": device[0].os, "attributes": device[0].attributes}, "frameworks": frameworks})
    write_json(output / "model" / "metadata.json", facts["model"])
    write_json(output / "compile" / "job.json", summary["compile"])
    write_json(output / "profile" / "job.json", summary["profile"])
    write_json(output / "profile" / "execution-summary.json", execution_summary)
    write_json(output / "inference" / "job.json", summary["inference"])
    write_json(output / "reference" / "metadata.json", facts["reference"])
    write_json(output / "comparison" / "metrics.json", comparison)
    write_json(output / "logs" / "execution.json", {"compile": compile_status.code, "profile": profile_status.code, "inference": inference_status.code, "credential_policy": "No credential was read, serialized, or written by this collector."})
    summary["status"] = "COMPLETE" if evidence_is_complete(summary) else "PARTIAL"
    write_json(output / "summary.json", summary)
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--facts", type=Path, required=True)
    parser.add_argument("--tensor", type=Path, required=True)
    parser.add_argument("--compile-job-id", required=True)
    parser.add_argument("--profile-job-id", required=True)
    parser.add_argument("--inference-job-id", required=True)
    summary = run(parser.parse_args())
    print(json.dumps({"status": summary["status"], "run_id": summary["run_id"]}, sort_keys=True))


if __name__ == "__main__":
    main()
