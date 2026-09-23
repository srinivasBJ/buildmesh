# Spatial capture ingestion

BuildMesh owns a normalized spatial-capture contract. It accepts an observed
room/scope and oriented bounding-box entities; it does not import another
project's source code, viewer, fixture, asset, or device SDK.

## Contract

A capture has `capture_id`, strict `source` metadata (`kind`, `device`,
`format`, `fixture`, `provenance`), `captured_at`, metre/right-handed-Y-up
coordinate metadata, one scope, and one or more entities. Entity fields are
strict: source identity, category, semantic type, OBB geometry (position,
positive dimensions, orientation), parent scope, explicit containment-only
relationships, and an evidence reference. `material` and numeric `confidence`
are optional. Unknown fields are rejected.

`confidence` is stored only when supplied. Its absence is persisted as
`confidence_state: UNKNOWN`; the snapshot's zero value is a conservative system
representation of no supplied numeric confidence, not a sensor measurement.

## Persisted flow

`capture -> spatial_capture evidence -> scope + observed components -> immutable
snapshot -> twin_observation evidence` is written in one Store transaction.
The capture evidence retains capture ID, source, device, format, provenance,
timestamp, frame, and fixture status. Each component retains its source entity
ID and a capture evidence ID; graph `evidence_for`, `contains`, `supports`, and
`observes` edges retain the lineage.

Capture IDs are project-scoped and content-hashed. Repeating an identical
submission returns the prior evidence, scope, snapshot, and components without
adding records. Reusing a capture ID with different content is rejected. A new
capture ID creates a new immutable snapshot and never rewrites earlier state.

## Scope and relationships

Only explicit containment is accepted. An entity may be parented by the capture
scope or another capture entity. No adjacency, intersection, topology, material
measurement, sensor confidence, mesh, texture, or physical-world claim is
derived by this adapter.

## Reality boundary

The contract and ingestion path are real BuildMesh functionality. The test-room
fixture and the manually supplied RoomPlan-style input are synthetic and marked
`fixture: true`. They are format tests, not evidence of iPhone/iPad LiDAR
capture. Physical Apple RoomPlan capture remains `UNVERIFIED` until a supported
device produces and is validated against a real capture. External visual
validation is a distinct path: `external_visual` sources retain URL, license,
checksum, derivation tool, and `DERIVED` evidence state. An arbitrary-scale
reconstruction uses `colmap-world-unknown-up`; BuildMesh does not convert it to
metres, infer object semantics, or call it LiDAR/RoomPlan data. See
[external spatial validation](external-spatial-validation.md).

## Inspection

`buildmesh --database DB spatial-capture inspect PROJECT_ID CAPTURE_ID` returns
the persisted capture evidence, snapshot, scope, entities, provenance, and
fixture flag. It is an inspection boundary only; visualization remains separate
from BuildMesh's system of record.
