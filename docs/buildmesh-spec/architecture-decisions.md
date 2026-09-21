# Architecture decisions

- ADR-001 persistent project graph: retain project memory and relationships.
- ADR-002 evidence-first reasoning: source claims before agents use them.
- ADR-003 bounded agents: agents recommend, not execute consequential changes.
- ADR-004 human approval: reviewer decision gates actions.
- ADR-005 atomic materialization: approval and derived task provenance commit together.
- ADR-006 explicit uncertainty: unknown/unverified must not become fact.
- ADR-007 QNN/CPU separation: backend selection is explicit and CPU is fallback only.
- ADR-008 Snapdragon verification: state machine gates NPU claims on evidence.
- ADR-009 deterministic fixtures: repeatable demo and regression scenarios.
- ADR-010 environmental source discipline: context is evidence with source type, units, scope, and freshness; fixture never impersonates live data.
- ADR-011 deterministic context fusion: configurable heuristics expose inputs and uncertainty and cannot make engineering certifications.
