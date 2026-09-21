# Spatial semantics

Geometry uses normalized point/line/polygon/bounding-box references. `VERIFIED` currently means a normalized bounding box is available; `UNAVAILABLE` means none was supplied; `UNSUPPORTED` means the supplied form cannot safely drive reasoning. IfcOpenShell placement transforms and azimuth are retained only where extractable, otherwise orientation is `UNKNOWN`.

`spatial-semantics` derives `within`, `contains`, `overlaps`, and `adjacent_to` only from verified bounding boxes, tagging every edge `GEOMETRIC_CONTAINMENT`. Explicit IFC containment remains distinct from geometric derivation. No adjacency, containment, connection, movement, or system topology is inferred from labels or IDs.

Queries are bounded: `components_in_room`, `components_of_type`, and `planned_not_observed`. The latter is a correspondence gap, never a claim of physical absence.

IFC placement correspondence uses extracted x/y/z transforms with a caller-configured tolerance and exact IFC class compatibility. It never matches names or room membership alone. Two qualifying observations are `CONFLICTING`; a qualified identity is `MATCHED`; an empty snapshot yields `NOT_OBSERVED`; incompatible or insufficient evidence yields `UNKNOWN`.
