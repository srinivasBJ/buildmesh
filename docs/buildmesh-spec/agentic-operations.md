# Agentic project operations

Phase 6 closes BuildMesh's operational loop as bounded, evidence-grounded, human-approved operations:

`OBSERVE → UNDERSTAND → PLAN → RECOMMEND → APPROVE → EXECUTE → NOTIFY → REMEMBER → RE-EVALUATE`

Schedule, material, environmental, design-reality, dependency, and perception signals are read by specialist agents. `RiskAgent` consolidates those signals while retaining contributing evidence. `EscalationAgent` classifies unresolved recommendations as `REVIEW` or `ESCALATE`; it does not perform an irreversible escalation. `ReportingAgent` emits a read-only daily brief.

Delayed work has four reversible recovery alternatives (`RESEQUENCE`, `SHIFT_WINDOW`, `COMPLETE_PREREQUISITE`, and `MITIGATE_AND_REVIEW`). Each includes affected tasks, assumptions, dependencies, expected schedule effect, and uncertainty. No alternative is selected automatically.

Recommendations remain pending review until the existing approval service materializes an action exactly once. Notifications retain recommendation, recipient, project, and evidence context and are idempotent on retry. Events and agent runs provide the operational timeline. SMTP is only an adapter; fixture notifications are explicitly marked `FIXTURE`.

This is not autonomous construction management, safety certification, engineering approval, procurement, or an ERP system.
