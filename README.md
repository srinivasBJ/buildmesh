# BuildMesh

BuildMesh is a local-first construction intelligence backend. It turns project updates, site evidence, and optional external context into an auditable project graph. Its OpenMesh runtime coordinates small, bounded agents that recommend actions; people approve consequential actions before anything changes.

The v0.1 vertical slice proves this loop:

`observe -> reconcile -> assess risk -> recommend -> approve -> act -> remember`

It is designed to be deployable on Snapdragon-powered Windows PCs. The repository includes a provider boundary for Qualcomm QNN / ONNX Runtime; no performance number is claimed until measured on target hardware.

## What is included

- SQLite-backed project graph: projects, nodes, edges, events, evidence, recommendations, approvals, tasks, and agent-run provenance.
- OpenMesh orchestrator: document signals, verified plan prerequisites, task dependencies, project-state and material reconciliation, schedule-variance assessment, risk assessment, and recommendation creation.
- Human approval gate: recommendations cannot create tasks until explicitly approved.
- Project accountability: role-bearing project members, task assignments, reviewer-candidate resolution, and deterministic assignment of an approved action when exactly one member matches its role.
- Local evidence intake: multipart image and document uploads, SHA-256 hashes, file-signature checks, PDF/text extraction, and project-scoped local storage.
- Live external context: Open-Meteo forecast retrieval from project coordinates, with the provider payload preserved as evidence.
- Snapdragon Edge Intelligence: explicit QNN/NPU or CPU development backend selection, strict detection/segmentation validation, runtime provenance, device identity, repeatable local benchmarks, and source-to-recommendation graph lineage.
- Operational outputs: structured daily intelligence reports and an opt-in SMTP reviewer-notification adapter.

## Runtime architecture

```text
Site image / document / worker update       Open-Meteo forecast
                 |                                  |
                 +--------- evidence store ----------+
                                      |
                              SQLite project graph
                                      |
                  OpenMesh bounded agent pipeline
  document + plan + state + schedule + material + dependency agents -> risk assessment -> recommendation
                                      |
                           human review / approval
                                      |
                   task materialization + event trace
                                      |
                  daily report / optional SMTP notice
```

Every model, file, or external-provider output is treated as data, not instruction. The API stores provenance before agents use it; recommendations name their supporting evidence; task creation is blocked until an identified reviewer approves.

## Quick start

```bash
cd buildmesh
python -m venv .venv
. .venv/bin/activate
pip install '.[dev]'
buildmesh --database ./buildmesh.db demo
buildmesh --database ./buildmesh.db serve
```

Open `http://127.0.0.1:8000` for the local BuildMesh workspace or `http://127.0.0.1:8000/docs` for the API. On Windows, activate the environment with `.venv\\Scripts\\activate`.

Environmental intelligence is evidence-first: normalized Open-Meteo forecasts may be `LIVE`; deterministic demos are `FIXTURE`; solar is `CALCULATED`; reports may be `MANUAL`; aggregate climate is `HISTORICAL`, never a forecast. `UNKNOWN`, `STALE`, and `CONFLICTING` remain distinct and request review rather than silently claiming safety. Run `buildmesh evaluate --scenario all --json` to execute the core and `ENV-001`–`ENV-010` behavioral suites.

IFC support uses the real local IfcOpenShell parser: `buildmesh ifc-import <project-id> <file.ifc>` normalizes supported IFC2X3/accepted-schema entities into planned spatial state with file-hash provenance. It is not a CAD editor, IFC geometry engine, or engineering-certification workflow.

Planned↔observed correspondence is snapshot-scoped and uses verified IFC identity, validated planned-evidence snapshot lineage, or bounded IFC-placement distance. Ambiguous candidates remain reviewable conflicts; they are never auto-selected.

Design reality reconciliation compares planned IFC components with independently observed snapshots. It preserves `MATCHED`, `SPATIAL_DEVIATION`, `TYPE_CONFLICT`, `SCOPE_CONFLICT`, `NOT_OBSERVED`, and `UNKNOWN` states, task/review provenance, and immutable snapshot history. Run `design-reality-demo` for the deterministic four-snapshot apartment fixture. The demo is fixture evidence only; this project does not claim full 3D reconstruction or engineering certification.

For a local container runtime:

```bash
docker compose up --build
```

## Demonstrate the product loop

```bash
# Creates a repeatable project, active Foundation F-12 excavation task, progress, and weather context.
buildmesh --database ./buildmesh.db demo

# Reset the same database/assets back to that exact initial state. With a QNN or CPU
# backend configured, pass a real construction-site image to include local perception.
buildmesh --database ./buildmesh.db demo-reset --site-image ./fixtures/foundation-access.jpg

# Inspect the result.
curl http://127.0.0.1:8000/projects
curl http://127.0.0.1:8000/projects/<project-id>/graph

# Human approval unlocks the derived task.
curl -X POST http://127.0.0.1:8000/recommendations/<recommendation-id>/approve \
  -H 'content-type: application/json' \
  -d '{"reviewer":"site.engineer@example.com","decision":"approved","comment":"Move waterproofing before forecast rain."}'
```

## API flow

1. `POST /projects` creates a project and its graph root.
2. `POST /projects/{id}/tasks` creates scoped work such as `Foundation F-12`.
3. `POST /projects/{id}/members` records active project members and roles; `/tasks/{task_id}/assign` records a task assignment.
4. `POST /projects/{id}/tasks/{task_id}/dependencies` creates an acyclic dependency on a prerequisite task; `/status` records an auditable task-state transition.
5. `POST /projects/{id}/updates` ingests a human progress report and its quantities.
6. `POST /projects/{id}/assets/images` and `/assets/documents` persist project-scoped local evidence.
7. `POST /projects/{id}/assets/images/{evidence_id}/analyze` invokes the configured local vision provider.
8. `POST /projects/{id}/context/weather/refresh` gets a live forecast when project metadata contains `latitude` and `longitude`; `/context/traffic` records sampled congestion windows, while `/context` accepts typed soil, CAD, and environmental context and `/context/schedule` records task-scoped planned progress and remaining days.
9. `POST /projects/{id}/plan-prerequisites` records a verified quote from an uploaded document that connects a predecessor and dependent task. It does not parse or certify CAD/BIM semantics.
10. `POST /projects/{id}/edges` connects graph nodes, for example a task to a structural element or evidence to a work package.
11. `POST /projects/{id}/orchestrate` runs the bounded agents and stores evidence-backed recommendations.
12. `POST /projects/{id}/ask` answers bounded risk, progress, and history questions directly from evidence. A local language model can be configured only through the validated `BUILDMESH_LLM_COMMAND` contract.
13. `POST /recommendations/{id}/approve` records a reviewer decision atomically. An approved recommendation materializes exactly one proposed task and assigns it when one active project member matches its proposed role.
14. `GET /projects/{id}/reports/daily` returns a traceable report payload; `POST /recommendations/{id}/notify-reviewer` uses SMTP only if explicitly configured.

## Snapdragon / Qualcomm AI Hub

BuildMesh's competition-targeted model is **Qualcomm AI Hub YOLOv11-Detection**. The AI Hub model recipe documents YOLOv11 detection and its Ultralytics license dependency; its source weights are not redistributable with this repository. The intended Windows deployment is an AI Hub-exported ONNX model using `QNNExecutionProvider` on the NPU. Standard YOLOv11 detection input is configured at **640×640** by default (`BUILDMESH_QNN_INPUT_SIZE`), and its model output is decoded into boxes/classes locally. The selected detection model provides boxes; the evidence contract also accepts independently produced segmentation regions, so a selected AI Hub segmentation runner can feed the same graph without changing BuildMesh agents.

```text
site image -> ONNX Runtime / QNNExecutionProvider -> validated detections + segments
          -> site_observation evidence -> project graph -> bounded OpenMesh agents
          -> grounded recommendation -> human approval -> task/action audit trail
```

The app has two explicit backends:

```powershell
# Competition target: Windows on a Snapdragon X Elite/X2 machine.
# Use AMD64 Python as required by Qualcomm's AI Hub Models tooling.
pip install ".[snapdragon]"
$env:VISION_BACKEND = "qnn"
$env:BUILDMESH_QNN_MODEL = "C:\\models\\yolov11-detection-qnn.onnx"
$env:BUILDMESH_QNN_MODEL_ID = "YOLOv11-Detection"
$env:BUILDMESH_QNN_MODEL_VERSION = "<AI-Hub-export-or-build-version>"
$env:BUILDMESH_QNN_BACKEND_PATH = "C:\\Qualcomm\\QAIRT\\lib\\x86_64-windows-msvc\\QnnHtp.dll"
$env:BUILDMESH_QNN_COMMAND = "python -m buildmesh.qnn_runner"
buildmesh --database .\\buildmesh.db demo-reset --site-image .\\fixtures\\foundation-access.jpg

# Development fallback only. It always records cpu/development_cpu rather than NPU.
pip install ".[vision]"
$env:VISION_BACKEND = "cpu"
$env:BUILDMESH_ULTRALYTICS_WEIGHTS = "C:\\models\\yolo11n.pt"
```

`buildmesh.qnn_runner` creates an ONNX Runtime session with `QNNExecutionProvider` and refuses to emit a result when that provider is unavailable; it does not silently use CPU. It emits a versioned JSON contract containing model/version, execution provider and target, locally observed device identity, preprocessing/inference/postprocessing/total timings, boxes, and optional segmentation regions. BuildMesh rejects unexpected fields, unsupported labels, malformed boxes, invalid confidence/area, inconsistent QNN/CPU claims, and timing that does not add up. A model observation is *perception*, not a structural, soil, concrete, safety, or compliance certification.

Use the API to benchmark a real image after configuring the backend:

```bash
curl http://127.0.0.1:8000/edge/status
curl -X POST http://127.0.0.1:8000/projects/<project-id>/assets/images/<image-evidence-id>/benchmark \
  -H 'content-type: application/json' -d '{"repetitions":20}'
```

The benchmark persists cold-start, warm-up, repeated p50/p95/mean latency and throughput as `perception_benchmark` evidence. Power and memory are explicitly `not measured`; no figures are fabricated. `qualcomm_reference` is a separate nullable field and is never populated from a local result. Run this benchmark on the actual submission machine before presenting a hardware claim. `GET /edge/status` says `device_identity: "unknown"` when the host cannot locally establish Qualcomm/Snapdragon identity.

Qualcomm AI Hub documents supported deployment through ONNX Runtime and Qualcomm AI Engine Direct, plus on-device profiling (latency, memory and compute-unit utilization). Its model collection lists YOLOv11 detection, YOLOv11 segmentation, Snapdragon X Elite/X2 target devices, and Windows ONNX support: [AI Hub documentation](https://app.aihub.qualcomm.com/docs/index.html), [AI Hub Models](https://github.com/qualcomm/ai-hub-models), and [YOLOv11 model recipe and licensing](https://github.com/qualcomm/ai-hub-models/blob/main/src/qai_hub_models/models/yolov11_det/README.md).

### Running BuildMesh on Snapdragon

Use a supported Snapdragon Windows PC, AMD64 Python, Qualcomm AI Runtime/QNN libraries, and an AI Hub-exported YOLOv11 ONNX model. Set `VISION_BACKEND=qnn`; BuildMesh will not change to CPU if QNN initialization, model loading, provider selection, shape validation, or inference fails. CPU is available only through the explicit `VISION_BACKEND=cpu` choice.

```powershell
# From the checked-out repository on the target PC.
pip install ".[dev,snapdragon]"
$env:VISION_BACKEND = "qnn"
$env:BUILDMESH_QNN_MODEL = "C:\\models\\yolov11-detection-qnn.onnx"
$env:BUILDMESH_QNN_COMMAND = "python -m buildmesh.qnn_runner"
buildmesh --database .\\competition.db competition-verify `
  --site-image .\\fixtures\\foundation-access.jpg `
  --output .\\competition-evidence --repetitions 20 `
  --cpu-weights C:\\models\\yolo11n.pt --run-tests
```

The command first restores the demo baseline, then attempts the actual image → QNN → evidence → graph → agent → recommendation → approval flow. Its evidence directory contains `competition-report.json`, `hardware.json`, graph/timeline lineage, and, on success, observation/benchmark/recommendation/approval records. Supplying `--cpu-weights` additionally writes `cpu-comparison.json` from the same fixture; without it comparison is explicitly `not_available`. It records `VERIFIED` only when the returned result says `backend: qnn` and `execution_target: npu`; otherwise it is `UNVERIFIED` and preserves the failure diagnostic. It detects Windows manufacturer/model when available, otherwise uses `unknown`.

Troubleshooting: a missing QNN runtime or provider, invalid model, incompatible model shape, malformed runner output, timeout, or unknown device identity is a failed/unverified run—not a CPU fallback. Check the package's `competition-report.json`, verify `QNNExecutionProvider` can initialize, then rerun. The published Qualcomm reference source is [YOLOv11-Detection on AI Hub](https://aihub.qualcomm.com/models/yolov11_det); its reference data must remain separate from the locally measured `benchmark.json` values.

### Qualcomm validation manifest and evidence

[qualcomm-deployment.json](src/buildmesh/qualcomm-deployment.json) is the versioned target-architecture manifest: YOLOv11-Detection / YOLO11-N, `[1,3,640,640]`, ONNX Runtime, `QNNExecutionProvider`, Windows, and the Qualcomm-published Snapdragon X Elite, X Plus 8-Core, and X2 Elite target families. It is not a local hardware-validation record.

`GET /edge/qnn-readiness` and the competition package expose an evidence-gated state machine: `NOT_AVAILABLE → CONFIGURED → MODEL_AVAILABLE → QNN_AVAILABLE → DEVICE_VERIFIED → NPU_EXECUTED → BENCHMARK_COMPLETE`. A transition requires its preceding state and local evidence; macOS configuration can never report `NPU_EXECUTED`.

AI Hub prepares, validates, and profiles model deployments on hosted devices. QNN is the local Snapdragon runtime. BuildMesh is the application layer that turns its validated output into evidence, graph lineage, recommendations, and human-approved actions. The architecture is:

```text
Input image -> BuildMesh VisionEngine -> ONNX Runtime -> QNNExecutionProvider
-> Snapdragon NPU -> validated observation -> evidence -> project graph
-> OpenMesh agents -> grounded recommendation -> human approval -> action
```

For a hosted Qualcomm AI Hub result, preserve the provider response as JSON and import it explicitly:

```bash
buildmesh --database ./competition.db import-qualcomm-result <project-id> ./ai-hub-result.json
```

The importer requires job ID, target device, model, runtime, compute unit, positive latency, HTTPS source URL, and completion time. It stores `qualcomm_reference` evidence with `measurement_origin: qualcomm_hosted_device`; it is never treated as a BuildMesh local measurement or used to calculate a speedup unless an operator establishes comparability.

For coordinates, BuildMesh queries Open-Meteo's forecast endpoint for hourly precipitation probability and preserves the raw source payload. Open-Meteo documents the coordinate-based hourly forecast interface and precipitation-probability semantics [here](https://open-meteo.com/en/docs).

## Design constraints

- Recommendations are not engineering approvals.
- External facts retain source, capture time, and confidence.
- Agent runs retain input and output hashes for auditability.
- A project update is reconciled against planned quantity when available; conflicts become review-required findings, never silent changes.
- Sensitive evidence stays local by default; external APIs are used only for data that is inherently external, such as weather or traffic.
- Traffic-window recommendations are comparisons of supplied observations, not traffic forecasts; road controls remain subject to human review.
- SMTP is disabled until `BUILDMESH_SMTP_HOST` and `BUILDMESH_SMTP_SENDER` are set. Credentials are environment-only; they are never stored in the graph.

## Repository layout

```text
src/buildmesh/     application package
tests/             unit and workflow tests
output/pdf/        pitch deck PDF
```

## Status

This is an MVP backend, not a safety-certified construction system. CAD/BIM geometry parsing, production access control, construction-specific model validation, full segmentation model deployment, traffic-provider credentials, and target-device QNN benchmarking remain required before a real construction deployment. Plan-text prerequisites are verified only against the uploaded text and always require human review before action.
