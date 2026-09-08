# Architecture

SQLite stores project graph nodes, edges, evidence, events, recommendations, agent runs, and approval materializations. Bounded OpenMesh agents operate on that state. The perception boundary is `VisionEngine → ONNX Runtime/QNN or explicit CPU fallback → validated observation → evidence → graph`.

QNN execution is a target architecture until the validation state records real NPU evidence.
