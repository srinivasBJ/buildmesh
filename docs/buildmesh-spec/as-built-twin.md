# As-built Project Twin

BuildMesh’s project twin is a time-indexed, evidence-backed graph representation of planned components and observed component state. It is not a collection of images, a 3D reconstruction, photogrammetry, CAD/BIM parsing, or construction certification.

The hierarchy is Project → Site → Building → Floor → Zone → Component. Immutable snapshots reference source evidence and contain validated `twin_observation` evidence. Planned state is an expectation; observed state is what source-backed snapshots report; verified state requires a `VERIFIED` epistemic state.

Completeness is transparent: `COMPLETE observations / expected components`; coverage is `observed components / expected components`; confidence is mean observation confidence. These are not engineering certification. A component absent from a newer snapshot is `NOT_OBSERVED_IN_CURRENT_SNAPSHOT`, never “removed.”

Future CAD/BIM may attach geometry-provenance to the same component nodes. It must preserve evidence provenance, uncertainty, and human approval.
