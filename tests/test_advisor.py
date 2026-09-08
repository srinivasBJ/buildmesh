from pathlib import Path

import pytest

from buildmesh.advisor import LocalCommandAdvisor
from buildmesh.service import BuildMeshService


def test_project_questions_are_grounded_in_current_project_evidence(tmp_path: Path) -> None:
    service = BuildMeshService(tmp_path / "buildmesh.db")
    project = service.create_project("Question project")
    progress = service.progress_update(project["id"], "Foundation F-12", 85, 42, 35.5, "worker@example.com")

    answer = service.ask_project(project["id"], "What is the latest progress?")
    assert answer["mode"] == "deterministic-progress-query"
    assert answer["evidence_ids"] == [progress["id"]]
    assert "85" in answer["answer"]


def test_local_advisor_rejects_out_of_scope_citations(tmp_path: Path) -> None:
    service = BuildMeshService(tmp_path / "buildmesh.db")
    project = service.create_project("Advisor project")
    service.progress_update(project["id"], "Foundation F-12", 85, 42, 35.5, "worker@example.com")
    script = tmp_path / "bad_advisor.py"
    script.write_text("import json; print(json.dumps({'answer':'unsafe', 'evidence_ids':['made_up'], 'confidence':0.9}))")
    advisor = LocalCommandAdvisor(service.store, ["python3", str(script)])
    with pytest.raises(ValueError, match="out-of-scope"):
        advisor.answer(project["id"], "What changed?")
