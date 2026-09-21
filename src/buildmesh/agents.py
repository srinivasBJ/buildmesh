from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .storage import Store
from .types import Evidence, Recommendation, Severity
from .environment import score


@dataclass(frozen=True)
class AgentResult:
    agent: str
    findings: list[dict[str, Any]]
    evidence_ids: list[str]


class DocumentAgent:
    """Extracts a deliberately small set of traceable signals from untrusted document text."""

    name = "document-agent"
    weather_sensitive_terms = {
        "waterproofing": "waterproofing",
        "excavation": "excavation",
        "concrete curing": "curing",
    }

    def run(self, store: Store, project_id: str, evidence: list[dict[str, Any]]) -> AgentResult:
        findings: list[dict[str, Any]] = []
        evidence_ids: list[str] = []
        for item in evidence:
            if item["kind"] != "document":
                continue
            text = item["payload"].get("extracted_text")
            if not isinstance(text, str):
                continue
            normalized = text.lower()
            for activity, term in self.weather_sensitive_terms.items():
                if term in normalized:
                    evidence_ids.append(item["id"])
                    findings.append({"type": "document_weather_sensitive_activity", "activity": activity, "matched_term": term, "evidence_id": item["id"]})
        result = AgentResult(self.name, findings, sorted(set(evidence_ids)))
        store.record_agent_run(project_id, self.name, {"document_evidence_ids": [item["id"] for item in evidence if item["kind"] == "document"]}, {"findings": findings, "evidence_ids": result.evidence_ids})
        return result


class ProjectStateAgent:
    name = "project-state-agent"

    def run(self, store: Store, project_id: str, evidence: list[dict[str, Any]]) -> AgentResult:
        findings: list[dict[str, Any]] = []
        ids: list[str] = []
        seen_task_refs: set[str] = set()
        for item in evidence:
            if item["kind"] != "worker_progress":
                continue
            ids.append(item["id"])
            payload = item["payload"]
            task_ref = payload.get("task_ref")
            if not isinstance(task_ref, str) or task_ref in seen_task_refs:
                continue
            seen_task_refs.add(task_ref)
            reported = payload.get("reported_percent")
            planned = payload.get("planned_quantity")
            completed = payload.get("completed_quantity")
            if planned and completed is not None:
                derived = round((completed / planned) * 100, 1)
                difference = abs(derived - float(reported)) if reported is not None else 0
                findings.append({"task_ref": task_ref, "reported_percent": reported, "derived_percent": derived, "consistent": difference <= 10, "evidence_id": item["id"]})
        result = AgentResult(self.name, findings, ids)
        store.record_agent_run(project_id, self.name, evidence, {"findings": findings, "evidence_ids": ids})
        return result


class ScheduleAgent:
    """Detects explicit schedule variance from current, task-scoped project evidence."""

    name = "schedule-agent"

    def run(self, store: Store, project_id: str, evidence: list[dict[str, Any]], state: AgentResult) -> AgentResult:
        findings: list[dict[str, Any]] = []
        evidence_ids: list[str] = []
        current_state = {finding["task_ref"]: finding for finding in state.findings}
        for item in evidence:
            if item["kind"] != "schedule_context":
                continue
            payload = item["payload"]
            task_ref = payload.get("task_ref")
            progress = current_state.get(task_ref)
            if not progress:
                continue
            planned_percent = float(payload["planned_percent"])
            reported_percent = float(progress["reported_percent"])
            days_remaining = int(payload["days_remaining"])
            variance = round(planned_percent - reported_percent, 1)
            if variance < 15 or days_remaining > 7:
                continue
            severity = Severity.HIGH.value if variance >= 25 or days_remaining <= 2 else Severity.MEDIUM.value
            evidence_ids.extend([item["id"], progress["evidence_id"]])
            findings.append({"type": "schedule_progress_variance", "severity": severity, "task_ref": task_ref, "planned_percent": planned_percent, "reported_percent": reported_percent, "variance_percent": variance, "days_remaining": days_remaining, "evidence_id": item["id"], "evidence_ids": [item["id"], progress["evidence_id"]]})
        result = AgentResult(self.name, findings, sorted(set(evidence_ids)))
        store.record_agent_run(project_id, self.name, {"schedule_evidence_ids": [item["id"] for item in evidence if item["kind"] == "schedule_context"], "state": state.findings}, {"findings": findings, "evidence_ids": result.evidence_ids})
        return result


class MaterialAgent:
    """Reconciles declared material use against the latest task-scoped progress evidence."""

    name = "material-agent"

    def run(self, store: Store, project_id: str, evidence: list[dict[str, Any]]) -> AgentResult:
        graph = store.graph(project_id)
        tasks = [node for node in graph["nodes"] if node["kind"] == "task"]
        task_by_reference = {
            reference: task
            for task in tasks
            for reference in (task["id"], task["label"], task["attributes"].get("external_ref"))
            if isinstance(reference, str) and reference
        }
        findings: list[dict[str, Any]] = []
        evidence_ids: list[str] = []
        seen_task_refs: set[str] = set()
        for item in evidence:
            if item["kind"] != "worker_progress":
                continue
            payload = item["payload"]
            task_ref = payload.get("task_ref")
            if not isinstance(task_ref, str) or task_ref in seen_task_refs:
                continue
            seen_task_refs.add(task_ref)
            task = task_by_reference.get(task_ref)
            planned_units = task["attributes"].get("planned_material_units") if task else None
            actual_units = payload.get("material_units")
            reported_percent = payload.get("reported_percent")
            if not all(isinstance(value, (int, float)) and not isinstance(value, bool) for value in (planned_units, actual_units, reported_percent)):
                continue
            if planned_units <= 0 or actual_units < 0 or not 0 <= reported_percent <= 100:
                continue
            expected_units = round(planned_units * (reported_percent / 100), 2)
            variance_units = round(actual_units - expected_units, 2)
            variance_ratio = abs(variance_units) / planned_units
            if variance_ratio < 0.2:
                continue
            evidence_ids.append(item["id"])
            findings.append({"type": "material_progress_variance", "severity": Severity.HIGH.value if variance_ratio >= 0.35 else Severity.MEDIUM.value, "task_ref": task_ref, "reported_percent": reported_percent, "planned_material_units": planned_units, "actual_material_units": actual_units, "expected_material_units": expected_units, "variance_units": variance_units, "evidence_id": item["id"]})
        result = AgentResult(self.name, findings, evidence_ids)
        store.record_agent_run(project_id, self.name, {"worker_progress_evidence_ids": [item["id"] for item in evidence if item["kind"] == "worker_progress"]}, {"findings": findings, "evidence_ids": evidence_ids})
        return result


class DependencyAgent:
    """Flags started work whose explicit prerequisite remains incomplete; it never changes task state."""

    name = "dependency-agent"

    def run(self, store: Store, project_id: str, evidence: list[dict[str, Any]]) -> AgentResult:
        graph = store.graph(project_id)
        tasks = {node["id"]: node for node in graph["nodes"] if node["kind"] == "task"}
        latest_state: dict[str, dict[str, Any]] = {}
        for item in evidence:
            if item["kind"] != "task_state":
                continue
            task_id = item["payload"].get("task_id")
            if isinstance(task_id, str) and task_id in tasks and task_id not in latest_state:
                latest_state[task_id] = item
        findings: list[dict[str, Any]] = []
        evidence_ids: list[str] = []
        for edge in graph["edges"]:
            if edge["relation"] != "depends_on":
                continue
            dependent, prerequisite = tasks.get(edge["source_id"]), tasks.get(edge["target_id"])
            if not dependent or not prerequisite:
                continue
            dependent_status = dependent["attributes"].get("status")
            prerequisite_status = prerequisite["attributes"].get("status")
            if dependent_status not in {"in_progress", "completed"} or prerequisite_status == "completed":
                continue
            dependent_evidence, prerequisite_evidence = latest_state.get(dependent["id"]), latest_state.get(prerequisite["id"])
            if not dependent_evidence or not prerequisite_evidence:
                continue
            cited = [dependent_evidence["id"], prerequisite_evidence["id"]]
            evidence_ids.extend(cited)
            findings.append({"type": "unfinished_task_prerequisite", "severity": Severity.HIGH.value if dependent_status == "in_progress" else Severity.CRITICAL.value, "dependent_task_id": dependent["id"], "dependent_task_label": dependent["label"], "dependent_status": dependent_status, "prerequisite_task_id": prerequisite["id"], "prerequisite_task_label": prerequisite["label"], "prerequisite_status": prerequisite_status, "evidence_id": dependent_evidence["id"], "evidence_ids": cited})
        result = AgentResult(self.name, findings, sorted(set(evidence_ids)))
        store.record_agent_run(project_id, self.name, {"dependency_edges": [edge["id"] for edge in graph["edges"] if edge["relation"] == "depends_on"], "task_status": {task_id: task["attributes"].get("status") for task_id, task in tasks.items()}}, {"findings": findings, "evidence_ids": result.evidence_ids})
        return result


class PlanConstraintAgent:
    """Checks human-verified document prerequisites against current task state without interpreting drawings."""

    name = "plan-constraint-agent"

    def run(self, store: Store, project_id: str, evidence: list[dict[str, Any]]) -> AgentResult:
        graph = store.graph(project_id)
        tasks = {node["id"]: node for node in graph["nodes"] if node["kind"] == "task"}
        latest_state: dict[str, dict[str, Any]] = {}
        for item in evidence:
            if item["kind"] != "task_state":
                continue
            task_id = item["payload"].get("task_id")
            if isinstance(task_id, str) and task_id in tasks and task_id not in latest_state:
                latest_state[task_id] = item
        findings: list[dict[str, Any]] = []
        evidence_ids: list[str] = []
        for item in evidence:
            if item["kind"] != "plan_prerequisite":
                continue
            payload = item["payload"]
            predecessor = tasks.get(payload.get("predecessor_task_id"))
            dependent = tasks.get(payload.get("dependent_task_id"))
            if not predecessor or not dependent:
                continue
            dependent_status = dependent["attributes"].get("status")
            predecessor_status = predecessor["attributes"].get("status")
            if dependent_status not in {"in_progress", "completed"} or predecessor_status == "completed":
                continue
            predecessor_state, dependent_state = latest_state.get(predecessor["id"]), latest_state.get(dependent["id"])
            if not predecessor_state or not dependent_state:
                continue
            cited = [item["id"], predecessor_state["id"], dependent_state["id"]]
            evidence_ids.extend(cited)
            findings.append({"type": "documented_prerequisite_unverified", "severity": Severity.MEDIUM.value, "predecessor_task_label": predecessor["label"], "predecessor_status": predecessor_status, "dependent_task_label": dependent["label"], "dependent_status": dependent_status, "evidence_quote": payload["evidence_quote"], "evidence_id": item["id"], "evidence_ids": cited})
        result = AgentResult(self.name, findings, sorted(set(evidence_ids)))
        store.record_agent_run(project_id, self.name, {"plan_prerequisite_evidence_ids": [item["id"] for item in evidence if item["kind"] == "plan_prerequisite"]}, {"findings": findings, "evidence_ids": result.evidence_ids})
        return result


class RiskAgent:
    name = "risk-agent"

    def run(self, store: Store, project_id: str, evidence: list[dict[str, Any]], state: AgentResult, documents: AgentResult, schedule: AgentResult, materials: AgentResult, dependencies: AgentResult, plan_constraints: AgentResult) -> AgentResult:
        findings: list[dict[str, Any]] = [*schedule.findings, *materials.findings, *dependencies.findings, *plan_constraints.findings]
        ids = list(state.evidence_ids)
        weather = [item for item in evidence if item["kind"] == "weather_forecast"]
        for item in weather:
            ids.append(item["id"])
            probability = float(item["payload"].get("rain_probability", 0))
            hours = int(item["payload"].get("hours_until", 999))
            if probability >= 0.6 and hours <= 48:
                related_documents = [finding for finding in documents.findings if finding["type"] == "document_weather_sensitive_activity"]
                related_ids = sorted({finding["evidence_id"] for finding in related_documents})
                ids.extend(related_ids)
                findings.append({"type": "weather_window_risk", "severity": Severity.HIGH.value, "rain_probability": probability, "hours_until": hours, "evidence_id": item["id"], "evidence_ids": [item["id"], *related_ids], "document_activities": sorted({finding["activity"] for finding in related_documents})})
        for finding in state.findings:
            if not finding["consistent"]:
                findings.append({"type": "progress_reconciliation_conflict", "severity": Severity.MEDIUM.value, **finding})
        for item in evidence:
            if item["kind"] != "site_observation":
                continue
            labels = {str(observation.get("label", "")).lower() for observation in item["payload"].get("observations", [])}
            if {"access_obstruction", "blocked_access", "obstruction", "temporary_barrier", "barrier", "truck", "vehicle", "construction_equipment"} & labels:
                ids.append(item["id"])
                findings.append({"type": "site_access_obstruction", "severity": Severity.HIGH.value, "evidence_id": item["id"], "labels": sorted(labels)})
        for item in evidence:
            if item["kind"] != "traffic_flow":
                continue
            windows = item["payload"].get("windows")
            confidence = item.get("confidence")
            if not isinstance(windows, list) or not isinstance(confidence, (int, float)) or confidence < 0.5:
                continue
            highest = max(windows, key=lambda window: window["congestion_index"])
            lowest = min(windows, key=lambda window: window["congestion_index"])
            reduction = float(highest["congestion_index"]) - float(lowest["congestion_index"])
            if float(highest["congestion_index"]) >= 0.8 and float(lowest["congestion_index"]) <= 0.55 and reduction >= 0.2:
                ids.append(item["id"])
                findings.append({"type": "traffic_disruption_risk", "severity": Severity.HIGH.value, "evidence_id": item["id"], "high_window": highest, "recommended_window": lowest, "confidence": float(confidence), "reduction": round(reduction, 4)})
        ids.extend([*schedule.evidence_ids, *materials.evidence_ids, *dependencies.evidence_ids, *plan_constraints.evidence_ids])
        result = AgentResult(self.name, findings, sorted(set(ids)))
        store.record_agent_run(project_id, self.name, {"evidence": evidence, "state": state.findings, "document_findings": documents.findings, "schedule_findings": schedule.findings, "material_findings": materials.findings, "dependency_findings": dependencies.findings, "plan_constraint_findings": plan_constraints.findings}, {"findings": findings, "evidence_ids": result.evidence_ids})
        return result


class ContextFusionAgent:
    """Correlates sourced environmental risk with active work/dependencies; never certifies safety."""
    name = "context-fusion-agent"

    def run(self, store: Store, project_id: str, evidence: list[dict[str, Any]]) -> AgentResult:
        graph = store.graph(project_id)
        tasks = [node for node in graph["nodes"] if node["kind"] == "task" and node["attributes"].get("status") == "in_progress"]
        contexts = [item for item in evidence if item["kind"] == "environmental_context"]
        twin_observations = [item for item in evidence if item["kind"] == "twin_observation"]
        conflicts = [item for item in evidence if item["kind"] == "environmental_conflict"]
        nodes = {node["id"]: node for node in graph["nodes"]}
        findings, cited = [], []
        for task in tasks:
            activity = task["attributes"].get("activity", "excavation")
            try:
                assessment = score(activity, contexts)
            except ValueError:
                continue
            incomplete_dependencies = [edge for edge in graph["edges"] if edge["relation"] == "depends_on" and edge["source_id"] == task["id"] and nodes.get(edge["target_id"], {"attributes": {}})["attributes"].get("status") != "completed"]
            risks = [factor for factor in assessment["factors"] if factor.get("effect") == "risk"]
            stale = [factor for factor in assessment["factors"] if factor["factor"] == "stale_context"]
            unknown_soil = [item for item in contexts if item["payload"]["kind"] in {"soil_context", "terrain_context"} and item["payload"]["epistemic_state"] == "UNKNOWN"]
            task_components = {edge["target_id"] for edge in graph["edges"] if edge["relation"] == "affects_component" and edge["source_id"] == task["id"]}
            relevant_twins = [item for item in twin_observations if item["payload"].get("component_id") in task_components]
            related_conflicts = [item for item in conflicts if any(identifier in {factor.get("evidence_id") for factor in assessment["factors"]} for identifier in item["payload"].get("evidence_ids", []))]
            if not (risks and incomplete_dependencies) and not stale and not unknown_soil and not related_conflicts:
                continue
            dependency_ids = [item["id"] for item in evidence if item["kind"] == "task_state" and item["payload"].get("task_id") in {task["id"], *(edge["target_id"] for edge in incomplete_dependencies)}]
            ids = sorted({factor["evidence_id"] for factor in assessment["factors"] if "evidence_id" in factor} | {item["id"] for item in relevant_twins} | {item["id"] for item in unknown_soil} | {item["id"] for item in related_conflicts} | set(dependency_ids))
            trace = ([{"type": "environment", "evidence_id": factor["evidence_id"], "epistemic_state": "STALE" if factor["factor"] == "stale_context" else "VERIFIED", "factor": factor["factor"], "threshold": factor.get("threshold"), "weight": assessment["heuristic"]["weight"]} for factor in assessment["factors"] if "evidence_id" in factor]
                     + [{"type": "twin_observation", "evidence_id": item["id"], "epistemic_state": item["payload"]["epistemic_state"], "component_id": item["payload"]["component_id"], "zone_id": item["payload"].get("zone_id")} for item in relevant_twins]
                     + [{"type": "dependency", "evidence_id": identifier, "epistemic_state": "VERIFIED"} for identifier in dependency_ids]
                     + [{"type": "unknown_context", "evidence_id": item["id"], "epistemic_state": "UNKNOWN", "factor": item["payload"]["kind"]} for item in unknown_soil]
                     + [{"type": "environmental_conflict", "evidence_id": item["id"], "epistemic_state": "CONFLICTING"} for item in related_conflicts])
            factors = [entry["type"] for entry in trace]
            state = "CONFLICTING" if related_conflicts else "STALE" if stale else "UNKNOWN" if unknown_soil else "NEEDS_REVIEW"
            findings.append({"type": "multi_factor_environmental_risk", "severity": "high" if risks and incomplete_dependencies else "medium", "task_id": task["id"], "task_label": task["label"], "affected_component_ids": sorted(task_components), "component_scope": "component" if task_components else "UNKNOWN", "evidence_id": ids[0] if ids else None, "evidence_ids": ids, "factors": factors, "reasoning_trace": {"task_id": task["id"], "affected_scope": sorted(task_components) if task_components else "UNKNOWN", "factors": trace, "requires_review": True}, "assessment": assessment, "epistemic_state": state, "assumptions": [assessment["heuristic"]["status"], "only explicit task-to-component links establish component relevance"], "requires_human_review": True})
            cited.extend(ids)
        result = AgentResult(self.name, findings, sorted(set(cited)))
        store.record_agent_run(project_id, self.name, {"context_ids": [item["id"] for item in contexts]}, {"findings": findings, "evidence_ids": result.evidence_ids})
        return result

class DesignRealityAgent:
    """Reads persisted service-layer reconciliations; it cannot mutate project state."""
    name = "design-reality-agent"
    def run(self, store: Store, project_id: str, evidence: list[dict[str, Any]]) -> AgentResult:
        graph = store.graph(project_id)
        findings = [{"type": "design_reality", "planned_component_id": edge["source_id"], **edge["attributes"]} for edge in graph["edges"] if edge["relation"] == "design_reconciliation"]
        ids = sorted({identifier for item in findings for identifier in item.get("trace", {}).get("evidence_ids", [])})
        store.record_agent_run(project_id, self.name, {"reconciliation_count": len(findings)}, {"findings": findings, "evidence_ids": ids})
        return AgentResult(self.name, findings, ids)

class EscalationAgent:
    """Classifies unresolved recommendations; never escalates by itself."""
    name = "escalation-agent"
    def run(self, store: Store, project_id: str, evidence: list[dict[str, Any]]) -> AgentResult:
        recommendations = store.recommendations(project_id)
        findings = [{"type": "escalation", "recommendation_id": item["id"], "level": "ESCALATE" if item["severity"] == "high" else "REVIEW", "evidence_ids": item["evidence_ids"], "requires_human_review": True} for item in recommendations if item["status"] == "pending_review"]
        ids = sorted({eid for item in findings for eid in item["evidence_ids"]})
        store.record_agent_run(project_id, self.name, {"recommendation_ids": [item["id"] for item in recommendations]}, {"findings": findings, "evidence_ids": ids})
        return AgentResult(self.name, findings, ids)

class ReportingAgent:
    """Produces a read-only operational brief from persisted graph state."""
    name = "reporting-agent"
    def run(self, store: Store, project_id: str, evidence: list[dict[str, Any]]) -> AgentResult:
        graph = store.graph(project_id); tasks = [n for n in graph["nodes"] if n["kind"] == "task"]; recs = store.recommendations(project_id)
        report = {"project_id": project_id, "progress": round(sum(float(n["attributes"].get("reported_percent", 0)) for n in tasks) / len(tasks), 1) if tasks else None, "blocked": sum(n["attributes"].get("status") == "blocked" for n in tasks), "reviews_required": sum(r["status"] == "pending_review" for r in recs), "recommended_actions": len(recs), "evidence_ids": sorted({eid for r in recs for eid in r["evidence_ids"]})}
        store.record_agent_run(project_id, self.name, {"evidence_count": len(evidence)}, report)
        return AgentResult(self.name, [{"type": "daily_project_brief", **report}], report["evidence_ids"])


class RecommendationAgent:
    name = "recommendation-agent"

    def run(self, store: Store, project_id: str, risks: AgentResult) -> list[dict[str, Any]]:
        created: list[dict[str, Any]] = []
        for risk in risks.findings:
            if risk["type"] == "multi_factor_environmental_risk":
                title = "Review environmental context and prerequisite before continuing work"
                if not risk["evidence_ids"] or store.has_recommendation_for_evidence(project_id, title, risk["evidence_ids"][0]):
                    continue
                rec = Recommendation(project_id=project_id, title=title, rationale=f"{risk['task_label']} is affected by: {', '.join(risk['factors'])}. Review the task timing and incomplete prerequisite before continuing. Scoring is a configurable planning heuristic, not an engineering certification.", severity=Severity(risk["severity"]), evidence_ids=risk["evidence_ids"], proposed_task={"title": "Review environmental constraints and prerequisite", "assignee_role": "site_engineer", "due_within_hours": 12, "requires_human_confirmation": True})
                created.append(store.add_recommendation(rec))
            if risk["type"] == "weather_window_risk":
                if store.has_recommendation_for_evidence(project_id, "Review waterproofing schedule before forecast rain", risk["evidence_id"]):
                    continue
                activities = risk.get("document_activities", [])
                document_note = f" Document evidence references {', '.join(activities)}." if activities else ""
                rec = Recommendation(project_id=project_id, title="Review waterproofing schedule before forecast rain", rationale=f"Forecast evidence reports {risk['rain_probability']:.0%} rain probability within {risk['hours_until']} hours.{document_note} Validate drainage and move weather-sensitive work only after engineer review.", severity=Severity.HIGH, evidence_ids=risk.get("evidence_ids", [risk["evidence_id"]]), proposed_task={"title": "Review weather-sensitive work package", "assignee_role": "site_engineer", "due_within_hours": risk["hours_until"], "requires_human_confirmation": True})
                created.append(store.add_recommendation(rec))
            if risk["type"] == "progress_reconciliation_conflict":
                if store.has_recommendation_for_evidence(project_id, "Verify reported progress against planned quantity", risk["evidence_id"]):
                    continue
                rec = Recommendation(project_id=project_id, title="Verify reported progress against planned quantity", rationale=f"Worker report is {risk['reported_percent']}% while derived completion is {risk['derived_percent']}%. This discrepancy requires manual verification before schedule changes.", severity=Severity.MEDIUM, evidence_ids=[risk["evidence_id"]], proposed_task={"title": "Verify quantity and progress report", "assignee_role": "site_engineer", "due_within_hours": 24, "requires_human_confirmation": True})
                created.append(store.add_recommendation(rec))
            if risk["type"] == "site_access_obstruction":
                if store.has_recommendation_for_evidence(project_id, "Verify and clear detected site access obstruction", risk["evidence_id"]):
                    continue
                rec = Recommendation(project_id=project_id, title="Verify and clear detected site access obstruction", rationale="Local perception found a potential access obstruction. Verify the observation on site before removing material or changing traffic controls; this is not a safety or engineering certification.", severity=Severity.HIGH, evidence_ids=[risk["evidence_id"]], proposed_task={"title": "Verify site access route", "assignee_role": "site_engineer", "due_within_hours": 4, "requires_human_confirmation": True})
                created.append(store.add_recommendation(rec))
            if risk["type"] == "traffic_disruption_risk":
                if store.has_recommendation_for_evidence(project_id, "Review lower-disruption lane-closure window", risk["evidence_id"]):
                    continue
                high, recommended = risk["high_window"], risk["recommended_window"]
                rec = Recommendation(project_id=project_id, title="Review lower-disruption lane-closure window", rationale=f"Sourced traffic evidence shows congestion index {high['congestion_index']:.2f} during {high['start_time']}–{high['end_time']} and {recommended['congestion_index']:.2f} during {recommended['start_time']}–{recommended['end_time']} ({risk['confidence']:.0%} source confidence). Review the lower-disruption window with traffic and site teams before changing road controls; this is not a traffic forecast.", severity=Severity.HIGH, evidence_ids=[risk["evidence_id"]], proposed_task={"title": "Review traffic-management work window", "assignee_role": "traffic_manager", "due_within_hours": 4, "requires_human_confirmation": True})
                created.append(store.add_recommendation(rec))
            if risk["type"] == "schedule_progress_variance":
                if store.has_recommendation_for_evidence(project_id, "Review schedule variance before next work window", risk["evidence_id"]):
                    continue
                rec = Recommendation(project_id=project_id, title="Review schedule variance before next work window", rationale=f"Schedule evidence expects {risk['planned_percent']:.0f}% completion for {risk['task_ref']}, while the latest progress evidence reports {risk['reported_percent']:.0f}%. The {risk['variance_percent']:.0f}-point variance has {risk['days_remaining']} day(s) remaining; confirm recovery actions with the responsible engineer.", severity=Severity(risk["severity"]), evidence_ids=risk["evidence_ids"], proposed_task={"title": "Review schedule recovery options", "assignee_role": "project_manager", "due_within_hours": min(max(risk["days_remaining"] * 24, 4), 24), "requires_human_confirmation": True})
                created.append(store.add_recommendation(rec))
            if risk["type"] == "material_progress_variance":
                if store.has_recommendation_for_evidence(project_id, "Verify material use against reported progress", risk["evidence_id"]):
                    continue
                direction = "above" if risk["variance_units"] > 0 else "below"
                rec = Recommendation(project_id=project_id, title="Verify material use against reported progress", rationale=f"Progress evidence for {risk['task_ref']} reports {risk['reported_percent']:.0f}% completion and {risk['actual_material_units']:.2f} material units. That is {abs(risk['variance_units']):.2f} units {direction} the {risk['expected_material_units']:.2f} expected from the planned {risk['planned_material_units']:.2f} units; verify quantities before procurement or schedule changes.", severity=Severity(risk["severity"]), evidence_ids=[risk["evidence_id"]], proposed_task={"title": "Verify material quantities and progress", "assignee_role": "site_engineer", "due_within_hours": 24, "requires_human_confirmation": True})
                created.append(store.add_recommendation(rec))
            if risk["type"] == "unfinished_task_prerequisite":
                if store.has_recommendation_for_evidence(project_id, "Verify unfinished prerequisite before continuing work", risk["evidence_id"]):
                    continue
                rec = Recommendation(project_id=project_id, title="Verify unfinished prerequisite before continuing work", rationale=f"{risk['dependent_task_label']} is {risk['dependent_status'].replace('_', ' ')}, but its recorded prerequisite {risk['prerequisite_task_label']} remains {risk['prerequisite_status'].replace('_', ' ')}. Verify the sequence on site before continuing or accepting dependent work.", severity=Severity(risk["severity"]), evidence_ids=risk["evidence_ids"], proposed_task={"title": "Verify prerequisite completion and work sequence", "assignee_role": "site_engineer", "due_within_hours": 4, "requires_human_confirmation": True})
                created.append(store.add_recommendation(rec))
            if risk["type"] == "documented_prerequisite_unverified":
                if store.has_recommendation_for_evidence(project_id, "Verify documented prerequisite before continuing work", risk["evidence_id"]):
                    continue
                rec = Recommendation(project_id=project_id, title="Verify documented prerequisite before continuing work", rationale=f"The verified plan quote “{risk['evidence_quote']}” records {risk['predecessor_task_label']} before {risk['dependent_task_label']}. The dependent task is {risk['dependent_status'].replace('_', ' ')}, while the predecessor remains {risk['predecessor_status'].replace('_', ' ')}. Verify the documented prerequisite with the responsible engineer before accepting or changing work.", severity=Severity.MEDIUM, evidence_ids=risk["evidence_ids"], proposed_task={"title": "Verify documented work prerequisite", "assignee_role": "site_engineer", "due_within_hours": 8, "requires_human_confirmation": True})
                created.append(store.add_recommendation(rec))
        store.record_agent_run(project_id, self.name, {"risks": risks.findings}, {"recommendations": [r["id"] for r in created]})
        return created


class OpenMeshOrchestrator:
    """Coordinates bounded agents; it cannot execute consequential changes without review."""

    def __init__(self, store: Store) -> None:
        self.store = store
        self.document_agent = DocumentAgent()
        self.state_agent = ProjectStateAgent()
        self.schedule_agent = ScheduleAgent()
        self.material_agent = MaterialAgent()
        self.dependency_agent = DependencyAgent()
        self.plan_constraint_agent = PlanConstraintAgent()
        self.context_fusion_agent = ContextFusionAgent()
        self.design_reality_agent = DesignRealityAgent()
        self.escalation_agent = EscalationAgent()
        self.reporting_agent = ReportingAgent()
        self.risk_agent = RiskAgent()
        self.recommendation_agent = RecommendationAgent()

    def run(self, project_id: str) -> dict[str, Any]:
        evidence = self.store.evidence(project_id)
        documents = self.document_agent.run(self.store, project_id, evidence)
        state = self.state_agent.run(self.store, project_id, evidence)
        schedule = self.schedule_agent.run(self.store, project_id, evidence, state)
        materials = self.material_agent.run(self.store, project_id, evidence)
        dependencies = self.dependency_agent.run(self.store, project_id, evidence)
        plan_constraints = self.plan_constraint_agent.run(self.store, project_id, evidence)
        context_fusion = self.context_fusion_agent.run(self.store, project_id, evidence)
        design_reality = self.design_reality_agent.run(self.store, project_id, evidence)
        risks = self.risk_agent.run(self.store, project_id, evidence, state, documents, schedule, materials, dependencies, plan_constraints)
        risks = AgentResult(risks.agent, [*risks.findings, *context_fusion.findings], sorted(set([*risks.evidence_ids, *context_fusion.evidence_ids])))
        recommendations = self.recommendation_agent.run(self.store, project_id, risks)
        escalation = self.escalation_agent.run(self.store, project_id, evidence)
        report = self.reporting_agent.run(self.store, project_id, evidence)
        return {"project_id": project_id, "agent_results": [{"agent": documents.agent, "findings": documents.findings}, {"agent": state.agent, "findings": state.findings}, {"agent": schedule.agent, "findings": schedule.findings}, {"agent": materials.agent, "findings": materials.findings}, {"agent": dependencies.agent, "findings": dependencies.findings}, {"agent": plan_constraints.agent, "findings": plan_constraints.findings}, {"agent": context_fusion.agent, "findings": context_fusion.findings}, {"agent": design_reality.agent, "findings": design_reality.findings}, {"agent": risks.agent, "findings": risks.findings}, {"agent": escalation.agent, "findings": escalation.findings}, {"agent": report.agent, "findings": report.findings}], "recommendations": recommendations, "human_approval_required": any(r.get("proposed_task") for r in recommendations)}
