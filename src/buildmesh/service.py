from __future__ import annotations

from datetime import datetime
import json
from math import ceil
from pathlib import Path
from typing import Any, BinaryIO

from .agents import OpenMeshOrchestrator
from .advisor import DeterministicProjectAdvisor, LocalCommandAdvisor, ProjectAdvisor
from .connectors import ConnectorError, Notifier, OpenMeteoWeatherClient, SMTPNotifier, WeatherClient
from .hardware import hardware_metadata
from .inference import PerceptionResult, VisionProvider, device_identity, parse_observations, provider_from_environment
from .validation import qnn_readiness, validation_state
from . import twin
from .environment import historical_windows, normalize, score, solar
from .spatial import geometric_relationship, geometry_status, planned_nodes, validate_plan
from .spatial_capture import validate_capture
from .ifc import parse as parse_ifc
from .design_reality import reconcile as reconcile_design, compare_scope_hierarchy
from .ingestion import DocumentExtractor, LocalAssetStore
from .reporting import build_daily_report
from .storage import Store
from .types import Evidence, Recommendation, RecommendationStatus, Severity, TaskStatus


class BuildMeshService:
    def __init__(self, database: str = "buildmesh.db", asset_root: str | Path | None = None, weather_client: WeatherClient | None = None, vision_provider: VisionProvider | None = None, notifier: Notifier | None = None) -> None:
        self.store = Store(database)
        self.openmesh = OpenMeshOrchestrator(self.store, self.store.add_recommendation)
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

    def create_twin_entity(self, project_id: str, parent_id: str, kind: str, label: str, attributes: dict[str, Any] | None = None) -> dict[str, Any]:
        return twin.hierarchy(self.store, project_id, parent_id, kind, label, attributes)

    def create_twin_snapshot(self, project_id: str, label: str, captured_at: str, source_evidence_ids: list[str], observations: list[dict[str, Any]]) -> dict[str, Any]:
        return twin.create_snapshot(self.store, project_id, label, captured_at, source_evidence_ids, observations)

    def ingest_spatial_capture(self, project_id: str, payload: dict[str, Any], fail_at: str | None = None) -> dict[str, Any]:
        """Ingest a BuildMesh-normalized observed capture without changing planned state."""
        return self.store.ingest_spatial_capture_atomic(project_id, validate_capture(payload), fail_at)

    def inspect_spatial_capture(self, project_id: str, capture_id: str) -> dict[str, Any]:
        return self.store.spatial_capture(project_id, capture_id)

    def link_task_component(self, project_id: str, task_id: str, component_id: str, scope: str = "component") -> dict[str, Any]:
        """Record an explicit, bounded work-scope assertion; it never infers one from names."""
        task, component = self.store.get_node(task_id), self.store.get_node(component_id)
        if task["project_id"] != project_id or task["kind"] != "task":
            raise ValueError("task_id does not identify a project task")
        if component["project_id"] != project_id or component["kind"] != "component":
            raise ValueError("component_id does not identify a project component")
        if scope not in {"component", "zone", "project"}:
            raise ValueError("scope must be component, zone, or project")
        graph = self.store.graph(project_id)
        existing = next((edge for edge in graph["edges"] if edge["source_id"] == task_id and edge["target_id"] == component_id and edge["relation"] == "affects_component"), None)
        if existing:
            return existing
        edge = self.store.add_edge(project_id, task_id, component_id, "affects_component", {"scope": scope, "assertion": "explicit task scope; no spatial inference"})
        self.store.record_event(project_id, "task_component_linked", {"task_id": task_id, "component_id": component_id, "scope": scope}, edge["id"])
        return edge

    def import_spatial_plan(self, project_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Import a validated JSON plan atomically enough to avoid malformed trusted graph state."""
        entities = validate_plan(payload)  # validate all input before mutating the graph
        graph = self.store.graph(project_id)
        existing = {node["attributes"].get("spatial_id"): node for node in graph["nodes"] if node["attributes"].get("spatial_id")}
        if all(item["id"] in existing for item in entities):
            return {"status": "idempotent", "entity_ids": [existing[item["id"]]["id"] for item in entities], "source": payload["source"]}
        if any(item["id"] in existing for item in entities):
            raise ValueError("spatial import partially overlaps an existing plan")
        evidence = self.store.add_evidence(Evidence(project_id=project_id, kind="spatial_plan", source=f"spatial:{payload['source']['reference']}", payload={"source": payload["source"], "schema_version": payload["schema_version"], "fixture": bool(payload["source"].get("fixture", False))}, confidence=payload["source"].get("confidence")))
        created: dict[str, dict[str, Any]] = {}
        for item in entities:
            kind = "planned_component" if item["kind"] in {"component", "opening"} else f"spatial_{item['kind']}"
            node = self.store.add_node(project_id, kind, item["label"], {**item["attributes"], "spatial_id": item["id"], "spatial_kind": item["kind"], "orientation": item["orientation"], "geometry": item["geometry"], "planned_state": item["attributes"].get("expected_status", "PLANNED")})
            created[item["id"]] = node
            self.store.add_edge(project_id, evidence["graph_node_id"], node["id"], "evidence_for")
        root = self.store.project_root(project_id)
        for item in entities:
            parent = created[item["parent_id"]] if item["parent_id"] else root
            self.store.add_edge(project_id, created[item["id"]]["id"], parent["id"], "part_of")
        self.store.record_event(project_id, "spatial_plan_imported", {"source": payload["source"], "entity_count": len(created), "evidence_id": evidence["id"]}, evidence["graph_node_id"])
        return {"status": "imported", "evidence": evidence, "entity_ids": [created[item["id"]]["id"] for item in entities]}

    def spatial_semantics(self, project_id: str) -> dict[str, Any]:
        """Derive only bbox-supported relationships; reruns are idempotent."""
        graph = self.store.graph(project_id); nodes = [n for n in graph["nodes"] if n["kind"].startswith("spatial_") or n["kind"] == "planned_component"]
        existing = {(e["source_id"], e["target_id"], e["relation"]) for e in graph["edges"]}; derived = []
        for first in nodes:
            for second in nodes:
                if first["id"] == second["id"]: continue
                relation = geometric_relationship(first["attributes"].get("geometry"), second["attributes"].get("geometry"))
                if relation and (first["id"], second["id"], relation.lower()) not in existing:
                    derived.append(self.store.add_edge(project_id, first["id"], second["id"], relation.lower(), {"derivation_method": "GEOMETRIC_CONTAINMENT", "geometry_status": "VERIFIED"}))
        return {"project_id": project_id, "relationships_created": len(derived), "geometry_normalized_entity_count": sum(geometry_status(n["attributes"].get("geometry")) == "VERIFIED" for n in nodes), "geometry_unavailable_entity_count": sum(geometry_status(n["attributes"].get("geometry")) == "UNAVAILABLE" for n in nodes), "relationships": derived}

    def spatial_query(self, project_id: str, query: str, scope_id: str | None = None, component_type: str | None = None) -> dict[str, Any]:
        graph = self.store.graph(project_id); nodes = graph["nodes"]; edges = graph["edges"]
        if query.startswith("ifc-id="):
            global_id = query.removeprefix("ifc-id=")
            return {"query": query, "entities": [node for node in nodes if node["attributes"].get("ifc_global_id") == global_id]}
        if query == "components_in_room":
            if not scope_id: raise ValueError("scope_id is required")
            ids = {e["source_id"] for e in edges if e["target_id"] == scope_id and e["relation"] in {"within", "located_in"}}
            return {"query": query, "scope_id": scope_id, "components": [n for n in nodes if n["id"] in ids]}
        if query == "components_of_type":
            return {"query": query, "components": [n for n in nodes if n["kind"] == "planned_component" and n["attributes"].get("ifc_class") == component_type]}
        if query == "planned_not_observed":
            linked = {e["source_id"] for e in edges if e["relation"] == "corresponds_to"}
            return {"query": query, "components": [n for n in nodes if n["kind"] == "planned_component" and n["id"] not in linked], "claim": "not linked to observed counterpart; not physically missing"}
        raise ValueError("unsupported spatial query")

    def import_ifc(self, project_id: str, path: str | Path) -> dict[str, Any]:
        """Real IFC → IfcOpenShell → validated normalized plan. Parse/validate precede mutation."""
        parsed = parse_ifc(path)
        result = self.import_spatial_plan(project_id, {key: parsed[key] for key in ("schema_version", "source", "entities")})
        graph = self.store.graph(project_id); by_spatial = {node["attributes"].get("spatial_id"): node for node in graph["nodes"]}
        existing = {(edge["source_id"], edge["target_id"], edge["relation"]) for edge in graph["edges"]}; extracted = []
        for relation in parsed["relationships"]:
            source, target = by_spatial.get(relation["source_spatial_id"]), by_spatial.get(relation["target_spatial_id"])
            if source and target and (source["id"], target["id"], relation["relation"]) not in existing:
                extracted.append(self.store.add_edge(project_id, source["id"], target["id"], relation["relation"], {"derivation_method": relation["derivation_method"], "ifc_relation_class": relation["ifc_relation_class"]}))
        return {**result, "adapter": "IfcOpenShell", "ifc_schema": parsed["source"]["ifc_schema"], "unsupported_entity_classes": parsed["unsupported_entity_classes"], "file_size_bytes": parsed["file_size_bytes"], "entity_count": len(parsed["entities"]), "explicit_relationship_count": len(extracted)}

    def match_planned_observed(self, project_id: str, planned_id: str, observed_id: str, method: str, evidence_ids: list[str], confidence: float | None = None) -> dict[str, Any]:
        if method not in {"IDENTITY", "EVIDENCE", "SPATIAL", "UNKNOWN"}: raise ValueError("unsupported matching method")
        planned, observed = self.store.get_node(planned_id), self.store.get_node(observed_id)
        if planned["project_id"] != project_id or planned["kind"] != "planned_component" or observed["project_id"] != project_id or observed["kind"] != "component": raise ValueError("matching needs project planned and observed components")
        for evidence_id in evidence_ids:
            if self.store.get_evidence(evidence_id)["project_id"] != project_id: raise ValueError("matching evidence outside project")
        graph = self.store.graph(project_id); existing = next((edge for edge in graph["edges"] if edge["source_id"] == planned_id and edge["target_id"] == observed_id and edge["relation"] == "corresponds_to"), None)
        if existing: return {"status": "idempotent", "edge": existing}
        decision = "MATCHED" if method != "UNKNOWN" else "UNKNOWN"
        edge = self.store.add_edge(project_id, planned_id, observed_id, "corresponds_to", {"matching_method": method, "decision": decision, "evidence_ids": evidence_ids, "confidence": confidence, "epistemic_state": "VERIFIED" if method == "IDENTITY" else "NEEDS_REVIEW" if method == "UNKNOWN" else "INFERRED"})
        if method == "UNKNOWN":
            self.store.add_recommendation(Recommendation(project_id=project_id, title="Review ambiguous planned-to-observed correspondence", rationale="The supplied evidence does not establish a unique planned-to-observed component match. Review before changing trusted planned or as-built state.", severity=__import__("buildmesh.types", fromlist=["Severity"]).Severity.MEDIUM, evidence_ids=evidence_ids, proposed_task={"title": "Review spatial correspondence", "assignee_role": "site_engineer", "due_within_hours": 24, "requires_human_confirmation": True}))
        return {"status": "created", "edge": edge}

    def resolve_spatial_match(self, project_id: str, planned_id: str, snapshot_id: str, tolerance: float = 1.0, fail_after_decision: bool = False) -> dict[str, Any]:
        """Resolve a snapshot-scoped match from identity, then bounded spatial evidence; no caller decision."""
        planned = self.store.get_node(planned_id)
        if planned["project_id"] != project_id or planned["kind"] != "planned_component" or tolerance <= 0: raise ValueError("invalid planned component or tolerance")
        observations = twin.snapshot_observations(self.store, project_id, snapshot_id)
        snapshot = self.store.get_node(snapshot_id)
        graph = self.store.graph(project_id)
        planned_evidence_nodes = [e["source_id"] for e in graph["edges"] if e["target_id"] == planned_id and e["relation"] == "evidence_for"]
        graph_nodes = {node["id"]: node for node in graph["nodes"]}
        planned_evidence_ids = [graph_nodes[node_id]["attributes"].get("evidence_id") for node_id in planned_evidence_nodes if graph_nodes[node_id]["attributes"].get("evidence_id")]
        snapshot_sources = set(snapshot["attributes"].get("source_evidence_ids", []))
        planned_identity = planned["attributes"].get("ifc_global_id")
        candidates = []
        for observation in observations:
            observed = self.store.get_node(observation["payload"]["component_id"]); identity = observed["attributes"].get("ifc_global_id")
            method, features = None, {}
            if planned_identity and identity == planned_identity: method, features = "IDENTITY", {"ifc_global_id": planned_identity}
            elif snapshot_sources & set(planned_evidence_ids): method, features = "EVIDENCE", {"planned_evidence_ids": sorted(snapshot_sources & set(planned_evidence_ids)), "observation_evidence_id": observation["id"], "snapshot_id": snapshot_id}
            else:
                pg, og = planned["attributes"].get("geometry"), observed["attributes"].get("geometry")
                pp, op = planned["attributes"].get("placement", {}), observed["attributes"].get("placement", {})
                if planned["attributes"].get("ifc_class") == observed["attributes"].get("ifc_class") and all(isinstance(value, (int, float)) for value in (pp.get("x"), pp.get("y"), op.get("x"), op.get("y"))):
                    distance = ((float(pp["x"])-float(op["x"]))**2 + (float(pp["y"])-float(op["y"]))**2) ** .5
                    if distance <= tolerance: method, features = "SPATIAL", {"distance": round(distance, 4), "tolerance": tolerance, "geometry": "IFC placement", "component_type": planned["attributes"].get("ifc_class"), "planned_position": [pp["x"], pp["y"], pp.get("z")], "observed_position": [op["x"], op["y"], op.get("z")]}
                elif planned["attributes"].get("ifc_class") == observed["attributes"].get("ifc_class") and pg and og and pg.get("type") == og.get("type") == "bounding_box":
                    a, b = pg["coordinates"], og["coordinates"]; distance = ((float(a[0])-float(b[0]))**2 + (float(a[1])-float(b[1]))**2) ** .5
                    if distance <= tolerance: method, features = "SPATIAL", {"distance": round(distance, 4), "tolerance": tolerance, "geometry": "bounding_box", "component_type": planned["attributes"].get("ifc_class")}
            if method: candidates.append({"observed": observed, "observation": observation, "method": method, "features": features})
        if not observations: decision, method, selected = "NOT_OBSERVED", "UNKNOWN", []
        elif len(candidates) == 1: decision, method, selected = "MATCHED", candidates[0]["method"], candidates
        elif len(candidates) > 1: decision, method, selected = "CONFLICTING", candidates[0]["method"], candidates
        else: decision, method, selected = "UNKNOWN", "UNKNOWN", []
        existing = [e for e in graph["edges"] if e["source_id"] == planned_id and e["relation"] == "match_decision" and e["attributes"].get("snapshot_id") == snapshot_id]
        if existing: return {"status": "idempotent", "decision": existing[0]["attributes"].get("decision"), "edge": existing[0]}
        evidence_ids = [item["observation"]["id"] for item in selected]
        node_id = selected[0]["observed"]["id"] if len(selected) == 1 else planned_id
        attrs = {"decision": decision, "matching_method": method, "snapshot_id": snapshot_id, "planned_ifc_global_id": planned_identity, "planned_evidence_ids": planned_evidence_ids, "observation_evidence_ids": evidence_ids, "candidate_component_ids": [item["observed"]["id"] for item in selected], "features": [item["features"] for item in selected], "epistemic_state": "VERIFIED" if method == "IDENTITY" and decision == "MATCHED" else "INFERRED" if method in {"SPATIAL", "EVIDENCE"} and decision == "MATCHED" else "NEEDS_REVIEW"}
        review = None
        if decision == "CONFLICTING" and evidence_ids:
            title = "Review ambiguous planned-to-observed correspondence"
            if not self.store.has_recommendation_for_evidence(project_id, title, evidence_ids[0]): review = Recommendation(project_id=project_id, title=title, rationale="More than one observed component satisfies the bounded correspondence rule. BuildMesh did not select a winner.", severity=Severity.MEDIUM, evidence_ids=evidence_ids, proposed_task={"title": "Review spatial correspondence", "assignee_role": "site_engineer", "due_within_hours": 24, "requires_human_confirmation": True})
        persisted = self.store.add_match_decision_atomic(project_id, planned_id, node_id, attrs, review, fail_after_decision)
        return {"status": "created", "decision": decision, "method": method, **persisted}

    def spatial_status(self, project_id: str) -> dict[str, Any]:
        graph = self.store.graph(project_id)
        nodes = [node for node in graph["nodes"] if node["kind"].startswith("spatial_") or node["kind"] == "planned_component"]
        return {"project_id": project_id, "spatial_entities": [{"id": node["id"], "spatial_id": node["attributes"].get("spatial_id"), "kind": node["attributes"].get("spatial_kind"), "orientation": node["attributes"].get("orientation", "UNKNOWN")} for node in nodes], "count": len(nodes)}

    def link_planned_observed(self, project_id: str, planned_id: str, observed_component_id: str) -> dict[str, Any]:
        planned, observed = self.store.get_node(planned_id), self.store.get_node(observed_component_id)
        if planned["project_id"] != project_id or planned["kind"] != "planned_component" or observed["project_id"] != project_id or observed["kind"] != "component":
            raise ValueError("planned and observed components must be project-scoped components")
        return self.store.add_edge(project_id, planned_id, observed_component_id, "corresponds_to")

    def spatial_diff(self, project_id: str, planned_scope_id: str, snapshot_id: str) -> dict[str, Any]:
        graph = self.store.graph(project_id); planned = planned_nodes(graph, planned_scope_id)
        observations = {item["payload"]["component_id"]: item for item in twin.snapshot_observations(self.store, project_id, snapshot_id)}
        links = {edge["source_id"]: edge["target_id"] for edge in graph["edges"] if edge["relation"] == "corresponds_to"}
        differences = []
        for item in planned:
            observed_id, observation = links.get(item["id"]), observations.get(links.get(item["id"]))
            if not observed_id or not observation:
                differences.append({"planned_component_id": item["id"], "state": "NOT_OBSERVED", "evidence_ids": [], "claim": "absence of linked observation; not MISSING"})
            elif observation["payload"]["observed_state"] == "CONFLICTING":
                differences.append({"planned_component_id": item["id"], "observed_component_id": observed_id, "state": "CONFLICTING", "evidence_ids": [observation["id"]]})
            else:
                differences.append({"planned_component_id": item["id"], "observed_component_id": observed_id, "state": "MATCHED", "evidence_ids": [observation["id"]]})
        return {"planned_scope_id": planned_scope_id, "snapshot_id": snapshot_id, "differences": differences, "counts": {state: sum(item["state"] == state for item in differences) for state in {item["state"] for item in differences}}}

    def link_document_spatial(self, project_id: str, evidence_id: str, spatial_id: str, task_id: str | None = None) -> dict[str, Any]:
        evidence, spatial = self.store.get_evidence(evidence_id), self.store.get_node(spatial_id)
        if evidence["project_id"] != project_id or spatial["project_id"] != project_id:
            raise ValueError("document evidence and spatial entity must belong to project")
        evidence_node = evidence.get("graph_node_id") or self.store.find_graph_node(project_id, "evidence_id", evidence_id)
        if not evidence_node:
            raise ValueError("document evidence has no graph node")
        edge = self.store.add_edge(project_id, evidence_node if isinstance(evidence_node, str) else evidence_node["id"], spatial_id, "evidence_for")
        if task_id:
            task = self.store.get_node(task_id)
            if task["project_id"] != project_id or task["kind"] != "task":
                raise ValueError("task_id does not identify a project task")
            self.store.add_edge(project_id, spatial_id, task_id, "planned_for")
        return edge

    def link_environment_spatial(self, project_id: str, environment_evidence_id: str, spatial_id: str) -> dict[str, Any]:
        evidence, spatial = self.store.get_evidence(environment_evidence_id), self.store.get_node(spatial_id)
        if evidence["project_id"] != project_id or evidence["kind"] != "environmental_context" or spatial["project_id"] != project_id:
            raise ValueError("environment evidence and spatial scope must belong to project")
        evidence_node = evidence.get("graph_node_id") or self.store.find_graph_node(project_id, "evidence_id", environment_evidence_id)
        if not evidence_node:
            raise ValueError("environment evidence has no graph node")
        return self.store.add_edge(project_id, evidence_node if isinstance(evidence_node, str) else evidence_node["id"], spatial_id, "affects")

    def spatial_analyze(self, project_id: str, scope_id: str) -> dict[str, Any]:
        node = self.store.get_node(scope_id)
        if node["project_id"] != project_id:
            raise ValueError("spatial scope is outside project")
        graph = self.store.graph(project_id)
        context_nodes = {edge["source_id"] for edge in graph["edges"] if edge["relation"] == "affects" and edge["target_id"] == scope_id}
        contexts = [item for item in self.store.evidence(project_id) if (item.get("graph_node_id") or (self.store.find_graph_node(project_id, "evidence_id", item["id"]) or {}).get("id")) in context_nodes]
        orientation = node["attributes"].get("orientation", "UNKNOWN")
        contextual = "Orientation is UNKNOWN; no daylight or wind conclusion is made." if orientation == "UNKNOWN" else f"Potentially favorable contextual orientation: {orientation}. This is not engineering-certified performance."
        return {"scope_id": scope_id, "orientation": orientation, "environment_evidence_ids": [item["id"] for item in contexts], "contextual_note": contextual}

    def architectural_orientation_context(self, project_id: str, spatial_id: str) -> dict[str, Any]:
        """A bounded orientation + solar observation, not a daylight certification."""
        scope = self.store.get_node(spatial_id)
        if scope["project_id"] != project_id:
            raise ValueError("spatial entity is outside project")
        graph = self.store.graph(project_id)
        source_nodes = {edge["source_id"] for edge in graph["edges"] if edge["relation"] == "affects" and edge["target_id"] == spatial_id}
        evidence_by_node = {
            (self.store.find_graph_node(project_id, "evidence_id", item["id"]) or {}).get("id"): item
            for item in self.store.evidence(project_id)
        }
        solar = [evidence_by_node[node_id] for node_id in source_nodes if node_id in evidence_by_node and evidence_by_node[node_id]["kind"] == "environmental_context" and evidence_by_node[node_id]["payload"].get("kind") == "solar_context"]
        orientation = scope["attributes"].get("orientation")
        if not orientation or not solar:
            return {"spatial_entity_id": spatial_id, "orientation": orientation or "UNKNOWN", "environment_evidence_ids": [item["id"] for item in solar], "recommendation": None, "epistemic_state": "UNKNOWN", "non_certifying": True}
        eastward = str(orientation).upper() in {"E", "EAST", "SE", "SOUTHEAST"}
        return {"spatial_entity_id": spatial_id, "orientation": orientation, "environment_evidence_ids": [item["id"] for item in solar], "recommendation": "Potentially favorable morning-light orientation." if eastward else "Orientation is recorded; no favorable daylight conclusion is made for this bounded solar context.", "epistemic_state": "INFERRED", "non_certifying": True}

    def match_cross_domain_trace(self, project_id: str, planned_id: str, snapshot_id: str) -> dict[str, Any]:
        """Read persisted lineage only; it does not manufacture links or conclusions."""
        graph = self.store.graph(project_id)
        decision = next((edge for edge in graph["edges"] if edge["source_id"] == planned_id and edge["relation"] == "match_decision" and edge["attributes"].get("snapshot_id") == snapshot_id), None)
        if not decision: raise ValueError("no persisted match decision for planned component and snapshot")
        task_ids = [edge["target_id"] for edge in graph["edges"] if edge["source_id"] == planned_id and edge["relation"] == "planned_for"]
        environment = [edge["source_id"] for edge in graph["edges"] if edge["relation"] == "affects" and edge["target_id"] == planned_id]
        return {"planned_component_id": planned_id, "task_ids": task_ids, "snapshot_id": snapshot_id, "decision_edge_id": decision["id"], "decision": decision["attributes"]["decision"], "planned_evidence_ids": decision["attributes"]["planned_evidence_ids"], "observation_evidence_ids": decision["attributes"]["observation_evidence_ids"], "observed_component_ids": decision["attributes"]["candidate_component_ids"], "environment_evidence_node_ids": environment}

    def design_reality_analyze(self, project_id: str, planned_id: str, snapshot_id: str, tolerance: float = 0.15, fail_after_decision: bool = False) -> dict[str, Any]:
        graph = self.store.graph(project_id); planned = self.store.get_node(planned_id)
        match = next((edge["attributes"] for edge in graph["edges"] if edge["source_id"] == planned_id and edge["relation"] == "match_decision" and edge["attributes"].get("snapshot_id") == snapshot_id), None)
        observed_ids = (match or {}).get("candidate_component_ids", []); observed = self.store.get_node(observed_ids[0]) if len(observed_ids) == 1 else None
        tasks = [edge["target_id"] for edge in graph["edges"] if edge["source_id"] == planned_id and edge["relation"] == "planned_for"]
        environment = [edge["source_id"] for edge in graph["edges"] if edge["target_id"] == planned_id and edge["relation"] == "affects"]
        observation = next((e for e in self.store.evidence(project_id) if e["id"] in (match or {}).get("observation_evidence_ids", [])), None)
        planned_scope = next((edge["source_id"] for edge in graph["edges"] if edge["target_id"] == planned_id and edge["relation"] == "contains"), None)
        observed_scope = observed["attributes"].get("planned_scope_id") if observed else None
        result = reconcile_design(planned, observed, match, tasks, environment, tolerance, planned_scope, observed_scope)
        planned_hierarchy = planned["attributes"].get("scope_hierarchy")
        observed_hierarchy = observed["attributes"].get("scope_hierarchy") if observed else None
        if planned_hierarchy or observed_hierarchy:
            scope_result = compare_scope_hierarchy(planned_hierarchy, observed_hierarchy)
            result["scope_comparison"] = scope_result
            if scope_result["decision"] == "SCOPE_CONFLICT":
                result["state"] = "SCOPE_CONFLICT"; result["deviation"] = scope_result
                result["requires_review"] = True
        existing = next((edge for edge in graph["edges"] if edge["source_id"] == planned_id and edge["relation"] == "design_reconciliation" and edge["attributes"].get("snapshot_id") == snapshot_id), None)
        if existing: return {"project_id": project_id, "planned_component_id": planned_id, "snapshot_id": snapshot_id, "tolerance": tolerance, "status": "idempotent", **existing["attributes"]}
        result["trace"]["planned_scope_id"] = planned_scope; result["trace"]["observed_scope_id"] = observed_scope
        review = None
        if result["requires_review"] and result["trace"]["evidence_ids"]:
            title = "Review design-to-reality deviation"
            if not self.store.has_recommendation_for_evidence(project_id, title, result["trace"]["evidence_ids"][0]):
                review = Recommendation(project_id=project_id, title=title, rationale="Observed evidence indicates a design-to-reality conflict. Review the installation record; this is not an engineering certification.", severity=Severity.MEDIUM, evidence_ids=result["trace"]["evidence_ids"], proposed_task={"title": "Review design-to-reality deviation", "assignee_role": "site_engineer", "due_within_hours": 24, "requires_human_confirmation": True})
        record = {"snapshot_id": snapshot_id, "state": result["state"], "deviation": result["deviation"], "evidence_sufficiency": result["evidence_sufficiency"], "requires_review": result["requires_review"], "trace": result["trace"]}
        persisted = self.store.add_match_decision_atomic(project_id, planned_id, observed["id"] if observed else planned_id, record, review, fail_after_decision, relation="design_reconciliation")
        return {"project_id": project_id, "planned_component_id": planned_id, "snapshot_id": snapshot_id, "tolerance": tolerance, "status": "created", **result, **persisted}

    def compare_scope_hierarchy(self, planned: dict[str, Any], observed: dict[str, Any]) -> dict[str, Any]:
        return compare_scope_hierarchy(planned, observed)

    def design_reality_timeline(self, project_id: str, planned_id: str) -> dict[str, Any]:
        """Expose immutable, persisted reconciliation history for one component."""
        graph = self.store.graph(project_id); planned = self.store.get_node(planned_id)
        if planned["project_id"] != project_id or planned["kind"] != "planned_component":
            raise ValueError("planned_id must identify a project planned component")
        snapshots = {node["id"]: node for node in graph["nodes"] if node["kind"] == "snapshot"}
        entries = []
        for edge in graph["edges"]:
            if edge["source_id"] != planned_id or edge["relation"] != "design_reconciliation": continue
            attrs, snapshot = edge["attributes"], snapshots.get(edge["attributes"]["snapshot_id"])
            entries.append({"snapshot_id": attrs["snapshot_id"], "snapshot_label": snapshot["label"] if snapshot else None, "captured_at": (snapshot or {}).get("attributes", {}).get("captured_at"), "decision": attrs["trace"].get("match", {}).get("decision"), "state": attrs["state"], "deviation": attrs["deviation"], "evidence_ids": attrs["trace"].get("evidence_ids", []), "task_ids": attrs["trace"].get("task_ids", []), "review_required": attrs["requires_review"], "_created_at": edge["created_at"]})
        return {"project_id": project_id, "planned_component_id": planned_id, "timeline": [{key: value for key, value in item.items() if key != "_created_at"} for item in sorted(entries, key=lambda item: item["_created_at"])]}

    def twin_status(self, project_id: str, scope_id: str, snapshot_id: str) -> dict[str, Any]:
        return twin.completeness(self.store, project_id, scope_id, snapshot_id)

    def twin_diff(self, project_id: str, previous_snapshot_id: str, current_snapshot_id: str) -> list[dict[str, Any]]:
        return twin.twin_diff(self.store, project_id, previous_snapshot_id, current_snapshot_id)

    def twin_reconcile(self, project_id: str, snapshot_id: str) -> list[dict[str, Any]]:
        """Bounded reconciliation: conflicting observed state requests review; it never overwrites plans."""
        findings = []
        for observation in twin.snapshot_observations(self.store, project_id, snapshot_id):
            if observation["payload"]["observed_state"] != "CONFLICTING":
                continue
            component = self.store.get_node(observation["payload"]["component_id"])
            title = f"Review conflicting as-built state for {component['label']}"
            if self.store.has_recommendation_for_evidence(project_id, title, observation["id"]):
                continue
            from .types import Recommendation, Severity
            findings.append(self.store.add_recommendation(Recommendation(project_id=project_id, title=title, rationale="Observed as-built state is conflicting. Review its source evidence and planned state before recording a project change; BuildMesh has not resolved the conflict.", severity=Severity.HIGH, evidence_ids=[observation["id"]], proposed_task={"title": f"Verify as-built state: {component['label']}", "assignee_role": "site_engineer", "due_within_hours": 24, "requires_human_confirmation": True})))
        return findings

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

    def environmental_context(self, project_id: str, **context: Any) -> dict[str, Any]:
        payload = normalize(**context)
        location_key = f"{payload['geographic_scope']['latitude']:.6f},{payload['geographic_scope']['longitude']:.6f}"
        location = self.store.find_graph_node(project_id, "location_key", location_key)
        if not location:
            location = self.store.add_node(project_id, "location_context", f"Location {location_key}", {"location_key": location_key, **payload["geographic_scope"]})
            self.store.add_edge(project_id, self.store.project_root(project_id)["id"], location["id"], "contains")
        evidence = self.store.add_evidence(Evidence(project_id=project_id, kind="environmental_context", source=payload["source"], payload=payload, confidence=payload["confidence"]))
        self.store.add_edge(project_id, location["id"], evidence["graph_node_id"], "observed_at")
        return evidence

    def refresh_environment(self, project_id: str, horizon_hours: int = 48) -> dict[str, Any]:
        """Normalize live weather provider output; missing fields remain absent, never invented."""
        project = self.store.get_project(project_id)
        metadata = project["metadata"]
        if "latitude" not in metadata or "longitude" not in metadata:
            raise ValueError("project metadata must include latitude and longitude")
        raw = self.weather.forecast(float(metadata["latitude"]), float(metadata["longitude"]), horizon_hours)
        required = {"provider", "retrieved_at", "latitude", "longitude", "rain_probability", "peak_at", "precipitation_total_mm"}
        if not required <= set(raw):
            raise ConnectorError("weather provider response is incomplete for environmental normalization")
        return self.environmental_context(project_id, kind="weather_forecast", source=f"weather:{raw['provider']}", source_type="live", retrieved_at=raw["retrieved_at"], observed_at=raw["peak_at"], latitude=float(raw["latitude"]), longitude=float(raw["longitude"]), values={"rain_probability": float(raw["rain_probability"]), "precipitation_mm": float(raw["precipitation_total_mm"])}, units={"rain_probability": "probability", "precipitation_mm": "mm"}, confidence=0.65, valid_until=None)

    def unknown_environment(self, project_id: str, kind: str, latitude: float, longitude: float, reason: str) -> dict[str, Any]:
        return self.environmental_context(project_id, kind=kind, source="system:unavailable", source_type="manual", retrieved_at=datetime.now().astimezone().isoformat(), observed_at=datetime.now().astimezone().isoformat(), latitude=latitude, longitude=longitude, values={"summary": reason}, units={}, epistemic_state="UNKNOWN", confidence=None)

    def reconcile_environment(self, project_id: str) -> list[dict[str, Any]]:
        contexts = [item for item in self.store.evidence(project_id) if item["kind"] == "environmental_context"]
        base_evidence_ids = [item["id"] for item in contexts if item["payload"]["kind"] not in {"traffic_context", "local_event"}]
        created = []
        forecasts = [item for item in contexts if item["payload"]["kind"] == "weather_forecast"]
        observations = [item for item in contexts if item["payload"]["kind"] == "weather_observation"]
        for forecast in forecasts:
            for observed in observations:
                low = forecast["payload"]["values"].get("rain_probability", 1) < 0.2
                active = observed["payload"]["values"].get("precipitation_mm", 0) > 0
                if not (low and active):
                    continue
                source_ids = sorted([forecast["id"], observed["id"]])
                if any(item["kind"] == "environmental_conflict" and item["payload"].get("evidence_ids") == source_ids for item in self.store.evidence(project_id)):
                    continue
                conflict = self.store.add_evidence(Evidence(project_id=project_id, kind="environmental_conflict", source="system:environment-reconciliation", payload={"epistemic_state": "CONFLICTING", "evidence_ids": source_ids, "reason": "low precipitation forecast conflicts with active observed precipitation"}, confidence=None))
                created.append(conflict)
        return created

    def environment_status(self, project_id: str) -> dict[str, Any]:
        contexts = [item for item in self.store.evidence(project_id) if item["kind"] == "environmental_context"]
        return {"project_id": project_id, "contexts": [{"id": item["id"], "kind": item["payload"]["kind"], "source": item["payload"]["source"], "source_type": item["payload"]["source_type"], "epistemic_state": item["payload"]["epistemic_state"], "freshness": item["payload"]["freshness"]} for item in contexts], "live_count": sum(item["payload"]["source_type"] == "live" for item in contexts), "fixture_count": sum(item["payload"]["fixture"] for item in contexts)}

    def environment_plan(self, project_id: str, task_id: str) -> dict[str, Any]:
        task = self.store.get_node(task_id)
        if task["project_id"] != project_id or task["kind"] != "task":
            raise ValueError("task_id does not identify a project task")
        activity = task["attributes"].get("activity", "excavation")
        contexts = [item for item in self.store.evidence(project_id) if item["kind"] == "environmental_context"]
        return {"task_id": task_id, "task": task["label"], "affected_task": task_id, "environmental_score": score(activity, contexts), "evidence_ids": [item["id"] for item in contexts]}

    def environmental_solar(self, project_id: str, latitude: float, longitude: float, for_date: str) -> dict[str, Any]:
        values = solar(latitude, longitude, for_date)
        return self.environmental_context(project_id, kind="solar_context", source="calculated:solar-geometry", source_type="calculated", retrieved_at=f"{for_date}T00:00:00+00:00", observed_at=f"{for_date}T00:00:00+00:00", latitude=latitude, longitude=longitude, values=values, units={"daylight_hours": "hours"}, epistemic_state="INFERRED", confidence=0.7)

    def historical_climate_plan(self, project_id: str, candidates: list[dict[str, Any]]) -> dict[str, Any]:
        comparison = historical_windows(candidates)
        evidence = self.store.add_evidence(Evidence(project_id=project_id, kind="historical_climate_plan", source="historical:provided", payload={"classification": "HISTORICAL_NOT_FORECAST", "candidates": comparison}, confidence=None))
        return {"evidence": evidence, "candidates": comparison}

    def candidate_work_windows(self, project_id: str, task_id: str, windows: list[dict[str, Any]]) -> dict[str, Any]:
        plan = self.environment_plan(project_id, task_id)
        if not isinstance(windows, list) or not windows:
            raise ValueError("work windows are required")
        contexts = [item for item in self.store.evidence(project_id) if item["kind"] == "environmental_context"]
        base_evidence_ids = [item["id"] for item in contexts if item["payload"]["kind"] not in {"traffic_context", "local_event"}]
        ranked = []
        for item in windows:
            if not isinstance(item, dict) or set(item) != {"start", "end"} or not all(isinstance(item[key], str) and item[key] for key in item):
                raise ValueError("work window must contain start and end")
            traffic = [context for context in contexts if context["payload"]["kind"] == "traffic_context" and context["payload"]["values"].get("window_start") == item["start"] and context["payload"]["values"].get("window_end") == item["end"]]
            events = [context for context in contexts if context["payload"]["kind"] == "local_event" and context["payload"]["values"].get("window_start") == item["start"]]
            traffic_risk = max((context["payload"]["values"].get("congestion_index", 0) for context in traffic), default=None)
            event_risk = max((context["payload"]["values"].get("disruption_level", 0) for context in events), default=None)
            penalty = (30 * traffic_risk if traffic_risk is not None else 0) + (25 * event_risk if event_risk is not None else 0)
            matched_ids = [context["id"] for context in [*traffic, *events]]
            ranked.append({**item, "score": round(max(0, (plan["environmental_score"]["suitability_score"] or 0) - penalty), 1) if plan["environmental_score"]["suitability_score"] is not None else None, "evidence_ids": [*base_evidence_ids, *matched_ids], "factors": [*plan["environmental_score"]["factors"], {"factor": "traffic", "value": traffic_risk, "evidence_ids": [context["id"] for context in traffic], "effect": "risk" if traffic_risk and traffic_risk >= .6 else "unknown" if traffic_risk is None else "neutral"}, {"factor": "local_event", "value": event_risk, "evidence_ids": [context["id"] for context in events], "effect": "risk" if event_risk and event_risk >= .5 else "unknown" if event_risk is None else "neutral"}], "assumptions": ["traffic/event values apply only when their exact fixture or sourced interval matches candidate window"]})
        return {"task_id": task_id, "ranked_windows": ranked, "formula": "current environmental suitability score; no predicted time-of-day variation", "evidence_ids": plan["evidence_ids"]}

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
        prior = [event for event in self.store.events(recommendation["project_id"]) if event["kind"] == "review_notification" and event["payload"].get("recommendation_id") == recommendation_id and event["payload"].get("recipient") == recipient]
        if prior:
            return {**prior[0]["payload"], "status": "idempotent"}
        if not self.notifier:
            result = {"status": "not_configured", "channel": "smtp", "recipient": recipient}
        else:
            result = self.notifier.send(recipient, f"BuildMesh review: {recommendation['title']}", f"A {recommendation['severity']} BuildMesh recommendation requires review.\n\n{recommendation['rationale']}")
        graph = self.store.graph(recommendation["project_id"])
        recommendation_node = next((node for node in graph["nodes"] if node["kind"] == "recommendation" and node["attributes"].get("recommendation_id") == recommendation_id), None)
        action = next((edge for edge in graph["edges"] if recommendation_node and edge["source_id"] == recommendation_node["id"] and edge["relation"] == "approved_action"), None)
        payload = {"recommendation_id": recommendation_id, "project_id": recommendation["project_id"], "recipient": recipient, "evidence_ids": recommendation["evidence_ids"], "proposed_task": recommendation["proposed_task"], "action_task_id": action["target_id"] if action else None, "fixture": result.get("mode") == "fixture", **result}
        self.store.record_event(recommendation["project_id"], "review_notification", payload, recommendation_id)
        return payload

    def recovery_options(self, project_id: str, task_id: str) -> dict[str, Any]:
        """Generate reversible schedule alternatives; never select one automatically."""
        task = self.store.get_node(task_id)
        if task["project_id"] != project_id or task["kind"] != "task": raise ValueError("task is outside project")
        graph = self.store.graph(project_id); prerequisites = [edge["target_id"] for edge in graph["edges"] if edge["relation"] == "depends_on" and edge["source_id"] == task_id]; downstream = [edge["source_id"] for edge in graph["edges"] if edge["relation"] == "depends_on" and edge["target_id"] == task_id]
        affected = [task_id, *downstream]
        options = [("RESEQUENCE", affected, "reduce waiting time", "MEDIUM"), ("SHIFT_WINDOW", [task_id], "recover one work window", "HIGH"), ("COMPLETE_PREREQUISITE", [*prerequisites, task_id], "remove dependency block", "LOW"), ("MITIGATE_AND_REVIEW", [task_id], "preserve current plan pending review", "HIGH")]
        return {"project_id": project_id, "task_id": task_id, "options": [{"id": ident, "affected_task_ids": ids, "assumptions": ["site conditions remain as supplied"], "dependencies": prerequisites if ident != "SHIFT_WINDOW" else [], "expected_schedule_effect": effect, "uncertainty": uncertainty} for ident, ids, effect, uncertainty in options], "selected": None, "requires_human_approval": True}

    def operational_timeline(self, recommendation_id: str) -> dict[str, Any]:
        """Derive a linked operational chain from persisted records, never display-only state."""
        recommendation = self.store.get_recommendation(recommendation_id); project_id = recommendation["project_id"]
        graph = self.store.graph(project_id); events = self.store.events(project_id)
        recommendation_node = next(node for node in graph["nodes"] if node["kind"] == "recommendation" and node["attributes"].get("recommendation_id") == recommendation_id)
        action = next((edge for edge in graph["edges"] if edge["source_id"] == recommendation_node["id"] and edge["relation"] == "approved_action"), None)
        action_id = action["target_id"] if action else None
        related = [event for event in events if event["subject_id"] in {recommendation_id, action_id} or event["payload"].get("recommendation_id") == recommendation_id]
        evidence = [self.store.get_evidence(evidence_id) for evidence_id in recommendation["evidence_ids"]]
        source_events = [event for event in events if event["subject_id"] in set(recommendation["evidence_ids"])]
        state_evidence = []
        if action_id:
            evidence_nodes = {node["id"]: node for node in graph["nodes"] if node["kind"] == "evidence"}
            for edge in graph["edges"]:
                if edge["relation"] == "states" and edge["target_id"] == action_id and edge["source_id"] in evidence_nodes:
                    state_evidence.append(self.store.get_evidence(evidence_nodes[edge["source_id"]]["attributes"]["evidence_id"]))
        return {"project_id": project_id, "recommendation_id": recommendation_id, "evidence_ids": recommendation["evidence_ids"], "observations": evidence, "source_events": source_events, "agent_runs": [run for run in self.store.agent_runs(project_id) if run["agent_name"] in {"risk-agent", "recommendation-agent"}], "recommendation": recommendation, "action_task_id": action_id, "action": self.store.get_node(action_id) if action_id else None, "state_evidence": state_evidence, "events": related}

    def approve(self, recommendation_id: str, reviewer: str, decision: str, comment: str | None = None) -> dict[str, Any]:
        return self.store.review_recommendation(recommendation_id, reviewer, RecommendationStatus(decision), comment)
