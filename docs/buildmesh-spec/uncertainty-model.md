# Uncertainty model

Canonical epistemic states are `VERIFIED`, `INFERRED`, `ASSUMED`, `UNKNOWN`, `CONFLICTING`, `STALE`, and `NEEDS_REVIEW`.

Only source-backed, validated facts may be `VERIFIED`. `INFERRED` and `ASSUMED` never become `VERIFIED` automatically. Missing values remain `UNKNOWN`; contradictory inputs must be `CONFLICTING` and trigger review; context outside its decision window is `STALE`.
