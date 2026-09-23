from buildmesh.qualcomm_validation import (
    COMPARISON_POLICY,
    contains_secret_marker,
    depth_comparison,
    evidence_is_complete,
    profile_artifact_summary,
    provenance_record,
    requirement_statuses,
)


def test_qai_001_to_010_status_table_is_complete_and_offline():
    statuses = requirement_statuses({f"QAI-{value:03d}" for value in range(1, 11)})
    assert [item["id"] for item in statuses] == [f"QAI-{value:03d}" for value in range(1, 11)]
    assert {item["status"] for item in statuses} == {"PASS"}


def test_qai_numerical_comparison_requires_shape_and_predeclared_tolerances():
    passing = depth_comparison([0.0, 1.0, 2.0], [0.0, 1.0001, 2.0], [1, 1, 1, 3], [1, 1, 1, 3])
    mismatch = depth_comparison([0.0, 1.0, 2.0], [0.0, 1.0001, 2.0], [1, 1, 1, 3], [1, 1, 3, 1])
    assert passing["passed"] is True
    assert passing["policy"] == COMPARISON_POLICY
    assert mismatch["shape_match"] is False and mismatch["passed"] is False


def test_qai_secret_guard_and_completion_gate():
    requirements = requirement_statuses({f"QAI-{value:03d}" for value in range(1, 11)})
    summary = {
        "stages": {"upload": "SUCCESS", "compile": "SUCCESS", "profile": "SUCCESS", "inference": "SUCCESS"},
        "comparison": {"passed": True},
        "requirements": requirements,
    }
    assert evidence_is_complete(summary) is True
    assert contains_secret_marker({"token": "plain metadata"}) is False
    assert contains_secret_marker("Bearer abcdefghijklmnop") is True
    summary["note"] = "api_key=not-permitted"
    assert evidence_is_complete(summary) is False


def test_qai_profile_artifact_parsing_and_failure_diagnostics_are_offline():
    profile = {
        "execution_summary": {"estimated_inference_time": 1241, "first_load_time": 886925, "warm_load_time": 95899},
        "execution_detail": [{"compute_unit": "NPU"}, {"compute_unit": "NPU"}],
    }
    assert profile_artifact_summary(profile) == {
        "estimated_inference_time_us": 1241,
        "first_load_time_us": 886925,
        "warm_load_time_us": 95899,
        "operator_count": 2,
        "compute_units": ["NPU"],
    }
    failures = requirement_statuses(set(), {"QAI-004": "compile job failed"})
    assert next(item for item in failures if item["id"] == "QAI-004")["status"] == "FAIL"


def test_qai_provenance_is_evidence_only_and_requires_reproducibility_fields():
    record = provenance_record(
        {"source_url": "https://example.test/input", "sha256": "abc"},
        {"name": "Midas-V2"},
    )
    assert record["classification"] == "validation evidence only"
    assert record["chain"].endswith("comparison")
