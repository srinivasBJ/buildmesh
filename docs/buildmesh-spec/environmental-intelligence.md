# Environmental Context Intelligence

BuildMesh stores environmental records as validated `environmental_context` evidence connected to a `location_context` graph node. Supported kinds are weather observation/forecast, historical climate, solar/wind, traffic, local events, terrain, and soil. Each record retains source, source type, timestamps, geographic scope, units, confidence, epistemic state, freshness, and a fixture flag.

`live`, `fixture`, `manual`, `calculated`, and `historical` sources are distinct. Fixture records cannot be represented as live. Sources are untrusted input and cannot directly mutate project task state.

Freshness is reusable: weather observation/wind 3 hours, forecast 6, traffic 2, local events 24. Expired context becomes `STALE`; it is not treated as current truth. Historical data remains historical and cannot be forecast. `UNKNOWN`, `CONFLICTING`, and `STALE` are distinct: unavailable data, contradictory retained sources, and expired context respectively.

Activity scoring is deterministic: `score = 100 - (risk factor count × configured activity weight × 100)`. Each returned score contains the applied thresholds, activity weight, source evidence IDs, missing-factor list, and formula. Missing data yields lower confidence rather than zero risk; stale data yields no current suitability score; conflicting sources create conflict evidence and a review path rather than a winner. Heuristics are configurable planning aids, not engineering standards.

`ContextFusionAgent` correlates an active task, incomplete dependency, relevant environmental risk/staleness, and explicitly scoped as-built observations. A task reaches a component only through an explicit `task -[affects_component]-> component` edge; observation names, project membership, and proximity never create that link. With no such edge, `component_scope` is `UNKNOWN`. Its machine-readable `reasoning_trace` contains task, affected scope, factor type, evidence ID, epistemic state, thresholds/weights where applicable, and review requirement. Recommendations retain those cited evidence IDs and require human approval before action materialization.

Traffic and event context use **time-scoped contextual matching**, not prediction: an effect applies only when its supplied start/end interval exactly matches a candidate work interval. Their source, timestamp, source type, and fixture/live status remain in the graph. Solar is calculated approximate daylight only; wind is contextual orientation data only. No CFD, solar-ray, terrain, soil, traffic, event, or historical-climate provider is claimed. Production historical climate, traffic, event, terrain, and soil adapters remain future interfaces; deterministic fixture adapters support evaluation.

The evaluator includes `ENV-001` through `ENV-010` and asserts state mutation, graph links, evidence IDs, epistemic behavior, interval effects, approvals, and idempotence—not generated wording. `LIVE`, `FIXTURE`, `CALCULATED`, `MANUAL`, and `HISTORICAL` identify source provenance; `UNKNOWN`, `STALE`, `CONFLICTING`, and `UNVERIFIED` retain their separate uncertainty meanings.

Future CAD/BIM may consume location/solar/wind context through component graph nodes. Future predictive models must retain distinct source and uncertainty fields.
