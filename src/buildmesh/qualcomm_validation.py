"""Offline-safe helpers for Qualcomm AI Hub validation evidence.

This module deliberately does not import ``qai_hub`` or any credential source.
The live runner imports the official SDK only at execution time; these helpers
make the generated evidence package testable on an ordinary development host.
"""
from __future__ import annotations

import math
import re
from collections.abc import Iterable, Sequence
from typing import Any


QAI_REQUIREMENTS = tuple(f"QAI-{number:03d}" for number in range(1, 11))
COMPARISON_POLICY = {
    "shape_must_match": True,
    "minimum_pearson_correlation": 0.999,
    "maximum_normalized_rmse": 0.01,
}


def depth_comparison(
    reference: Sequence[float],
    observed: Sequence[float],
    reference_shape: Sequence[int],
    observed_shape: Sequence[int],
) -> dict[str, Any]:
    """Compare two depth tensors without making a Qualcomm performance claim."""
    if not reference or not observed:
        raise ValueError("depth comparison requires non-empty tensors")
    if len(reference) != len(observed):
        raise ValueError("depth comparison requires tensors of equal length")
    ref = [float(value) for value in reference]
    got = [float(value) for value in observed]
    count = len(ref)
    mae = sum(abs(a - b) for a, b in zip(ref, got)) / count
    rmse = math.sqrt(sum((a - b) ** 2 for a, b in zip(ref, got)) / count)
    maximum_absolute_error = max(abs(a - b) for a, b in zip(ref, got))
    reference_range = max(ref) - min(ref)
    normalized_rmse = rmse / reference_range if reference_range else math.inf
    mean_reference = sum(ref) / count
    mean_observed = sum(got) / count
    covariance = sum((a - mean_reference) * (b - mean_observed) for a, b in zip(ref, got))
    reference_variance = sum((a - mean_reference) ** 2 for a in ref)
    observed_variance = sum((b - mean_observed) ** 2 for b in got)
    denominator = math.sqrt(reference_variance * observed_variance)
    correlation = covariance / denominator if denominator else 0.0
    shape_match = list(reference_shape) == list(observed_shape)
    passed = (
        shape_match
        and correlation >= COMPARISON_POLICY["minimum_pearson_correlation"]
        and normalized_rmse <= COMPARISON_POLICY["maximum_normalized_rmse"]
    )
    return {
        "policy": COMPARISON_POLICY,
        "reference_shape": list(reference_shape),
        "observed_shape": list(observed_shape),
        "shape_match": shape_match,
        "element_count": count,
        "mae": mae,
        "rmse": rmse,
        "normalized_rmse": normalized_rmse,
        "max_absolute_error": maximum_absolute_error,
        "pearson_correlation": correlation,
        "passed": passed,
    }


def requirement_statuses(passed: Iterable[str], failures: dict[str, str] | None = None) -> list[dict[str, str]]:
    """Return a complete, fixed QAI-001..QAI-010 status table."""
    passed_set = set(passed)
    failures = failures or {}
    return [
        {
            "id": requirement,
            "status": "PASS" if requirement in passed_set else "FAIL" if requirement in failures else "NOT_RUN",
            "detail": "completed with structured evidence" if requirement in passed_set else failures.get(requirement, "not run"),
        }
        for requirement in QAI_REQUIREMENTS
    ]


def profile_artifact_summary(profile: Any) -> dict[str, Any]:
    """Parse the portable portion of an AI Hub profile artifact defensively."""
    if not isinstance(profile, dict) or not isinstance(profile.get("execution_summary"), dict):
        raise ValueError("profile artifact must contain execution_summary")
    summary = profile["execution_summary"]
    required = ("estimated_inference_time", "first_load_time", "warm_load_time")
    if any(not isinstance(summary.get(field), int) or isinstance(summary[field], bool) or summary[field] < 0 for field in required):
        raise ValueError("profile artifact has invalid timing fields")
    detail = profile.get("execution_detail", [])
    if not isinstance(detail, list):
        raise ValueError("profile artifact execution_detail must be a list")
    return {
        "estimated_inference_time_us": summary["estimated_inference_time"],
        "first_load_time_us": summary["first_load_time"],
        "warm_load_time_us": summary["warm_load_time"],
        "operator_count": len(detail),
        "compute_units": sorted({item.get("compute_unit", "UNKNOWN") for item in detail if isinstance(item, dict)}),
    }


def provenance_record(input_metadata: dict[str, Any], model_metadata: dict[str, Any]) -> dict[str, str]:
    """Create a minimal evidence-only provenance record without project state."""
    source_url = input_metadata.get("source_url")
    checksum = input_metadata.get("sha256")
    model = model_metadata.get("name")
    if not all(isinstance(item, str) and item for item in (source_url, checksum, model)):
        raise ValueError("provenance requires input source URL/checksum and model name")
    return {
        "classification": "validation evidence only",
        "input_source_url": source_url,
        "input_sha256": checksum,
        "model": model,
        "chain": "input -> model -> reference and hosted inference -> comparison",
    }


_SECRET_PATTERN = re.compile(
    r"(?i)(?:api[_-]?key|access[_-]?token|bearer\s+|qai[_-]?hub[_-]?token|sk-[a-z0-9_-]{12,})"
)


def contains_secret_marker(value: Any) -> bool:
    """Conservatively reject likely credential-bearing strings before writing JSON."""
    if isinstance(value, str):
        return bool(_SECRET_PATTERN.search(value))
    if isinstance(value, dict):
        return any(contains_secret_marker(key) or contains_secret_marker(item) for key, item in value.items())
    if isinstance(value, (list, tuple)):
        return any(contains_secret_marker(item) for item in value)
    return False


def evidence_is_complete(summary: dict[str, Any]) -> bool:
    """A validation is complete only after all hosted stages and comparison pass."""
    stages = summary.get("stages", {})
    requirements = summary.get("requirements", [])
    return (
        all(stages.get(stage) == "SUCCESS" for stage in ("upload", "compile", "profile", "inference"))
        and bool(summary.get("comparison", {}).get("passed"))
        and len(requirements) == len(QAI_REQUIREMENTS)
        and all(item.get("status") == "PASS" for item in requirements)
        and not contains_secret_marker(summary)
    )
