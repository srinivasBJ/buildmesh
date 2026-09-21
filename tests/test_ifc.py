from pathlib import Path
import pytest
from buildmesh.service import BuildMeshService

FIXTURE = Path(__file__).parent / "fixtures" / "sample.ifc"
RICH_FIXTURE = Path(__file__).parent / "fixtures" / "rich-apartment.ifc"

def test_real_ifc_openshell_import_is_provenanced_and_idempotent(tmp_path: Path):
    service = BuildMeshService(tmp_path / "ifc.db"); project = service.create_project("IFC")
    first = service.import_ifc(project["id"], FIXTURE); second = service.import_ifc(project["id"], FIXTURE)
    assert first["adapter"] == "IfcOpenShell" and first["ifc_schema"] == "IFC2X3" and first["entity_count"] >= 5
    assert second["status"] == "idempotent" and service.spatial_status(project["id"])["count"] == first["entity_count"]
    evidence = first["evidence"]["payload"]
    assert evidence["source"]["content_sha256"] and evidence["source"]["parser"] == "IfcOpenShell"
    assert first["explicit_relationship_count"] >= 2

def test_malformed_ifc_never_mutates_graph(tmp_path: Path):
    service = BuildMeshService(tmp_path / "ifc.db"); project = service.create_project("IFC")
    before = service.spatial_status(project["id"])["count"]
    bad = tmp_path / "bad.ifc"; bad.write_text("not IFC")
    with pytest.raises(ValueError): service.import_ifc(project["id"], bad)
    assert service.spatial_status(project["id"])["count"] == before

def test_matching_decision_is_provenanced_and_idempotent(tmp_path: Path):
    service = BuildMeshService(tmp_path / "ifc.db"); project = service.create_project("IFC")
    planned = next(node for node in (service.import_ifc(project["id"], FIXTURE), service.store.graph(project["id"])["nodes"])[1] if node["kind"] == "planned_component")
    root = service.store.project_root(project["id"]); zone = service.create_twin_entity(project["id"], root["id"], "zone", "Room"); observed = service.create_twin_entity(project["id"], zone["id"], "component", "Observed")
    source = service.weather_context(project["id"], "fixture", .1, 1)
    first = service.match_planned_observed(project["id"], planned["id"], observed["id"], "EVIDENCE", [source["id"]], .8)
    assert first["edge"]["attributes"]["matching_method"] == "EVIDENCE"
    assert service.match_planned_observed(project["id"], planned["id"], observed["id"], "EVIDENCE", [source["id"]])["status"] == "idempotent"

def test_resolver_uses_real_identity_snapshot_and_detects_spatial_ambiguity(tmp_path: Path):
    service = BuildMeshService(tmp_path / "ifc.db"); project = service.create_project("IFC")
    service.import_ifc(project["id"], FIXTURE); planned = next(n for n in service.store.graph(project["id"])["nodes"] if n["attributes"].get("ifc_global_id") == "5$Window")
    root = service.store.project_root(project["id"]); zone = service.create_twin_entity(project["id"], root["id"], "zone", "Room")
    observed = service.create_twin_entity(project["id"], zone["id"], "component", "Observed window", {"ifc_global_id": "5$Window"})
    source = service.weather_context(project["id"], "fixture", .1, 1); snap = service.create_twin_snapshot(project["id"], "S", "2026-09-08T10:00:00Z", [source["id"]], [{"component_id": observed["id"], "observed_state": "IN_PROGRESS", "confidence": .9, "epistemic_state": "VERIFIED", "zone_id": zone["id"], "fixture": True}])
    resolved = service.resolve_spatial_match(project["id"], planned["id"], snap["id"])
    assert resolved["decision"] == "MATCHED" and resolved["method"] == "IDENTITY" and resolved["edge"]["attributes"]["snapshot_id"] == snap["id"]
    empty = service.create_twin_snapshot(project["id"], "No window", "2026-09-08T11:00:00Z", [source["id"]], [{"component_id": service.create_twin_entity(project["id"], zone["id"], "component", "Other")["id"], "observed_state": "IN_PROGRESS", "confidence": .8, "epistemic_state": "INFERRED", "zone_id": zone["id"], "fixture": True}])
    assert service.resolve_spatial_match(project["id"], planned["id"], empty["id"])["decision"] == "UNKNOWN"

def test_rich_fixture_exact_relationships_and_real_spatial_evidence_matching(tmp_path: Path):
    service = BuildMeshService(tmp_path / "rich.db"); project = service.create_project("Rich IFC")
    imported = service.import_ifc(project["id"], RICH_FIXTURE); graph = service.store.graph(project["id"])
    by_ifc = {n["attributes"].get("ifc_global_id"): n for n in graph["nodes"]}
    def edge(source, target): return next(e for e in graph["edges"] if e["source_id"] == by_ifc[source]["id"] and e["target_id"] == by_ifc[target]["id"] and e["relation"] == "contains")
    assert imported["entity_count"] == 20 and edge("B$A", "F$1")["attributes"]["ifc_relation_class"] == "IfcRelAggregates"
    assert edge("F$1", "R$Kitchen")["attributes"]["derivation_method"] == "EXPLICIT_IFC_RELATION"
    assert edge("R$Living", "C$WinLiving")["attributes"]["ifc_relation_class"] == "IfcRelContainedInSpatialStructure"
    planned = by_ifc["C$WinLiving"]; root = service.store.project_root(project["id"]); zone = service.create_twin_entity(project["id"], root["id"], "zone", "Living")
    observed = service.create_twin_entity(project["id"], zone["id"], "component", "Observed window", {"ifc_class": "IfcWindow", "placement": {"x": 2.1, "y": 1.0, "z": 0.0}})
    snap = service.create_twin_snapshot(project["id"], "S", "2026-09-08T10:00:00Z", [imported["evidence"]["id"]], [{"component_id": observed["id"], "observed_state": "IN_PROGRESS", "confidence": .9, "epistemic_state": "VERIFIED", "zone_id": zone["id"], "fixture": True}])
    result = service.resolve_spatial_match(project["id"], planned["id"], snap["id"], tolerance=.5)
    assert result["decision"] == "MATCHED" and result["method"] == "EVIDENCE"  # provenance takes precedence
    # A snapshot without planned evidence forces actual placement-based correspondence.
    source = service.weather_context(project["id"], "fixture", .1, 1)
    spatial_snap = service.create_twin_snapshot(project["id"], "Spatial", "2026-09-08T11:00:00Z", [source["id"]], [{"component_id": observed["id"], "observed_state": "IN_PROGRESS", "confidence": .9, "epistemic_state": "VERIFIED", "zone_id": zone["id"], "fixture": True}])
    spatial = service.resolve_spatial_match(project["id"], planned["id"], spatial_snap["id"], tolerance=.5)
    assert spatial["decision"] == "MATCHED" and spatial["method"] == "SPATIAL" and spatial["edge"]["attributes"]["features"][0]["distance"] == .1

def test_rich_ifc_mutations_and_spatial_conflict_change_production_results(tmp_path: Path):
    raw = RICH_FIXTURE.read_text()
    def imported(text, name):
        path = tmp_path / name; path.write_text(text); service = BuildMeshService(tmp_path / f"{name}.db"); project = service.create_project(name); service.import_ifc(project["id"], path); return service, project
    # Removal changes the exact IFC containment graph edge.
    service, project = imported(raw.replace("#410=IFCRELCONTAINEDINSPATIALSTRUCTURE('REL$Living',#2,$,$,(#20,#21),#10);\n", ""), "no-relation.ifc")
    graph = service.store.graph(project["id"]); by = {n["attributes"].get("ifc_global_id"): n for n in graph["nodes"]}
    assert not any(e["source_id"] == by["R$Living"]["id"] and e["target_id"] == by["C$WinLiving"]["id"] and e["relation"] == "contains" for e in graph["edges"])
    # GlobalId mutation changes an identity match to UNKNOWN.
    service, project = imported(raw.replace("C$WinLiving", "C$WinChanged"), "changed-id.ifc")
    planned = next(n for n in service.store.graph(project["id"])["nodes"] if n["attributes"].get("ifc_global_id") == "C$WinChanged"); root = service.store.project_root(project["id"]); zone = service.create_twin_entity(project["id"], root["id"], "zone", "Living"); observed = service.create_twin_entity(project["id"], zone["id"], "component", "Observed", {"ifc_global_id": "C$WinLiving"}); source = service.weather_context(project["id"], "fixture", .1, 1); snap = service.create_twin_snapshot(project["id"], "identity", "2026-09-08T10:00:00Z", [source["id"]], [{"component_id": observed["id"], "observed_state": "IN_PROGRESS", "confidence": .8, "epistemic_state": "VERIFIED", "zone_id": zone["id"], "fixture": True}])
    assert service.resolve_spatial_match(project["id"], planned["id"], snap["id"])["decision"] == "UNKNOWN"
    # Placement mutation makes an otherwise spatial candidate fall outside tolerance.
    service, project = imported(raw.replace("#321=IFCCARTESIANPOINT((2.,1.,0.));", "#321=IFCCARTESIANPOINT((20.,1.,0.));"), "moved.ifc")
    planned = next(n for n in service.store.graph(project["id"])["nodes"] if n["attributes"].get("ifc_global_id") == "C$WinLiving"); root = service.store.project_root(project["id"]); zone = service.create_twin_entity(project["id"], root["id"], "zone", "Living"); a = service.create_twin_entity(project["id"], zone["id"], "component", "A", {"ifc_class": "IfcWindow", "placement": {"x": 2.1, "y": 1.0, "z": 0.0}}); b = service.create_twin_entity(project["id"], zone["id"], "component", "B", {"ifc_class": "IfcWindow", "placement": {"x": 2.2, "y": 1.0, "z": 0.0}}); source = service.weather_context(project["id"], "fixture", .1, 1); snap = service.create_twin_snapshot(project["id"], "moved", "2026-09-08T10:00:00Z", [source["id"]], [{"component_id": a["id"], "observed_state": "IN_PROGRESS", "confidence": .8, "epistemic_state": "VERIFIED", "zone_id": zone["id"], "fixture": True}, {"component_id": b["id"], "observed_state": "IN_PROGRESS", "confidence": .8, "epistemic_state": "VERIFIED", "zone_id": zone["id"], "fixture": True}])
    assert service.resolve_spatial_match(project["id"], planned["id"], snap["id"], tolerance=.5)["decision"] == "UNKNOWN"
    # The unchanged fixture gives two bounded spatial candidates and requires review.
    service, project = imported(raw, "conflict.ifc"); planned = next(n for n in service.store.graph(project["id"])["nodes"] if n["attributes"].get("ifc_global_id") == "C$WinLiving"); root = service.store.project_root(project["id"]); zone = service.create_twin_entity(project["id"], root["id"], "zone", "Living"); candidates = [service.create_twin_entity(project["id"], zone["id"], "component", label, {"ifc_class": "IfcWindow", "placement": {"x": x, "y": 1.0, "z": 0.0}}) for label, x in (("A", 2.1), ("B", 2.2))]; source = service.weather_context(project["id"], "fixture", .1, 1); snap = service.create_twin_snapshot(project["id"], "conflict", "2026-09-08T10:00:00Z", [source["id"]], [{"component_id": c["id"], "observed_state": "IN_PROGRESS", "confidence": .8, "epistemic_state": "VERIFIED", "zone_id": zone["id"], "fixture": True} for c in candidates])
    conflict = service.resolve_spatial_match(project["id"], planned["id"], snap["id"], tolerance=.5)
    assert conflict["decision"] == "CONFLICTING" and len(conflict["edge"]["attributes"]["candidate_component_ids"]) == 2 and service.store.recommendations(project["id"])

def test_match_conflict_rolls_back_and_rich_cross_domain_trace_is_persisted(tmp_path: Path):
    service = BuildMeshService(tmp_path / "atomic.db"); project = service.create_project("Atomic")
    imported = service.import_ifc(project["id"], RICH_FIXTURE); graph = service.store.graph(project["id"]); planned = next(n for n in graph["nodes"] if n["attributes"].get("ifc_global_id") == "C$WinLiving"); room = next(n for n in graph["nodes"] if n["attributes"].get("ifc_global_id") == "R$Living")
    task = service.create_task(project["id"], "Install Window", {}); service.link_document_spatial(project["id"], imported["evidence"]["id"], planned["id"], task["id"])
    solar = service.environmental_solar(project["id"], 12.97, 77.59, "2026-09-16"); service.link_environment_spatial(project["id"], solar["id"], planned["id"])
    root = service.store.project_root(project["id"]); zone = service.create_twin_entity(project["id"], root["id"], "zone", "Living"); observed = [service.create_twin_entity(project["id"], zone["id"], "component", f"Candidate {i}", {"ifc_class": "IfcWindow", "placement": {"x": x, "y": 1., "z": 0.}}) for i, x in enumerate((2.1, 2.2))]
    source = service.weather_context(project["id"], "fixture", .1, 1); snap = service.create_twin_snapshot(project["id"], "S", "2026-09-16T10:00:00Z", [source["id"]], [{"component_id": item["id"], "observed_state": "IN_PROGRESS", "confidence": .9, "epistemic_state": "VERIFIED", "zone_id": zone["id"], "fixture": True} for item in observed])
    before = service.store.graph(project["id"]); before_recommendations = service.store.recommendations(project["id"])
    with pytest.raises(RuntimeError, match="injected"):
        service.resolve_spatial_match(project["id"], planned["id"], snap["id"], tolerance=.5, fail_after_decision=True)
    after = service.store.graph(project["id"])
    assert len(after["edges"]) == len(before["edges"]) and service.store.recommendations(project["id"]) == before_recommendations
    result = service.resolve_spatial_match(project["id"], planned["id"], snap["id"], tolerance=.5)
    trace = service.match_cross_domain_trace(project["id"], planned["id"], snap["id"])
    assert result["decision"] == "CONFLICTING" and trace["task_ids"] == [task["id"]] and trace["snapshot_id"] == snap["id"]
    assert trace["environment_evidence_node_ids"] and trace["observation_evidence_ids"] and room["id"]

def test_design_reality_computes_deviation_type_conflict_and_not_observed(tmp_path: Path):
    service = BuildMeshService(tmp_path / "design.db"); project = service.create_project("Design")
    imported = service.import_ifc(project["id"], RICH_FIXTURE); planned = next(n for n in service.store.graph(project["id"])["nodes"] if n["attributes"].get("ifc_global_id") == "C$WinLiving")
    root = service.store.project_root(project["id"]); zone = service.create_twin_entity(project["id"], root["id"], "zone", "Living"); task = service.create_task(project["id"], "Install window", {}); service.link_document_spatial(project["id"], imported["evidence"]["id"], planned["id"], task["id"])
    observed = service.create_twin_entity(project["id"], zone["id"], "component", "Observed", {"ifc_class": "IfcWindow", "placement": {"x": 3., "y": 1., "z": 0.}}); source = service.weather_context(project["id"], "fixture", .1, 1); snap = service.create_twin_snapshot(project["id"], "S", "2026-09-16T10:00:00Z", [source["id"]], [{"component_id": observed["id"], "observed_state": "IN_PROGRESS", "confidence": .9, "epistemic_state": "VERIFIED", "zone_id": zone["id"], "fixture": True}]); service.resolve_spatial_match(project["id"], planned["id"], snap["id"], tolerance=2)
    report = service.design_reality_analyze(project["id"], planned["id"], snap["id"], tolerance=.15)
    assert report["state"] == "SPATIAL_DEVIATION" and report["requires_review"] and report["trace"]["task_ids"] == [task["id"]]
    repeat = service.design_reality_analyze(project["id"], planned["id"], snap["id"], tolerance=.15)
    assert repeat["status"] == "idempotent" and any(r["agent"] == "design-reality-agent" for r in service.openmesh.run(project["id"])["agent_results"])

def test_design_reality_scope_conflict_is_explicit(tmp_path: Path):
    service = BuildMeshService(tmp_path / "scope.db"); project = service.create_project("Scope")
    imported = service.import_ifc(project["id"], RICH_FIXTURE); graph = service.store.graph(project["id"]); planned = next(n for n in graph["nodes"] if n["attributes"].get("ifc_global_id") == "C$WinLiving"); planned_scope = next(e["source_id"] for e in graph["edges"] if e["target_id"] == planned["id"] and e["relation"] == "contains")
    root = service.store.project_root(project["id"]); zone = service.create_twin_entity(project["id"], root["id"], "zone", "Other"); observed = service.create_twin_entity(project["id"], zone["id"], "component", "Observed", {"ifc_global_id": "C$WinLiving", "planned_scope_id": "different-scope"}); source = service.weather_context(project["id"], "fixture", .1, 1); snap = service.create_twin_snapshot(project["id"], "S", "2026-09-16T10:00:00Z", [source["id"]], [{"component_id": observed["id"], "observed_state": "IN_PROGRESS", "confidence": .9, "epistemic_state": "VERIFIED", "zone_id": zone["id"], "fixture": True}]); service.resolve_spatial_match(project["id"], planned["id"], snap["id"])
    report = service.design_reality_analyze(project["id"], planned["id"], snap["id"])
    assert report["state"] == "SCOPE_CONFLICT" and report["trace"]["planned_scope_id"] == planned_scope and report["requires_review"]

def test_design_reality_rollback_and_persisted_timeline(tmp_path: Path):
    service = BuildMeshService(tmp_path / "design-atomic.db"); project = service.create_project("Design atomic")
    service.import_ifc(project["id"], RICH_FIXTURE); planned = next(n for n in service.store.graph(project["id"])["nodes"] if n["attributes"].get("ifc_global_id") == "C$WinLiving")
    root = service.store.project_root(project["id"]); zone = service.create_twin_entity(project["id"], root["id"], "zone", "Living")
    observed = service.create_twin_entity(project["id"], zone["id"], "component", "Observed", {"ifc_global_id": "C$WinLiving", "ifc_class": "IfcWindow", "placement": {"x": 3., "y": 1.}})
    source = service.weather_context(project["id"], "fixture", .1, 1); snapshot = service.create_twin_snapshot(project["id"], "S1", "2026-09-16T10:00:00Z", [source["id"]], [{"component_id": observed["id"], "observed_state": "IN_PROGRESS", "confidence": .9, "epistemic_state": "VERIFIED", "zone_id": zone["id"], "fixture": True}])
    service.resolve_spatial_match(project["id"], planned["id"], snapshot["id"])
    before = service.store.graph(project["id"]); events, reviews = service.store.events(project["id"]), service.store.recommendations(project["id"])
    with pytest.raises(RuntimeError, match="injected"):
        service.design_reality_analyze(project["id"], planned["id"], snapshot["id"], tolerance=.15, fail_after_decision=True)
    assert len(service.store.graph(project["id"])["edges"]) == len(before["edges"])
    assert service.store.events(project["id"]) == events and service.store.recommendations(project["id"]) == reviews
    assert service.design_reality_analyze(project["id"], planned["id"], snapshot["id"], tolerance=.15)["state"] == "SPATIAL_DEVIATION"
    assert len(service.design_reality_timeline(project["id"], planned["id"])["timeline"]) == 1

def test_orientation_context_retains_solar_evidence(tmp_path: Path):
    service = BuildMeshService(tmp_path / "orientation.db"); project = service.create_project("Orientation")
    root = service.store.project_root(project["id"]); room = service.create_twin_entity(project["id"], root["id"], "zone", "Bedroom", {"orientation": "East"})
    solar = service.environmental_solar(project["id"], 12.97, 77.59, "2026-09-16"); service.link_environment_spatial(project["id"], solar["id"], room["id"])
    result = service.architectural_orientation_context(project["id"], room["id"])
    assert result["recommendation"] == "Potentially favorable morning-light orientation." and result["environment_evidence_ids"] == [solar["id"]] and result["non_certifying"]

def test_hierarchical_scope_comparison_identifies_first_mismatch(tmp_path: Path):
    service = BuildMeshService(tmp_path / "hierarchy.db"); project = service.create_project("Hierarchy")
    planned = {level: f"{level}-a" for level in ("project", "building", "floor", "zone", "room", "component")}
    observed = {**planned, "room": "room-b"}
    result = service.compare_scope_hierarchy(planned, observed)
    assert result["decision"] == "SCOPE_CONFLICT" and result["scope_conflict_level"] == "ROOM" and result["epistemic_state"] == "NEEDS_REVIEW"
    assert service.compare_scope_hierarchy(planned, {"project": "project-a"})["decision"] == "UNKNOWN"
