from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from .types import Evidence, Recommendation, RecommendationStatus, new_id, now


class NotFoundError(KeyError):
    pass


class Store:
    """Small, portable persistence layer for the BuildMesh project graph."""

    def __init__(self, database: str | Path = "buildmesh.db") -> None:
        self.database = str(database)
        self.initialize()

    @contextmanager
    def connection(self, immediate: bool = False) -> Iterator[sqlite3.Connection]:
        con = sqlite3.connect(self.database)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA foreign_keys = ON")
        try:
            if immediate:
                con.execute("BEGIN IMMEDIATE")
            yield con
            con.commit()
        except Exception:
            con.rollback()
            raise
        finally:
            con.close()

    def initialize(self) -> None:
        with self.connection() as con:
            con.executescript(
                """
                CREATE TABLE IF NOT EXISTS projects (
                    id TEXT PRIMARY KEY, name TEXT NOT NULL, location TEXT,
                    metadata_json TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS graph_nodes (
                    id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id),
                    kind TEXT NOT NULL, label TEXT NOT NULL, attributes_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS graph_edges (
                    id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id),
                    source_id TEXT NOT NULL REFERENCES graph_nodes(id),
                    target_id TEXT NOT NULL REFERENCES graph_nodes(id), relation TEXT NOT NULL,
                    attributes_json TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS evidence (
                    id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id),
                    kind TEXT NOT NULL, source TEXT NOT NULL, payload_json TEXT NOT NULL,
                    captured_at TEXT NOT NULL, confidence REAL
                );
                CREATE TABLE IF NOT EXISTS events (
                    id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id),
                    kind TEXT NOT NULL, subject_id TEXT, payload_json TEXT NOT NULL, created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS recommendations (
                    id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id),
                    title TEXT NOT NULL, rationale TEXT NOT NULL, severity TEXT NOT NULL,
                    evidence_ids_json TEXT NOT NULL, proposed_task_json TEXT, status TEXT NOT NULL,
                    created_at TEXT NOT NULL, reviewer TEXT, reviewed_at TEXT, review_comment TEXT
                );
                CREATE TABLE IF NOT EXISTS agent_runs (
                    id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id),
                    agent_name TEXT NOT NULL, input_hash TEXT NOT NULL, output_hash TEXT NOT NULL,
                    result_json TEXT NOT NULL, created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS recommendation_materializations (
                    recommendation_id TEXT PRIMARY KEY REFERENCES recommendations(id),
                    task_node_id TEXT NOT NULL UNIQUE REFERENCES graph_nodes(id),
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS escalations (
                    id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id),
                    recommendation_id TEXT NOT NULL REFERENCES recommendations(id), level TEXT NOT NULL,
                    evidence_ids_json TEXT NOT NULL, created_at TEXT NOT NULL, fixture INTEGER NOT NULL DEFAULT 0,
                    UNIQUE(project_id, recommendation_id, level)
                );
                CREATE TABLE IF NOT EXISTS spatial_captures (
                    project_id TEXT NOT NULL REFERENCES projects(id), capture_id TEXT NOT NULL,
                    input_hash TEXT NOT NULL, evidence_id TEXT NOT NULL REFERENCES evidence(id),
                    snapshot_id TEXT NOT NULL REFERENCES graph_nodes(id), scope_node_id TEXT NOT NULL REFERENCES graph_nodes(id),
                    entity_node_ids_json TEXT NOT NULL, created_at TEXT NOT NULL,
                    PRIMARY KEY(project_id, capture_id)
                );
                """
            )

    @staticmethod
    def _dump(value: Any) -> str:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)

    @staticmethod
    def _row(row: sqlite3.Row | None) -> dict[str, Any]:
        if row is None:
            raise NotFoundError("resource not found")
        value = dict(row)
        for key in [k for k in value if k.endswith("_json")]:
            raw = value.pop(key)
            value[key.removesuffix("_json")] = json.loads(raw) if raw is not None else None
        return value

    def create_project(self, name: str, location: str | None = None, metadata: dict[str, Any] | None = None) -> dict[str, Any]:
        project_id = new_id("project")
        root_id = new_id("node")
        created_at = now()
        with self.connection() as con:
            con.execute("INSERT INTO projects VALUES (?, ?, ?, ?, ?)", (project_id, name, location, self._dump(metadata or {}), created_at))
            con.execute("INSERT INTO graph_nodes VALUES (?, ?, ?, ?, ?, ?)", (root_id, project_id, "project", name, self._dump({"location": location}), created_at))
        return self.get_project(project_id)

    def get_project(self, project_id: str) -> dict[str, Any]:
        with self.connection() as con:
            return self._row(con.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone())

    def list_projects(self) -> list[dict[str, Any]]:
        with self.connection() as con:
            return [self._row(r) for r in con.execute("SELECT * FROM projects ORDER BY created_at DESC")]

    def add_node(self, project_id: str, kind: str, label: str, attributes: dict[str, Any] | None = None) -> dict[str, Any]:
        self.get_project(project_id)
        node = {"id": new_id("node"), "project_id": project_id, "kind": kind, "label": label, "attributes": attributes or {}, "created_at": now()}
        with self.connection() as con:
            con.execute("INSERT INTO graph_nodes VALUES (?, ?, ?, ?, ?, ?)", (node["id"], project_id, kind, label, self._dump(node["attributes"]), node["created_at"]))
        return node

    def get_node(self, node_id: str) -> dict[str, Any]:
        with self.connection() as con:
            return self._row(con.execute("SELECT * FROM graph_nodes WHERE id = ?", (node_id,)).fetchone())

    def update_node_attributes(self, node_id: str, attributes: dict[str, Any]) -> dict[str, Any]:
        node = self.get_node(node_id)
        if node["kind"] == "snapshot" and node["attributes"].get("immutable"):
            raise ValueError("immutable snapshots cannot be modified")
        merged = {**node["attributes"], **attributes}
        with self.connection() as con:
            con.execute("UPDATE graph_nodes SET attributes_json = ? WHERE id = ?", (self._dump(merged), node_id))
        return self.get_node(node_id)

    def project_root(self, project_id: str) -> dict[str, Any]:
        with self.connection() as con:
            return self._row(con.execute("SELECT * FROM graph_nodes WHERE project_id = ? AND kind = 'project' ORDER BY created_at LIMIT 1", (project_id,)).fetchone())

    def find_graph_node(self, project_id: str, attribute_name: str, attribute_value: str) -> dict[str, Any] | None:
        with self.connection() as con:
            rows = [self._row(row) for row in con.execute("SELECT * FROM graph_nodes WHERE project_id = ?", (project_id,))]
        return next((row for row in rows if row["attributes"].get(attribute_name) == attribute_value), None)

    def add_edge(self, project_id: str, source_id: str, target_id: str, relation: str, attributes: dict[str, Any] | None = None) -> dict[str, Any]:
        source, target = self.get_node(source_id), self.get_node(target_id)
        if source["project_id"] != project_id or target["project_id"] != project_id:
            raise ValueError("graph edges may only connect nodes in the same project")
        edge = {"id": new_id("edge"), "project_id": project_id, "source_id": source_id, "target_id": target_id, "relation": relation, "attributes": attributes or {}, "created_at": now()}
        with self.connection() as con:
            con.execute("INSERT INTO graph_edges VALUES (?, ?, ?, ?, ?, ?, ?)", (edge["id"], project_id, source_id, target_id, relation, self._dump(edge["attributes"]), edge["created_at"]))
        return edge

    def add_match_decision_atomic(self, project_id: str, source_id: str, target_id: str, attributes: dict[str, Any], review: Recommendation | None = None, fail_after_decision: bool = False, relation: str = "match_decision") -> dict[str, Any]:
        """Atomically persist a service-derived decision/reconciliation and review."""
        if relation not in {"match_decision", "design_reconciliation"}:
            raise ValueError("unsupported atomic decision relation")
        edge = {"id": new_id("edge"), "project_id": project_id, "source_id": source_id, "target_id": target_id, "relation": relation, "attributes": attributes, "created_at": now()}
        with self.connection(immediate=True) as con:
            for node_id in (source_id, target_id):
                node = con.execute("SELECT project_id FROM graph_nodes WHERE id = ?", (node_id,)).fetchone()
                if not node or node["project_id"] != project_id: raise ValueError("match nodes must belong to project")
            con.execute("INSERT INTO graph_edges VALUES (?, ?, ?, ?, ?, ?, ?)", (edge["id"], project_id, source_id, target_id, edge["relation"], self._dump(attributes), edge["created_at"]))
            if fail_after_decision: raise RuntimeError("injected decision transaction failure")
            recommendation = None
            if review:
                data = review.data()
                for evidence_id in data["evidence_ids"]:
                    item = con.execute("SELECT project_id FROM evidence WHERE id = ?", (evidence_id,)).fetchone()
                    if not item or item["project_id"] != project_id: raise ValueError("review evidence must belong to project")
                con.execute("INSERT INTO recommendations (id, project_id, title, rationale, severity, evidence_ids_json, proposed_task_json, status, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", (data["id"], project_id, data["title"], data["rationale"], data["severity"], self._dump(data["evidence_ids"]), self._dump(data["proposed_task"]) if data["proposed_task"] else None, data["status"], data["created_at"]))
                node_id = new_id("node"); root = con.execute("SELECT id FROM graph_nodes WHERE project_id = ? AND kind = 'project' ORDER BY created_at LIMIT 1", (project_id,)).fetchone()["id"]
                con.execute("INSERT INTO graph_nodes VALUES (?, ?, ?, ?, ?, ?)", (node_id, project_id, "recommendation", data["title"], self._dump({"recommendation_id": data["id"], "severity": data["severity"], "status": data["status"]}), data["created_at"]))
                con.execute("INSERT INTO graph_edges VALUES (?, ?, ?, ?, ?, ?, ?)", (new_id("edge"), project_id, root, node_id, "contains", self._dump({}), data["created_at"]))
                recommendation = data
            con.execute("INSERT INTO events VALUES (?, ?, ?, ?, ?, ?)", (new_id("event"), project_id, f"{relation}_recorded", edge["id"], self._dump({"edge_id": edge["id"], "review_id": recommendation["id"] if recommendation else None}), now()))
        return {"edge": edge, "recommendation": recommendation}

    def graph(self, project_id: str) -> dict[str, Any]:
        self.get_project(project_id)
        with self.connection() as con:
            nodes = [self._row(r) for r in con.execute("SELECT * FROM graph_nodes WHERE project_id = ? ORDER BY created_at", (project_id,))]
            edges = [self._row(r) for r in con.execute("SELECT * FROM graph_edges WHERE project_id = ? ORDER BY created_at", (project_id,))]
        return {"project": self.get_project(project_id), "nodes": nodes, "edges": edges}

    def record_event(self, project_id: str, kind: str, payload: dict[str, Any], subject_id: str | None = None) -> dict[str, Any]:
        event = {"id": new_id("event"), "project_id": project_id, "kind": kind, "subject_id": subject_id, "payload": payload, "created_at": now()}
        with self.connection() as con:
            con.execute("INSERT INTO events VALUES (?, ?, ?, ?, ?, ?)", (event["id"], project_id, kind, subject_id, self._dump(payload), event["created_at"]))
        return event

    def events(self, project_id: str) -> list[dict[str, Any]]:
        with self.connection() as con:
            return [self._row(r) for r in con.execute("SELECT * FROM events WHERE project_id = ? ORDER BY created_at DESC", (project_id,))]

    def add_evidence(
        self,
        evidence: Evidence,
        related_node_id: str | None = None,
        relation: str | None = None,
        parent_evidence_id: str | None = None,
    ) -> dict[str, Any]:
        self.get_project(evidence.project_id)
        related_node = self.get_node(related_node_id) if related_node_id else None
        if related_node and related_node["project_id"] != evidence.project_id:
            raise ValueError("evidence may only be linked to a node in the same project")
        parent_evidence_node = self.find_graph_node(evidence.project_id, "evidence_id", parent_evidence_id) if parent_evidence_id else None
        if parent_evidence_id and not parent_evidence_node:
            raise ValueError("parent evidence is not available in this project")
        with self.connection() as con:
            con.execute("INSERT INTO evidence VALUES (?, ?, ?, ?, ?, ?, ?)", (evidence.id, evidence.project_id, evidence.kind, evidence.source, self._dump(evidence.payload), evidence.captured_at, evidence.confidence))
        node = self.add_node(evidence.project_id, "evidence", f"{evidence.kind}: {evidence.source}", {"evidence_id": evidence.id, "kind": evidence.kind, "source": evidence.source, "confidence": evidence.confidence})
        self.add_edge(evidence.project_id, self.project_root(evidence.project_id)["id"], node["id"], "contains")
        if related_node:
            self.add_edge(evidence.project_id, node["id"], related_node["id"], relation or "relates_to")
        if parent_evidence_node:
            self.add_edge(evidence.project_id, parent_evidence_node["id"], node["id"], "derived")
        self.record_event(evidence.project_id, "evidence_recorded", {"evidence_id": evidence.id, "graph_node_id": node["id"], "kind": evidence.kind}, evidence.id)
        result = evidence.data()
        result["graph_node_id"] = node["id"]
        return result

    def evidence(self, project_id: str) -> list[dict[str, Any]]:
        with self.connection() as con:
            return [self._row(r) for r in con.execute("SELECT * FROM evidence WHERE project_id = ? ORDER BY captured_at DESC", (project_id,))]

    def get_evidence(self, evidence_id: str) -> dict[str, Any]:
        with self.connection() as con:
            return self._row(con.execute("SELECT * FROM evidence WHERE id = ?", (evidence_id,)).fetchone())

    def ingest_spatial_capture_atomic(self, project_id: str, capture: dict[str, Any], fail_at: str | None = None) -> dict[str, Any]:
        """Persist one validated spatial capture using the existing Store transaction layer."""
        if fail_at not in {None, "evidence", "snapshot", "entity", "graph", "final"}:
            raise ValueError("unsupported spatial capture failure point")
        capture_hash = hashlib.sha256(self._dump(capture).encode()).hexdigest()
        with self.connection(immediate=True) as con:
            self.get_project(project_id)
            existing = con.execute("SELECT * FROM spatial_captures WHERE project_id=? AND capture_id=?", (project_id, capture["capture_id"])).fetchone()
            if existing:
                record = self._row(existing)
                if record["input_hash"] != capture_hash:
                    raise ValueError("capture_id already exists with different content")
                return {"status": "idempotent", "capture_id": capture["capture_id"], "evidence_id": record["evidence_id"], "snapshot_id": record["snapshot_id"], "scope_id": record["scope_node_id"], "entity_ids": record["entity_node_ids"]}

            def node(kind: str, label: str, attributes: dict[str, Any]) -> dict[str, Any]:
                value = {"id": new_id("node"), "project_id": project_id, "kind": kind, "label": label, "attributes": attributes, "created_at": now()}
                con.execute("INSERT INTO graph_nodes VALUES (?, ?, ?, ?, ?, ?)", (value["id"], project_id, kind, label, self._dump(attributes), value["created_at"]))
                return value

            def edge(source_id: str, target_id: str, relation: str, attributes: dict[str, Any] | None = None) -> dict[str, Any]:
                value = {"id": new_id("edge"), "project_id": project_id, "source_id": source_id, "target_id": target_id, "relation": relation, "attributes": attributes or {}, "created_at": now()}
                con.execute("INSERT INTO graph_edges VALUES (?, ?, ?, ?, ?, ?, ?)", (value["id"], project_id, source_id, target_id, relation, self._dump(value["attributes"]), value["created_at"]))
                return value

            root = con.execute("SELECT id FROM graph_nodes WHERE project_id=? AND kind='project' ORDER BY created_at LIMIT 1", (project_id,)).fetchone()
            if root is None:
                raise ValueError("project root is missing")
            payload = {"capture_id": capture["capture_id"], "source": capture["source"], "captured_at": capture["captured_at"], "coordinate_frame": capture["coordinate_frame"], "scope": capture["scope"], "fixture": capture["source"]["fixture"]}
            evidence = {"id": new_id("evidence"), "project_id": project_id, "kind": "spatial_capture", "source": f"spatial-capture:{capture['source']['kind']}", "payload": payload, "captured_at": capture["captured_at"], "confidence": None}
            con.execute("INSERT INTO evidence VALUES (?, ?, ?, ?, ?, ?, ?)", (evidence["id"], project_id, evidence["kind"], evidence["source"], self._dump(payload), evidence["captured_at"], None))
            evidence_node = node("evidence", f"spatial_capture: {capture['capture_id']}", {"evidence_id": evidence["id"], "kind": "spatial_capture", "source": evidence["source"], "confidence": None})
            edge(root["id"], evidence_node["id"], "contains")
            con.execute("INSERT INTO events VALUES (?, ?, ?, ?, ?, ?)", (new_id("event"), project_id, "evidence_recorded", evidence["id"], self._dump({"evidence_id": evidence["id"], "graph_node_id": evidence_node["id"], "kind": "spatial_capture"}), now()))
            if fail_at == "evidence":
                raise RuntimeError("injected spatial capture evidence failure")

            parent_scope_id = capture["scope"]["parent_scope_id"] or root["id"]
            parent = con.execute("SELECT project_id FROM graph_nodes WHERE id=?", (parent_scope_id,)).fetchone()
            if not parent or parent["project_id"] != project_id:
                raise ValueError("capture scope parent is outside the project")
            scope = node(capture["scope"]["kind"], capture["scope"]["label"], {"capture_id": capture["capture_id"], "source_scope_id": capture["scope"]["source_scope_id"], "capture_evidence_id": evidence["id"], "fixture": capture["source"]["fixture"]})
            edge(parent_scope_id, scope["id"], "contains")
            edge(evidence_node["id"], scope["id"], "evidence_for")
            snapshot = node("snapshot", f"Capture {capture['capture_id']}", {"captured_at": capture["captured_at"], "source_evidence_ids": [evidence["id"]], "immutable": True, "observation_count": len(capture["entities"]), "capture_id": capture["capture_id"], "fixture": capture["source"]["fixture"]})
            edge(root["id"], snapshot["id"], "contains")
            edge(evidence_node["id"], snapshot["id"], "supports")
            if fail_at == "snapshot":
                raise RuntimeError("injected spatial capture snapshot failure")

            external_refs = {item["evidence_reference"] for item in capture["entities"] if item["evidence_reference"] != "capture"}
            for reference in external_refs:
                item = con.execute("SELECT project_id FROM evidence WHERE id=?", (reference,)).fetchone()
                if not item or item["project_id"] != project_id:
                    raise ValueError("entity evidence reference is unavailable in this project")
            entities: dict[str, dict[str, Any]] = {}
            for item in capture["entities"]:
                geometry = item["geometry"]
                attrs = {"capture_id": capture["capture_id"], "source_entity_id": item["source_entity_id"], "semantic_type": item["semantic_type"], "ifc_class": item["semantic_type"], "geometry": geometry, "placement": dict(zip(("x", "y", "z"), geometry["position"])), "dimensions": geometry["dimensions"], "orientation": geometry["orientation"], "parent_scope": item["parent_scope"], "capture_evidence_id": evidence["id"], "entity_evidence_id": evidence["id"] if item["evidence_reference"] == "capture" else item["evidence_reference"], "confidence": item.get("confidence"), "confidence_state": "SUPPLIED" if "confidence" in item else "UNKNOWN", "fixture": capture["source"]["fixture"]}
                if "material" in item:
                    attrs["material"] = item["material"]
                entities[item["source_entity_id"]] = node("component", item["semantic_type"], attrs)
            if fail_at == "entity":
                raise RuntimeError("injected spatial capture entity failure")

            containment: set[tuple[str, str]] = set()
            for item in capture["entities"]:
                child = entities[item["source_entity_id"]]["id"]
                parent_id = scope["id"] if item["parent_scope"] == capture["scope"]["source_scope_id"] else entities[item["parent_scope"]]["id"]
                containment.add((parent_id, child))
                for relationship in item["relationships"]:
                    containment.add((entities[item["source_entity_id"]]["id"], entities[relationship["target_source_entity_id"]]["id"]))
            for parent_id, child_id in sorted(containment):
                edge(parent_id, child_id, "contains", {"source": "explicit_capture_relationship"})
            evidence_nodes = [self._row(row) for row in con.execute("SELECT * FROM graph_nodes WHERE project_id=? AND kind='evidence'", (project_id,))]
            for item in capture["entities"]:
                component = entities[item["source_entity_id"]]
                entity_evidence_id = component["attributes"]["entity_evidence_id"]
                edge(evidence_node["id"], component["id"], "evidence_for")
                source_node = evidence_node if entity_evidence_id == evidence["id"] else next((node for node in evidence_nodes if node["attributes"].get("evidence_id") == entity_evidence_id), None)
                if source_node is None:
                    raise ValueError("entity evidence provenance node is missing")
                if source_node["id"] != evidence_node["id"]:
                    edge(source_node["id"], component["id"], "evidence_for")
                confidence = item.get("confidence", 0.0)
                epistemic = "VERIFIED" if "confidence" in item else "UNKNOWN"
                observation = {"id": new_id("evidence"), "project_id": project_id, "kind": "twin_observation", "source": f"snapshot:{snapshot['id']}", "payload": {"snapshot_id": snapshot["id"], "component_id": component["id"], "observed_state": "IN_PROGRESS", "confidence": confidence, "epistemic_state": epistemic, "zone_id": scope["id"] if scope["kind"] == "zone" else None, "fixture": capture["source"]["fixture"], "capture_evidence_id": evidence["id"], "source_entity_id": item["source_entity_id"]}, "captured_at": capture["captured_at"], "confidence": confidence}
                con.execute("INSERT INTO evidence VALUES (?, ?, ?, ?, ?, ?, ?)", (observation["id"], project_id, observation["kind"], observation["source"], self._dump(observation["payload"]), observation["captured_at"], observation["confidence"]))
                observation_node = node("evidence", f"twin_observation: {item['source_entity_id']}", {"evidence_id": observation["id"], "kind": "twin_observation", "source": observation["source"], "confidence": confidence})
                edge(root["id"], observation_node["id"], "contains")
                edge(snapshot["id"], observation_node["id"], "contains")
                edge(observation_node["id"], component["id"], "observes")
            if fail_at == "graph":
                raise RuntimeError("injected spatial capture graph failure")
            con.execute("INSERT INTO events VALUES (?, ?, ?, ?, ?, ?)", (new_id("event"), project_id, "twin_snapshot_created", snapshot["id"], self._dump({"snapshot_id": snapshot["id"], "source_evidence_ids": [evidence["id"]], "capture_id": capture["capture_id"]}), now()))
            if fail_at == "final":
                raise RuntimeError("injected spatial capture final failure")
            created_at = now()
            con.execute("INSERT INTO spatial_captures VALUES (?, ?, ?, ?, ?, ?, ?, ?)", (project_id, capture["capture_id"], capture_hash, evidence["id"], snapshot["id"], scope["id"], self._dump([item["id"] for item in entities.values()]), created_at))
            con.execute("INSERT INTO events VALUES (?, ?, ?, ?, ?, ?)", (new_id("event"), project_id, "spatial_capture_ingested", capture["capture_id"], self._dump({"capture_id": capture["capture_id"], "evidence_id": evidence["id"], "snapshot_id": snapshot["id"], "scope_id": scope["id"], "entity_count": len(entities), "fixture": capture["source"]["fixture"]}), created_at))
            return {"status": "created", "capture_id": capture["capture_id"], "evidence_id": evidence["id"], "snapshot_id": snapshot["id"], "scope_id": scope["id"], "entity_ids": [item["id"] for item in entities.values()]}

    def spatial_capture(self, project_id: str, capture_id: str) -> dict[str, Any]:
        with self.connection() as con:
            record = self._row(con.execute("SELECT * FROM spatial_captures WHERE project_id=? AND capture_id=?", (project_id, capture_id)).fetchone())
        return {"capture_id": capture_id, "capture": self.get_evidence(record["evidence_id"]), "snapshot": self.get_node(record["snapshot_id"]), "scope": self.get_node(record["scope_node_id"]), "entities": [self.get_node(node_id) for node_id in record["entity_node_ids"]], "fixture": self.get_evidence(record["evidence_id"])["payload"]["fixture"]}

    def _insert_recommendation(self, con: sqlite3.Connection, recommendation: Recommendation) -> dict[str, Any]:
        d = recommendation.data()
        evidence_ids = d["evidence_ids"]
        if not evidence_ids or len(evidence_ids) != len(set(evidence_ids)):
            raise ValueError("recommendations require unique supporting evidence")
        for evidence_id in evidence_ids:
            evidence = con.execute("SELECT project_id FROM evidence WHERE id = ?", (evidence_id,)).fetchone()
            if not evidence or evidence["project_id"] != d["project_id"]:
                raise ValueError("recommendation evidence must belong to the same project")
        con.execute("INSERT INTO recommendations (id, project_id, title, rationale, severity, evidence_ids_json, proposed_task_json, status, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", (d["id"], d["project_id"], d["title"], d["rationale"], d["severity"], self._dump(d["evidence_ids"]), self._dump(d["proposed_task"]) if d["proposed_task"] else None, d["status"], d["created_at"]))
        node_id = new_id("node")
        root = con.execute("SELECT id FROM graph_nodes WHERE project_id = ? AND kind = 'project' ORDER BY created_at LIMIT 1", (d["project_id"],)).fetchone()
        if root is None:
            raise ValueError("project root is missing")
        con.execute("INSERT INTO graph_nodes VALUES (?, ?, ?, ?, ?, ?)", (node_id, d["project_id"], "recommendation", d["title"], self._dump({"recommendation_id": d["id"], "severity": d["severity"], "status": d["status"]}), d["created_at"]))
        con.execute("INSERT INTO graph_edges VALUES (?, ?, ?, ?, ?, ?, ?)", (new_id("edge"), d["project_id"], root["id"], node_id, "contains", self._dump({}), d["created_at"]))
        evidence_nodes = [self._row(row) for row in con.execute("SELECT * FROM graph_nodes WHERE project_id = ? AND kind = 'evidence'", (d["project_id"],))]
        for evidence_id in d["evidence_ids"]:
            evidence_node = next((node for node in evidence_nodes if node["attributes"].get("evidence_id") == evidence_id), None)
            if evidence_node is None:
                raise ValueError("recommendation evidence provenance node is missing")
            con.execute("INSERT INTO graph_edges VALUES (?, ?, ?, ?, ?, ?, ?)", (new_id("edge"), d["project_id"], evidence_node["id"], node_id, "supports", self._dump({}), d["created_at"]))
        con.execute("INSERT INTO events VALUES (?, ?, ?, ?, ?, ?)", (new_id("event"), d["project_id"], "recommendation_created", d["id"], self._dump({"recommendation_id": d["id"], "graph_node_id": node_id, "severity": d["severity"], "evidence_ids": evidence_ids}), d["created_at"]))
        d["graph_node_id"] = node_id
        return d

    def add_recommendation(self, recommendation: Recommendation) -> dict[str, Any]:
        """Persist one evidence-backed recommendation atomically."""
        with self.connection(immediate=True) as con:
            return self._insert_recommendation(con, recommendation)

    def recommendations(self, project_id: str) -> list[dict[str, Any]]:
        with self.connection() as con:
            return [self._row(r) for r in con.execute("SELECT * FROM recommendations WHERE project_id = ? ORDER BY created_at DESC", (project_id,))]

    def has_pending_recommendation(self, project_id: str, title: str, evidence_id: str) -> bool:
        for recommendation in self.recommendations(project_id):
            if recommendation["status"] == RecommendationStatus.PENDING_REVIEW.value and recommendation["title"] == title and evidence_id in recommendation["evidence_ids"]:
                return True
        return False

    def has_recommendation_for_evidence(self, project_id: str, title: str, evidence_id: str) -> bool:
        """A reviewed decision remains part of project memory; do not recreate it from unchanged evidence."""
        return any(recommendation["title"] == title and evidence_id in recommendation["evidence_ids"] for recommendation in self.recommendations(project_id))

    def get_recommendation(self, recommendation_id: str) -> dict[str, Any]:
        with self.connection() as con:
            return self._row(con.execute("SELECT * FROM recommendations WHERE id = ?", (recommendation_id,)).fetchone())

    def review_recommendation(self, recommendation_id: str, reviewer: str, decision: RecommendationStatus, comment: str | None = None) -> dict[str, Any]:
        if decision not in {RecommendationStatus.APPROVED, RecommendationStatus.REJECTED}:
            raise ValueError("only approved or rejected is a valid review decision")
        if not isinstance(reviewer, str) or not reviewer.strip():
            raise ValueError("reviewer must be a non-empty string")
        if comment is not None and (not isinstance(comment, str) or len(comment.strip()) > 4000):
            raise ValueError("review comment must be a short string when supplied")
        reviewed_at = now()
        reviewer, comment = reviewer.strip(), comment.strip() if comment else None
        with self.connection(immediate=True) as con:
            raw = con.execute("SELECT * FROM recommendations WHERE id = ?", (recommendation_id,)).fetchone()
            recommendation = self._row(raw)
            if recommendation["status"] != RecommendationStatus.PENDING_REVIEW.value:
                raise ValueError("recommendation has already been reviewed")
            changed = con.execute("UPDATE recommendations SET status=?, reviewer=?, reviewed_at=?, review_comment=? WHERE id=? AND status=?", (decision.value, reviewer, reviewed_at, comment, recommendation_id, RecommendationStatus.PENDING_REVIEW.value))
            if changed.rowcount != 1:
                raise ValueError("recommendation has already been reviewed")
            project_id = recommendation["project_id"]
            recommendation_nodes = []
            for row in con.execute("SELECT * FROM graph_nodes WHERE project_id = ? AND kind = 'recommendation'", (project_id,)):
                node = self._row(row)
                if node["attributes"].get("recommendation_id") == recommendation_id:
                    recommendation_nodes.append(node)
            if len(recommendation_nodes) != 1:
                raise ValueError("recommendation provenance node is missing or ambiguous")
            recommendation_node = recommendation_nodes[0]
            recommendation_attributes = {**recommendation_node["attributes"], "status": decision.value, "reviewer": reviewer, "reviewed_at": reviewed_at}
            con.execute("UPDATE graph_nodes SET attributes_json = ? WHERE id = ?", (self._dump(recommendation_attributes), recommendation_node["id"]))
            con.execute("INSERT INTO events VALUES (?, ?, ?, ?, ?, ?)", (new_id("event"), project_id, "recommendation_reviewed", recommendation_id, self._dump({"recommendation_id": recommendation_id, "decision": decision.value, "reviewer": reviewer, "comment": comment}), reviewed_at))
            result = {**recommendation, "status": decision.value, "reviewer": reviewer, "reviewed_at": reviewed_at, "review_comment": comment}
            if decision is RecommendationStatus.APPROVED and recommendation["proposed_task"]:
                materialization = con.execute("SELECT task_node_id FROM recommendation_materializations WHERE recommendation_id = ?", (recommendation_id,)).fetchone()
                if materialization:
                    raise ValueError("recommendation action has already been materialized")
                task = recommendation["proposed_task"]
                created_at = now()
                task_id = new_id("node")
                task_attributes = {**task, "origin_recommendation_id": recommendation_id, "status": "open"}
                assignee_member = None
                role = task.get("assignee_role")
                if isinstance(role, str) and role.strip():
                    matches = []
                    for row in con.execute("SELECT * FROM graph_nodes WHERE project_id = ? AND kind = 'project_member'", (project_id,)):
                        member = self._row(row)
                        if member["attributes"].get("active") and role.strip().casefold() in member["attributes"].get("roles", []):
                            matches.append(member)
                    if len(matches) == 1:
                        assignee_member = matches[0]
                        task_attributes["assigned_member_id"] = assignee_member["id"]
                con.execute("INSERT INTO graph_nodes VALUES (?, ?, ?, ?, ?, ?)", (task_id, project_id, "task", task["title"], self._dump(task_attributes), created_at))
                root = con.execute("SELECT id FROM graph_nodes WHERE project_id = ? AND kind = 'project' ORDER BY created_at LIMIT 1", (project_id,)).fetchone()
                if root is None:
                    raise ValueError("project root is missing")
                task_edges = [(root["id"], task_id, "contains"), (recommendation_node["id"], task_id, "approved_action")]
                if assignee_member:
                    task_edges.append((task_id, assignee_member["id"], "assigned_to"))
                for source_id, target_id, relation in task_edges:
                    con.execute("INSERT INTO graph_edges VALUES (?, ?, ?, ?, ?, ?, ?)", (new_id("edge"), project_id, source_id, target_id, relation, self._dump({}), created_at))
                state_id, state_node_id = new_id("evidence"), new_id("node")
                state_payload = {"task_id": task_id, "previous_status": None, "status": "open", "changed_by": reviewer, "reason": f"approved recommendation {recommendation_id}"}
                con.execute("INSERT INTO evidence VALUES (?, ?, ?, ?, ?, ?, ?)", (state_id, project_id, "task_state", "system:approved-action", self._dump(state_payload), created_at, 1.0))
                con.execute("INSERT INTO graph_nodes VALUES (?, ?, ?, ?, ?, ?)", (state_node_id, project_id, "evidence", "task_state: system:approved-action", self._dump({"evidence_id": state_id, "kind": "task_state", "source": "system:approved-action", "confidence": 1.0}), created_at))
                for source_id, target_id, relation in ((root["id"], state_node_id, "contains"), (state_node_id, task_id, "states")):
                    con.execute("INSERT INTO graph_edges VALUES (?, ?, ?, ?, ?, ?, ?)", (new_id("edge"), project_id, source_id, target_id, relation, self._dump({}), created_at))
                con.execute("INSERT INTO events VALUES (?, ?, ?, ?, ?, ?)", (new_id("event"), project_id, "task_created_from_approval", task_id, self._dump({"task_node_id": task_id, "recommendation_id": recommendation_id}), created_at))
                if assignee_member:
                    con.execute("INSERT INTO events VALUES (?, ?, ?, ?, ?, ?)", (new_id("event"), project_id, "task_assigned", task_id, self._dump({"task_id": task_id, "member_id": assignee_member["id"], "assigned_by": "recommendation-role-resolution", "recommendation_id": recommendation_id}), created_at))
                con.execute("INSERT INTO recommendation_materializations VALUES (?, ?, ?)", (recommendation_id, task_id, created_at))
                result["created_task"] = {"id": task_id, "project_id": project_id, "kind": "task", "label": task["title"], "attributes": task_attributes, "created_at": created_at}
        return result

    def persist_escalation(self, project_id: str, recommendation_id: str, level: str, evidence_ids: list[str], fixture: bool = False) -> dict[str, Any]:
        if level not in {"REVIEW", "ESCALATE"} or not evidence_ids:
            raise ValueError("escalation requires a supported level and evidence")
        with self.connection(immediate=True) as con:
            existing = con.execute("SELECT * FROM escalations WHERE project_id=? AND recommendation_id=? AND level=?", (project_id, recommendation_id, level)).fetchone()
            if existing:
                return self._row(existing) | {"status": "idempotent"}
            escalation = {"id": new_id("escalation"), "project_id": project_id, "recommendation_id": recommendation_id, "level": level, "evidence_ids": evidence_ids, "created_at": now(), "fixture": bool(fixture)}
            con.execute("INSERT INTO escalations VALUES (?, ?, ?, ?, ?, ?, ?)", (escalation["id"], project_id, recommendation_id, level, self._dump(evidence_ids), escalation["created_at"], int(fixture)))
            con.execute("INSERT INTO events VALUES (?, ?, ?, ?, ?, ?)", (new_id("event"), project_id, "escalation_recorded", escalation["id"], self._dump({"escalation_id": escalation["id"], "recommendation_id": recommendation_id, "level": level, "evidence_ids": evidence_ids}), escalation["created_at"]))
            return escalation | {"status": "created"}

    def escalations(self, project_id: str) -> list[dict[str, Any]]:
        with self.connection() as con:
            return [self._row(row) for row in con.execute("SELECT * FROM escalations WHERE project_id=? ORDER BY created_at", (project_id,))]

    def persist_operational_batch(self, recommendations: list[Recommendation], escalations: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Commit an orchestrator result as one Store transaction, or not at all."""
        with self.connection(immediate=True) as con:
            created = [self._insert_recommendation(con, recommendation) for recommendation in recommendations]
            for finding in escalations:
                project_id, recommendation_id = finding["project_id"], finding["recommendation_id"]
                level, evidence_ids = finding["level"], finding["evidence_ids"]
                if level not in {"REVIEW", "ESCALATE"} or not evidence_ids:
                    raise ValueError("escalation requires a supported level and evidence")
                existing = con.execute("SELECT id FROM escalations WHERE project_id=? AND recommendation_id=? AND level=?", (project_id, recommendation_id, level)).fetchone()
                if existing:
                    continue
                escalation_id, created_at = new_id("escalation"), now()
                con.execute("INSERT INTO escalations VALUES (?, ?, ?, ?, ?, ?, ?)", (escalation_id, project_id, recommendation_id, level, self._dump(evidence_ids), created_at, int(finding.get("fixture", False))))
                con.execute("INSERT INTO events VALUES (?, ?, ?, ?, ?, ?)", (new_id("event"), project_id, "escalation_recorded", escalation_id, self._dump({"escalation_id": escalation_id, "recommendation_id": recommendation_id, "level": level, "evidence_ids": evidence_ids}), created_at))
            return created

    def record_agent_run(self, project_id: str, agent_name: str, input_value: Any, output_value: Any) -> dict[str, Any]:
        digest = lambda value: hashlib.sha256(self._dump(value).encode()).hexdigest()
        run = {"id": new_id("run"), "project_id": project_id, "agent_name": agent_name, "input_hash": digest(input_value), "output_hash": digest(output_value), "result": output_value, "created_at": now()}
        with self.connection() as con:
            con.execute("INSERT INTO agent_runs VALUES (?, ?, ?, ?, ?, ?, ?)", (run["id"], project_id, agent_name, run["input_hash"], run["output_hash"], self._dump(output_value), run["created_at"]))
        return run

    def agent_runs(self, project_id: str) -> list[dict[str, Any]]:
        self.get_project(project_id)
        with self.connection() as con:
            return [self._row(row) for row in con.execute("SELECT * FROM agent_runs WHERE project_id = ? ORDER BY created_at DESC", (project_id,))]
