from __future__ import annotations

from datetime import datetime
from math import ceil
from pathlib import Path
from typing import Any, BinaryIO

from .agents import OpenMeshOrchestrator
from .advisor import DeterministicProjectAdvisor, LocalCommandAdvisor, ProjectAdvisor
from .connectors import ConnectorError, Notifier, OpenMeteoWeatherClient, SMTPNotifier, WeatherClient
from .hardware import hardware_metadata
from .inference import PerceptionResult, VisionProvider, device_identity, parse_observations, provider_from_environment
from .validation import qnn_readiness, validation_state
from .ingestion import DocumentExtractor, LocalAssetStore
from .reporting import build_daily_report
from .storage import Store
from .types import Evidence, RecommendationStatus, TaskStatus


class BuildMeshService:
    def __init__(self, database: str = "buildmesh.db", asset_root: str | Path | None = None, weather_client: WeatherClient | None = None, vision_provider: VisionProvider | None = None, notifier: Notifier | None = None) -> None:
        self.store = Store(database)
        self.openmesh = OpenMeshOrchestrator(self.store)
        self.assets = LocalAssetStore(asset_root or Path(database).parent / "data")
        self.documents = DocumentExtractor()
        self.weather = weather_client or OpenMeteoWeatherClient()
        self.vision = vision_provider if vision_provider is not None else provider_from_environment()
        self.notifier = notifier or SMTPNotifier.from_environment()
        self.advisor: ProjectAdvisor = LocalCommandAdvisor.from_environment(self.store) or DeterministicProjectAdvisor(self.store)

    def create_project(self, name: str, location: str | None = None, metadata: dict[str, Any] | None = None) -> dict[str, Any]:
        return self.store.create_project(name, location, metadata)

    def create_task(self, project_id: str, title: str, attributes: dict[str, Any]) -> dict[str, Any]:
        task = self.store.add_node(project_id, "task", title, {**attributes, "status": TaskStatus.OPEN.value})
        self.store.add_edge(project_id, self.store.project_root(project_id)["id"], task["id"], "contains")
        self.store.add_evidence(Evidence(project_id=project_id, kind="task_state", source="system:task-created", payload={"task_id": task["id"], "previous_status": None, "status": TaskStatus.OPEN.value, "changed_by": "system", "reason": "task created"}, confidence=1.0), related_node_id=task["id"], relation="states")
        self.store.record_event(project_id, "task_created", {"task_node_id": task["id"], "status": TaskStatus.OPEN.value}, task["id"])
        return task

    def add_project_member(self, project_id: str, name: str, email: str, roles: list[str]) -> dict[str, Any]:
        if not isinstance(name, str) or not 2 <= len(name.strip()) <= 160:
            raise ValueError("member name must be between 2 and 160 characters")
        if not isinstance(email, str) or "@" not in email or len(email.strip()) > 320:
            raise ValueError("member email must be a valid short address")
        if not isinstance(roles, list) or not 1 <= len(roles) <= 12 or any(not isinstance(role, str) or not 2 <= len(role.strip()) <= 80 for role in roles):
            raise ValueError("member roles must contain between 1 and 12 short role names")
        normalized_email = email.strip().casefold()
        normalized_roles = sorted({role.strip().casefold() for role in roles})
        graph = self.store.graph(project_id)
        if any(node["kind"] == "project_member" and node["attributes"].get("email") == normalized_email for node in graph["nodes"]):
            raise ValueError("a member with this email already exists in the project")
        member = self.store.add_node(project_id, "project_member", name.strip(), {"email": normalized_email, "roles": normalized_roles, "active": True})
        self.store.add_edge(project_id, self.store.project_root(project_id)["id"], member["id"], "contains")
        self.store.record_event(project_id, "project_member_added", {"member_id": member["id"], "email": normalized_email, "roles": normalized_roles}, member["id"])
        return member

    def assign_task(self, project_id: str, task_id: str, member_id: str, assigned_by: str) -> dict[str, Any]:
        task, member = self.store.get_node(task_id), self.store.get_node(member_id)
        if task["project_id"] != project_id or task["kind"] != "task":
            raise ValueError("task_id does not identify a task in this project")
        if member["project_id"] != project_id or member["kind"] != "project_member" or not member["attributes"].get("active"):
            raise ValueError("member_id does not identify an active project member")
        if not isinstance(assigned_by, str) or not assigned_by.strip():
            raise ValueError("assigned_by must be a non-empty string")
        graph = self.store.graph(project_id)
        if any(edge["source_id"] == task_id and edge["target_id"] == member_id and edge["relation"] == "assigned_to" for edge in graph["edges"]):
            raise ValueError("this task is already assigned to this member")
        edge = self.store.add_edge(project_id, task_id, member_id, "assigned_to")
        self.store.record_event(project_id, "task_assigned", {"task_id": task_id, "member_id": member_id, "assigned_by": assigned_by.strip()}, edge["id"])
        return edge

    def recommendation_reviewer_candidates(self, recommendation_id: str) -> list[dict[str, Any]]:
        recommendation = self.store.get_recommendation(recommendation_id)
        proposed_task = recommendation["proposed_task"] or {}
        role = proposed_task.get("assignee_role")
        if not isinstance(role, str) or not role.strip():
            return []
        normalized_role = role.strip().casefold()
        graph = self.store.graph(recommendation["project_id"])
        return [
            {"id": node["id"], "name": node["label"], "email": node["attributes"]["email"], "roles": node["attributes"]["roles"]}
            for node in graph["nodes"]
            if node["kind"] == "project_member" and node["attributes"].get("active") and normalized_role in node["attributes"].get("roles", [])
        ]

    def _task_node_for_reference(self, project_id: str, task_ref: str) -> dict[str, Any] | None:
        """Resolve only an unambiguous project-local task reference before linking evidence."""
        graph = self.store.graph(project_id)
        matches = [node for node in graph["nodes"] if node["kind"] == "task" and (node["id"] == task_ref or node["label"] == task_ref or node["attributes"].get("external_ref") == task_ref)]
        if len(matches) > 1:
            raise ValueError("task_ref matches more than one task in this project")
        if matches:
            return matches[0]
        if task_ref.startswith("node_"):
            raise ValueError("task_ref does not identify a task in this project")
        return None

    def connect(self, project_id: str, source_id: str, target_id: str, relation: str, attributes: dict[str, Any] | None = None) -> dict[str, Any]:
        return self.store.add_edge(project_id, source_id, target_id, relation, attributes)

    def add_task_dependency(self, project_id: str, dependent_task_id: str, prerequisite_task_id: str) -> dict[str, Any]:
        dependent, prerequisite = self.store.get_node(dependent_task_id), self.store.get_node(prerequisite_task_id)
        if dependent["project_id"] != project_id or prerequisite["project_id"] != project_id or dependent["kind"] != "task" or prerequisite["kind"] != "task":
            raise ValueError("dependencies must connect two tasks in the same project")
        if dependent_task_id == prerequisite_task_id:
            raise ValueError("a task cannot depend on itself")
        graph = self.store.graph(project_id)
        dependency_edges = [edge for edge in graph["edges"] if edge["relation"] == "depends_on"]
        if any(edge["source_id"] == dependent_task_id and edge["target_id"] == prerequisite_task_id for edge in dependency_edges):
            raise ValueError("this task dependency already exists")
        adjacency: dict[str, list[str]] = {}
        for edge in dependency_edges:
            adjacency.setdefault(edge["source_id"], []).append(edge["target_id"])
        pending, visited = [prerequisite_task_id], set()
        while pending:
            current = pending.pop()
            if current == dependent_task_id:
                raise ValueError("task dependency would create a cycle")
            if current in visited:
                continue
            visited.add(current)
            pending.extend(adjacency.get(current, []))
        edge = self.store.add_edge(project_id, dependent_task_id, prerequisite_task_id, "depends_on")
        self.store.record_event(project_id, "task_dependency_created", {"dependent_task_id": dependent_task_id, "prerequisite_task_id": prerequisite_task_id}, edge["id"])
        return edge

    def set_task_status(self, project_id: str, task_id: str, status: str, changed_by: str, reason: str | None = None) -> dict[str, Any]:
        task = self.store.get_node(task_id)
        if task["project_id"] != project_id or task["kind"] != "task":
            raise ValueError("task_id does not identify a task in this project")
        if not isinstance(changed_by, str) or not changed_by.strip():
            raise ValueError("changed_by must be a non-empty string")
        if reason is not None and (not isinstance(reason, str) or len(reason.strip()) > 1000):
            raise ValueError("reason must be a short string when supplied")
        next_status = TaskStatus(status)
        previous_status = TaskStatus(task["attributes"].get("status", TaskStatus.OPEN.value))
        allowed = {
            TaskStatus.OPEN: {TaskStatus.OPEN, TaskStatus.IN_PROGRESS, TaskStatus.BLOCKED, TaskStatus.COMPLETED},
            TaskStatus.IN_PROGRESS: {TaskStatus.IN_PROGRESS, TaskStatus.BLOCKED, TaskStatus.COMPLETED},
            TaskStatus.BLOCKED: {TaskStatus.BLOCKED, TaskStatus.OPEN, TaskStatus.IN_PROGRESS, TaskStatus.COMPLETED},
            TaskStatus.COMPLETED: {TaskStatus.COMPLETED},
        }
        if next_status not in allowed[previous_status]:
            raise ValueError(f"cannot move a {previous_status.value} task to {next_status.value}")
        updated = self.store.update_node_attributes(task_id, {"status": next_status.value})
        state_evidence = self.store.add_evidence(Evidence(project_id=project_id, kind="task_state", source=f"task-status:{changed_by.strip()}", payload={"task_id": task_id, "previous_status": previous_status.value, "status": next_status.value, "changed_by": changed_by.strip(), "reason": reason.strip() if reason else None}, confidence=1.0), related_node_id=task_id, relation="states")
        self.store.record_event(project_id, "task_status_changed", {"task_id": task_id, "previous_status": previous_status.value, "status": next_status.value, "changed_by": changed_by.strip(), "reason": reason.strip() if reason else None, "evidence_id": state_evidence["id"]}, task_id)
        updated["state_evidence_id"] = state_evidence["id"]
        return updated

    def progress_update(self, project_id: str, task_ref: str, reported_percent: float, planned_quantity: float | None, completed_quantity: float | None, reporter: str, material_units: float | None = None) -> dict[str, Any]:
        task = self._task_node_for_reference(project_id, task_ref)
        evidence = Evidence(project_id=project_id, kind="worker_progress", source=f"worker:{reporter}", payload={"task_ref": task_ref, "reported_percent": reported_percent, "planned_quantity": planned_quantity, "completed_quantity": completed_quantity, "material_units": material_units, "reporter": reporter}, confidence=1.0)
        return self.store.add_evidence(evidence, related_node_id=task["id"] if task else None, relation="reports_on")

    def weather_context(self, project_id: str, source: str, rain_probability: float, hours_until: int, summary: str = "") -> dict[str, Any]:
        if not 0 <= rain_probability <= 1:
            raise ValueError("rain_probability must be between 0 and 1")
        evidence = Evidence(project_id=project_id, kind="weather_forecast", source=source, payload={"rain_probability": rain_probability, "hours_until": hours_until, "summary": summary})
        return self.store.add_evidence(evidence)

    def record_context(self, project_id: str, kind: str, source: str, payload: dict[str, Any], confidence: float | None = None) -> dict[str, Any]:
        if kind not in {"soil_condition", "cad_extract", "environment_context"}:
            raise ValueError("context kind is not supported")
        if confidence is not None and not 0 <= confidence <= 1:
            raise ValueError("confidence must be between 0 and 1")
        return self.store.add_evidence(Evidence(project_id=project_id, kind=kind, source=source, payload=payload, confidence=confidence))

    def traffic_context(self, project_id: str, source: str, windows: list[dict[str, Any]], confidence: float, observed_at: str) -> dict[str, Any]:
        if not isinstance(source, str) or not source.strip():
            raise ValueError("traffic source must be a non-empty string")
        if isinstance(confidence, bool) or not isinstance(confidence, (int, float)) or not 0 <= confidence <= 1:
            raise ValueError("traffic confidence must be between 0 and 1")
        if not isinstance(observed_at, str) or not observed_at.strip() or len(observed_at) > 80:
            raise ValueError("traffic observed_at must be a short non-empty string")
        if not isinstance(windows, list) or not 2 <= len(windows) <= 48:
            raise ValueError("traffic windows must contain between 2 and 48 observations")
        normalized: list[dict[str, Any]] = []
        seen_starts: set[str] = set()
        for window in windows:
            if not isinstance(window, dict) or set(window) != {"start_time", "end_time", "congestion_index", "sample_count"}:
                raise ValueError("traffic windows have an invalid schema")
            start, end = window["start_time"], window["end_time"]
            congestion, samples = window["congestion_index"], window["sample_count"]
            if not isinstance(start, str) or not isinstance(end, str):
                raise ValueError("traffic window times must be strings")
            try:
                start_value, end_value = datetime.strptime(start, "%H:%M"), datetime.strptime(end, "%H:%M")
            except ValueError as exc:
                raise ValueError("traffic window times must use HH:MM") from exc
            if start_value >= end_value or start in seen_starts:
                raise ValueError("traffic windows must have unique, increasing times")
            if isinstance(congestion, bool) or not isinstance(congestion, (int, float)) or not 0 <= congestion <= 1:
                raise ValueError("traffic congestion_index must be between 0 and 1")
            if isinstance(samples, bool) or not isinstance(samples, int) or not 1 <= samples <= 1_000_000:
                raise ValueError("traffic sample_count must be between 1 and 1000000")
            seen_starts.add(start)
            normalized.append({"start_time": start, "end_time": end, "congestion_index": round(float(congestion), 4), "sample_count": samples})
        return self.store.add_evidence(Evidence(project_id=project_id, kind="traffic_flow", source=source.strip(), payload={"observed_at": observed_at.strip(), "windows": normalized}, confidence=float(confidence)))

    def schedule_context(self, project_id: str, task_ref: str, planned_percent: float, days_remaining: int, source: str, current_phase: str | None = None) -> dict[str, Any]:
        if not isinstance(task_ref, str) or not task_ref.strip():
            raise ValueError("schedule task_ref must be a non-empty string")
        task = self._task_node_for_reference(project_id, task_ref)
        if not task:
            raise ValueError("schedule task_ref must identify a task in this project")
        if isinstance(planned_percent, bool) or not isinstance(planned_percent, (int, float)) or not 0 <= planned_percent <= 100:
            raise ValueError("planned_percent must be between 0 and 100")
        if isinstance(days_remaining, bool) or not isinstance(days_remaining, int) or not 0 <= days_remaining <= 3650:
            raise ValueError("days_remaining must be between 0 and 3650")
        if not isinstance(source, str) or not source.strip():
            raise ValueError("schedule source must be a non-empty string")
        if current_phase is not None and (not isinstance(current_phase, str) or len(current_phase.strip()) > 160):
            raise ValueError("current_phase must be a short string when supplied")
        payload = {"task_ref": task_ref, "planned_percent": float(planned_percent), "days_remaining": days_remaining, "current_phase": current_phase.strip() if current_phase else None}
        return self.store.add_evidence(Evidence(project_id=project_id, kind="schedule_context", source=source.strip(), payload=payload, confidence=1.0), related_node_id=task["id"], relation="plans")

    @staticmethod
    def _normalized_text(value: str) -> str:
        return " ".join(value.casefold().split())

    def record_plan_prerequisite(
        self,
        project_id: str,
        source_document_evidence_id: str,
        predecessor_task_ref: str,
        dependent_task_ref: str,
        evidence_quote: str,
    ) -> dict[str, Any]:
        if not isinstance(evidence_quote, str) or not 3 <= len(evidence_quote.strip()) <= 1000:
            raise ValueError("evidence_quote must be between 3 and 1000 characters")
        document = self.store.get_evidence(source_document_evidence_id)
        if document["project_id"] != project_id or document["kind"] != "document":
            raise ValueError("source_document_evidence_id must identify a project document")
        extracted_text = document["payload"].get("extracted_text")
        if not isinstance(extracted_text, str) or not extracted_text.strip():
            raise ValueError("source document has no extractable text")
        if self._normalized_text(evidence_quote) not in self._normalized_text(extracted_text):
            raise ValueError("evidence_quote does not occur in the source document")
        predecessor = self._task_node_for_reference(project_id, predecessor_task_ref)
        dependent = self._task_node_for_reference(project_id, dependent_task_ref)
        if not predecessor or not dependent:
            raise ValueError("plan prerequisite tasks must identify tasks in this project")
        if predecessor["id"] == dependent["id"]:
            raise ValueError("a plan prerequisite must connect two different tasks")
        graph = self.store.graph(project_id)
        if any(edge["relation"] == "planned_before" and edge["source_id"] == predecessor["id"] and edge["target_id"] == dependent["id"] for edge in graph["edges"]):
            raise ValueError("this documented prerequisite already exists")
        evidence = self.store.add_evidence(
            Evidence(
                project_id=project_id,
                kind="plan_prerequisite",
                source=f"document-prerequisite:{source_document_evidence_id}",
                payload={
                    "source_document_evidence_id": source_document_evidence_id,
                    "predecessor_task_id": predecessor["id"],
                    "dependent_task_id": dependent["id"],
                    "evidence_quote": evidence_quote.strip(),
                },
                confidence=1.0,
            ),
            related_node_id=predecessor["id"],
            relation="precedes",
            parent_evidence_id=source_document_evidence_id,
        )
        self.store.add_edge(project_id, evidence["graph_node_id"], dependent["id"], "constrains")
        edge = self.store.add_edge(project_id, predecessor["id"], dependent["id"], "planned_before", {"plan_prerequisite_evidence_id": evidence["id"]})
        self.store.record_event(project_id, "plan_prerequisite_recorded", {"plan_prerequisite_evidence_id": evidence["id"], "source_document_evidence_id": source_document_evidence_id, "predecessor_task_id": predecessor["id"], "dependent_task_id": dependent["id"], "planned_before_edge_id": edge["id"]}, evidence["id"])
        return evidence

    def refresh_weather(self, project_id: str, horizon_hours: int = 48) -> dict[str, Any]:
        project = self.store.get_project(project_id)
        metadata = project["metadata"]
        if "latitude" not in metadata or "longitude" not in metadata:
            raise ValueError("project metadata must include latitude and longitude for live weather refresh")
        forecast = self.weather.forecast(float(metadata["latitude"]), float(metadata["longitude"]), horizon_hours)
        return self.store.add_evidence(Evidence(project_id=project_id, kind="weather_forecast", source=f"weather:{forecast['provider']}", payload=forecast, confidence=0.65))

    def record_observations(self, project_id: str, source: str, observations: list[dict[str, Any]], model: str, parent_evidence_id: str | None = None, site_ref: str | None = None) -> dict[str, Any]:
        if not isinstance(model, str) or not model.strip():
            raise ValueError("observation model must be a non-empty string")
        if site_ref is not None and (not isinstance(site_ref, str) or not 2 <= len(site_ref.strip()) <= 160):
            raise ValueError("site_ref must be between 2 and 160 characters when supplied")
        validated = [observation.__dict__ for observation in parse_observations({"observations": observations})]
        evidence = Evidence(project_id=project_id, kind="site_observation", source=source, payload={"observations": validated, "model": model.strip(), "site_ref": site_ref.strip() if site_ref else None})
        return self.store.add_evidence(evidence, parent_evidence_id=parent_evidence_id)

    def record_perception(self, project_id: str, source_evidence_id: str, result: PerceptionResult, site_ref: str | None = None) -> dict[str, Any]:
        """Promote validated local perception to evidence, never to an engineering fact.

        The source image and exact runner metadata remain attached to the evidence
        node. Re-analyzing the same image with the same backend/model is idempotent
        so a retry does not create duplicate observations or recommendations.
        """
        source = self.store.get_evidence(source_evidence_id)
        if source["project_id"] != project_id or source["kind"] != "site_image":
            raise ValueError("source_evidence_id is not a site image for this project")
        payload = result.data()
        identity = {"source_evidence_id": source_evidence_id, "backend": result.runtime.backend, "model": result.runtime.model, "model_version": result.runtime.model_version}
        for existing in self.store.evidence(project_id):
            if existing["kind"] == "site_observation" and all(existing["payload"].get(key) == value for key, value in identity.items()):
                node = self.store.find_graph_node(project_id, "evidence_id", existing["id"])
                return {**existing, "graph_node_id": node["id"] if node else None}
        evidence_payload = {
            **payload,
            **identity,
            # Existing agents deliberately consume only observations. Raw model
            # detections and segmentation remain separately identifiable below.
            "observations": payload["detections"],
            "model": result.runtime.model,
            "site_ref": site_ref.strip() if site_ref else None,
            "interpretation": "perception only; requires human verification",
        }
        evidence = self.store.add_evidence(
            Evidence(project_id=project_id, kind="site_observation", source=f"inference:{result.runtime.backend}:{source_evidence_id}", payload=evidence_payload),
            parent_evidence_id=source_evidence_id,
        )
        related_task = self._task_node_for_reference(project_id, site_ref.strip()) if site_ref else None
        if related_task:
            self.store.add_edge(project_id, evidence["graph_node_id"], related_task["id"], "observed_at")
        self.store.record_event(project_id, "local_perception_recorded", {"source_evidence_id": source_evidence_id, "observation_evidence_id": evidence["id"], "backend": result.runtime.backend, "model": result.runtime.model, "object_count": payload["summary"]["object_count"], "segmentation_count": payload["summary"]["segmentation_count"], "related_task_id": related_task["id"] if related_task else None}, evidence["id"])
        return evidence

    def ingest_image(self, project_id: str, stream: BinaryIO, filename: str, media_type: str, site_ref: str | None = None) -> dict[str, Any]:
        if site_ref is not None and (not isinstance(site_ref, str) or not 2 <= len(site_ref.strip()) <= 160):
            raise ValueError("site_ref must be between 2 and 160 characters when supplied")
        asset = self.assets.save(project_id, stream, filename, media_type, "images")
        return self.store.add_evidence(Evidence(project_id=project_id, kind="site_image", source=f"local-asset:{asset.id}", payload={**asset.__dict__, "site_ref": site_ref.strip() if site_ref else None}, confidence=1.0))

    def analyze_image(self, project_id: str, evidence_id: str) -> dict[str, Any]:
        if not self.vision:
            raise RuntimeError("no local vision provider configured; set BUILDMESH_QNN_COMMAND or BUILDMESH_ULTRALYTICS_WEIGHTS")
        source = self.store.get_evidence(evidence_id)
        if source["project_id"] != project_id or source["kind"] != "site_image":
            raise ValueError("evidence is not a site image for this project")
        asset = source["payload"]
        result = self.vision.inspect(str(self.assets.path_for(asset["relative_path"])))
        return self.record_perception(project_id, evidence_id, result, asset.get("site_ref"))

    def edge_status(self) -> dict[str, Any]:
        """Machine-readable local state; Qualcomm reference data is never inserted here."""
        if not self.vision:
            return {"configured": False, "device_identity": device_identity(), "local_perception": False, "network_required_for_perception": False, "hardware": hardware_metadata()}
        return {"configured": True, "backend": self.vision.backend, "engine": self.vision.name, "device_identity": device_identity(), "local_perception": True, "network_required_for_perception": False, "power": "not measured", "hardware": hardware_metadata()}

    def qnn_readiness(self) -> dict[str, Any]:
        return qnn_readiness()

    def import_qualcomm_result(self, project_id: str, result: dict[str, Any]) -> dict[str, Any]:
        from .validation import parse_qualcomm_result

        imported = parse_qualcomm_result(result)
        evidence = self.store.add_evidence(Evidence(project_id=project_id, kind="qualcomm_reference", source="Qualcomm AI Hub", payload=imported, confidence=1.0))
        self.store.record_event(project_id, "qualcomm_hosted_result_imported", {"evidence_id": evidence["id"], "job_id": imported["job_id"], "measurement_origin": imported["measurement_origin"]}, evidence["id"])
        return evidence

    def benchmark_image(self, project_id: str, evidence_id: str, repetitions: int = 8) -> dict[str, Any]:
        if not self.vision:
            raise RuntimeError("no local vision provider configured")
        if not isinstance(repetitions, int) or not 3 <= repetitions <= 100:
            raise ValueError("benchmark repetitions must be between 3 and 100")
        source = self.store.get_evidence(evidence_id)
        if source["project_id"] != project_id or source["kind"] != "site_image":
            raise ValueError("evidence is not a site image for this project")
        path = str(self.assets.path_for(source["payload"]["relative_path"]))
        cold = self.vision.inspect(path)
        warmup = self.vision.inspect(path)
        runs = [self.vision.inspect(path) for _ in range(repetitions)]
        latencies = sorted(run.runtime.total_latency_ms for run in runs)
        p95_index = max(0, ceil(0.95 * len(latencies)) - 1)
        payload = {
            "source_evidence_id": evidence_id,
            "backend": cold.runtime.backend,
            "runtime": cold.runtime.runtime,
            "execution_provider": cold.runtime.execution_provider,
            "execution_target": cold.runtime.execution_target,
            "model": cold.runtime.model,
            "model_version": cold.runtime.model_version,
            "device_identity": cold.runtime.device_identity,
            "cold_start_ms": cold.runtime.total_latency_ms,
            "warm_up_ms": warmup.runtime.total_latency_ms,
            "repetitions": repetitions,
            "measured": {"p50_ms": latencies[(len(latencies) - 1) // 2], "p95_ms": latencies[p95_index], "mean_ms": round(sum(latencies) / len(latencies), 4), "throughput_fps": round(1000 / (sum(latencies) / len(latencies)), 4) if sum(latencies) else None, "memory": "not measured", "power": "not measured"},
            "qualcomm_reference": None,
        }
        evidence = self.store.add_evidence(Evidence(project_id=project_id, kind="perception_benchmark", source=f"benchmark:{self.vision.name}", payload=payload, confidence=1.0), parent_evidence_id=evidence_id)
        self.store.record_event(project_id, "perception_benchmark_recorded", {"benchmark_evidence_id": evidence["id"], "source_evidence_id": evidence_id, "backend": payload["backend"], "repetitions": repetitions}, evidence["id"])
        return evidence

    def ingest_document(self, project_id: str, stream: BinaryIO, filename: str, media_type: str) -> dict[str, Any]:
        asset = self.assets.save(project_id, stream, filename, media_type, "documents")
        extracted = self.documents.extract(self.assets.path_for(asset.relative_path), asset.media_type)
        return self.store.add_evidence(Evidence(project_id=project_id, kind="document", source=f"local-asset:{asset.id}", payload={**asset.__dict__, "extracted_text": extracted, "extracted_chars": len(extracted)}, confidence=1.0))

    def daily_report(self, project_id: str) -> dict[str, Any]:
        return build_daily_report(self.store, project_id)

    def ask_project(self, project_id: str, question: str) -> dict[str, Any]:
        answer = self.advisor.answer(project_id, question).data()
        self.store.record_event(project_id, "project_question_answered", {"question": question, "evidence_ids": answer["evidence_ids"], "mode": answer["mode"], "confidence": answer["confidence"]})
        return answer

    def notify_reviewer(self, recommendation_id: str, recipient: str) -> dict[str, Any]:
        recommendation = self.store.get_recommendation(recommendation_id)
        if not self.notifier:
            result = {"status": "not_configured", "channel": "smtp", "recipient": recipient}
        else:
            result = self.notifier.send(recipient, f"BuildMesh review: {recommendation['title']}", f"A {recommendation['severity']} BuildMesh recommendation requires review.\n\n{recommendation['rationale']}")
        self.store.record_event(recommendation["project_id"], "review_notification", {"recommendation_id": recommendation_id, **result}, recommendation_id)
        return result

    def approve(self, recommendation_id: str, reviewer: str, decision: str, comment: str | None = None) -> dict[str, Any]:
        return self.store.review_recommendation(recommendation_id, reviewer, RecommendationStatus(decision), comment)
