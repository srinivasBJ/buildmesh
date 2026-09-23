# External spatial validation artifact

This package contains metadata and derived structured results only. It contains
no TUM RGB-D media, no LingBot-Map/Voxel-SLAM source, model, fixture, or asset.

`source.json` identifies the `REAL_PUBLIC_INPUT`; `capture.json` is the exact
BuildMesh normalized `DERIVED_SPATIAL_OUTPUT`; `summary.json` records sparse
reconstruction and persisted-state facts; `reconciliation.json` records the
conservative `UNKNOWN` bridge result; `execution.log` records the local run.

Reproduce outside the repository using the source URL and SHA-256 in
`source.json`, then use the metadata documented in
`docs/buildmesh-spec/external-spatial-validation.md`.
