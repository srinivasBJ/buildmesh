# Spatial Intelligence Foundation

BuildMesh imports a normalized, project-scoped `buildmesh-spatial-plan-v1` JSON fixture. It models building, level, zone, room, component/opening, system, and connection with stable source IDs, parent relationships, orientation, and optional point/line/polygon/bounding-box geometry references. This is a spatial foundation, not CAD editing, IFC/DWG/DXF parsing, 3D reconstruction, or engineering certification.

Imported planned components retain plan evidence. Explicit `corresponds_to` links connect a planned component to an observed twin component; `spatial_diff` returns `MATCHED`, `CONFLICTING`, or conservative `NOT_OBSERVED`. Absence never becomes `MISSING` without independent evidence. Document evidence can link explicitly to a spatial element and task. Environmental evidence can explicitly affect a spatial scope; orientation remains `UNKNOWN` where unsupported.

The only adapter is the deterministic JSON fixture boundary. DXF, DWG, IFC, and BIM adapters are interface/future work and are not claimed as supported imports.
