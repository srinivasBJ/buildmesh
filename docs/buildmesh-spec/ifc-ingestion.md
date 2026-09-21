# Real IFC ingestion

BuildMesh uses **IfcOpenShell 0.8.x** to parse actual IFC STEP files locally, then validates and imports normalized planned spatial entities. Supported schemas are those accepted by the installed IfcOpenShell parser; the tested fixture is IFC2X3.

Currently normalized: site, building, building storey, space, wall, slab, door, window, roof, column, beam, and stair. Each entity preserves its IFC GlobalId (or explicit step fallback), IFC class, source-file SHA-256, filename, parser version, schema, and planned-state provenance. Complex geometry/placements are currently retained as `UNKNOWN` / unsupported rather than reconstructed or claimed exact.

`buildmesh ifc-import <project-id> sample.ifc` is the real production import path. DXF/DWG and full IFC geometry/system semantics remain unsupported. Duplicate source identity is idempotent; malformed IFC is parsed and validated before graph mutation.

IfcOpenShell aggregate and spatial-containment relations are normalized only when the source IFC declares them, with `EXPLICIT_IFC_RELATION` and original IFC relation class attached to the graph edge. Planned-to-observed correspondence is resolved from imported/twin evidence rather than a caller-supplied decision. It supports bounded identity, evidence-lineage, and placement-distance matching; full mesh correspondence, CAD geometry reconstruction, and complete IFC system semantics remain future work.

The rich non-confidential IFC2X3 fixture contains two storeys, seven spaces, walls, slabs, doors, windows, a column, a stair, 20 `IfcLocalPlacement` records, three aggregate relationships, and six spatial-containment relationships. `spatial-query <project> ifc-id=<GlobalId>` resolves an imported source identity.

`resolve_spatial_match` is snapshot-scoped and computes its decision in precedence order: verified GlobalId identity, planned-import evidence present in the snapshot source lineage, then same IFC class plus placement distance within a supplied tolerance. Multiple valid candidates become `CONFLICTING` and receive a review recommendation; an empty snapshot is `NOT_OBSERVED`. The decision edge records planned/observation evidence IDs, snapshot, candidates, features, method, decision, and epistemic state. This is bounded placement correspondence, not full geometric reconstruction.

Conflict decisions, their persisted event, and their required review recommendation use one SQLite transaction. A failure injected after decision insertion rolls back all of them. The rich-fixture trace can expose persisted planned component → task → observed component/snapshot → match decision → environmental-context links.
