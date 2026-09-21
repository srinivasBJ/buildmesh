# Design-to-reality reasoning

BuildMesh compares imported planned components to a snapshot-scoped observed correspondence. It reports `MATCHED`, `SPATIAL_DEVIATION`, `TYPE_CONFLICT`, `NOT_OBSERVED`, `CONFLICTING`, or `UNKNOWN` only from persisted matching evidence and explicit type/placement fields.

Distance is deterministic and uses the supplied tolerance. Identity evidence is HIGH sufficiency, bounded spatial evidence MEDIUM, and unsupported/ambiguous evidence LOW or REVIEW_REQUIRED. A task-linked spatial deviation creates a human-review recommendation with its trace; it never changes schedule or certifies design quality.

Scope comparison is explicit: a planned IFC containment scope and an independently supplied observed scope can yield `SCOPE_CONFLICT`; absent lower-level scope remains `UNKNOWN`. Repeated analysis reads the persisted reconciliation edge before writing, so the same component/snapshot creates no duplicate edge, recommendation, or event. The reconciliation edge, mandatory review, and event are committed in one transaction.

`design_reality_timeline` reads immutable snapshot-scoped reconciliation records. `architectural_orientation_context` is a separate bounded orientation-plus-solar observation: it retains linked solar evidence IDs, marks its output `INFERRED`, and is explicitly non-certifying—there is no daylight-factor, energy, or CFD claim.
