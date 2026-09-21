# Competition traceability

| Criterion | Capability | Evidence/demo |
| --- | --- | --- |
| Technical implementation | QNN target architecture | deployment manifest, readiness, competition-verify |
| Application innovation | project graph + bounded agents | scenario evaluator |
| Deployment/accessibility | local-first API and CPU mode | CLI/API tests |
| Presentation/documentation | specification and evidence package | this directory |
| Environmental correctness | normalized live/fixture context, bounded score, scoped fusion | `ENV-001`–`ENV-010`, `tests/test_environment.py` |
| As-built relevance | explicit task → component edge, traceable twin observations | `ENV-002`, `ENV-009` |
| Spatial foundation | planned JSON → project graph → observed-twin comparison | `tests/test_spatial.py` |
| Design-to-reality | snapshot-scoped matching, deviation/type/scope conflict, review provenance | `DESIGN-013`–`DESIGN-020`, `design-reality-demo`, `tests/test_ifc.py` |

QNN/NPU execution remains unverified until `NPU_EXECUTED` evidence exists.

Environmental fixture evidence is explicitly `FIXTURE`; Open-Meteo is the only current `LIVE` environmental path. Historical comparison is `HISTORICAL_NOT_FORECAST`; solar is `CALCULATED`; manual reports remain `MANUAL`. Provider gaps for historical climate, traffic, events, terrain, and soil are not represented as live integrations.

Design-reality evidence is bounded and non-certifying. The four-snapshot apartment output uses the real rich IFC fixture and preserves historical records rather than rewriting prior decisions. Scope comparison reports the first explicit mismatch level when complete hierarchy IDs are supplied; missing levels remain `UNKNOWN`.
