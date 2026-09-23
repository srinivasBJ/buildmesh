# Qualcomm AI Hub validation implementation

`scripts/validate_qualcomm_ai_hub.py` is the deliberate live runner. It imports `qai_hub as hub`, constructs `hub.Client()` without accepting a credential argument, checks the device inventory and framework list, uploads the prepared model directory, and waits for compile, profile, and inference jobs. It writes only sanitized metadata and checks potential credential markers before JSON serialization.

`scripts/collect_qualcomm_validation.py` is a separate evidence collector for completed job IDs. It downloads the returned profile and inference output, computes the offline numerical comparison through `buildmesh.qualcomm_validation`, and emits the provenance package. Neither script is part of BuildMesh runtime behavior or its project graph.

Use an environment with the official SDK and reference dependencies, then invoke the runner with a prepared Qualcomm catalog MiDaS external-data directory and a legally obtained TUM frame. Credentials remain in the SDK's existing configuration and must never be supplied on the command line or written to the repository.

For this run, the already configured `qai-hub` CLI was verified at version 0.55.0. Its active Python was 3.14.7, while the Qualcomm catalog helper currently declares Python `<3.14`; a temporary local Python 3.13.15 environment was therefore used only to retrieve the official catalog artifact and execute the reference/API scripts. It reused the configured SDK credential source and was never recorded in the repository.
