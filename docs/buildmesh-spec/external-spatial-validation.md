# External real-world spatial validation

## Classification

This is `EXTERNAL_REAL_WORLD_SPATIAL_VALIDATION`, not physical RoomPlan,
LiDAR-device, Apple-device, construction-grade reconstruction, or an
engineering-certification claim. Physical Apple RoomPlan is
`PHYSICAL_ROOMPLAN_UNVERIFIED` because no supported capture device was
available.

## Research and feasibility audit

[LingBot-Map](https://github.com/Robbyant/lingbot-map) was inspected at commit
`849e690bb086103637e44b1e91878d9d43a8bf0c`. It is Apache-2.0 and documents
image-folder and video input, example scenes, point-cloud rendering, and
per-frame predictions. Its documented path requires CUDA PyTorch 2.8/CUDA
12.8 and a checkpoint. The validation machine is an Apple M2 (8 GPU cores,
8 GB unified RAM, Metal), with no CUDA, no NVIDIA GPU/VRAM, and 63 GiB free
disk. LingBot-Map was therefore not executed locally; no model result is
claimed.

[Voxel-SLAM](https://github.com/hku-mars/Voxel-SLAM) is GPL-2.0 and documents
a LiDAR-inertial ROS/Ubuntu 20.04 stack. It is `RESEARCH_REFERENCE_ONLY`; no
source, dependency, model, or output from it is integrated.

## Actual public input and local output

The actual input was TUM RGB-D `freiburg1_xyz`, a real Kinect recording of an
office desk sequence. TUM licenses benchmark data CC BY 4.0. The download URL,
SHA-256, download date, source size, and precise result are in
[`results/external-spatial-validation`](../../results/external-spatial-validation).
The archive is deliberately not checked in, even though the source license
permits reuse: this source-only repository does not redistribute media.

On 2026-09-23, 12 of the source's 798 RGB frames (640×480) were selected at a
regular interval and processed locally by PyCOLMAP 4.2.0 on CPU. The actual
sparse reconstruction registered 12 cameras and triangulated 1,783 points.
Its bounds and trajectory are in arbitrary COLMAP scale with unknown-up axis;
they are not metres and are not construction measurements.

## BuildMesh mapping and boundary

`normalize_external_spatial` maps an externally produced sparse-scene summary
into the existing `validate_capture` and `ingest_spatial_capture` production
path. It creates exactly one spatial observation: `sparse-scene-bounds`, an
oriented bounding box with `semantic_type: UNKNOWN`. There are no fabricated
windows, doors, walls, furniture, topology, adjacency, or structural claims.

The persisted lineage is:

`REAL_PUBLIC_INPUT → DERIVED_SPATIAL_OUTPUT → spatial_capture evidence → immutable snapshot → twin_observation → observed component`.

The capture retains source URL, license, archive checksum, source capture time
(`UNKNOWN`), derivation tool, `fixture: false`, and evidence state `DERIVED`.
It persists only explicit/supported `contains`, `evidence_for`, and `observes`
relationships. A controlled planned `IfcWindow` comparison returns `UNKNOWN`:
the derived observation has no semantic alignment and arbitrary scale, so no
operational recommendation is created.

## Reproduction

Download the source outside this repository, validate the SHA-256, extract RGB
frames, select the recorded 12-frame sample, then run PyCOLMAP feature
extraction, exhaustive matching, and incremental mapping on CPU. Feed the
result's registered-image count, point count, and bounds to
`buildmesh.external_spatial.normalize_external_spatial`, then call
`BuildMeshService.ingest_spatial_capture`. The checked-in artifact package
contains the exact metadata and normalized capture but not public media.

## Reality labels

| Item | Classification |
| --- | --- |
| BuildMesh normalization and persisted evidence/snapshot/twin | REAL |
| TUM RGB-D imagery | REAL_PUBLIC_INPUT |
| PyCOLMAP sparse scene bounds | DERIVED_SPATIAL_OUTPUT |
| Test-room capture fixture | SYNTHETIC_FIXTURE |
| Apple RoomPlan | PHYSICAL_ROOMPLAN_UNVERIFIED |
| Construction-grade reconstruction | NOT_SUPPORTED |
