# Qualcomm AI Hub validation evidence

Status: **COMPLETE**. This package records one completed hosted validation run; it is not a claim that BuildMesh itself runs on Qualcomm hardware.

- **Model:** Midas-V2, the Qualcomm AI Hub Models `midas` ONNX float artifact (MiDaS_small; MIT source license).
- **Device:** Snapdragon 8 Elite Gen 5 QRD, Android 16; current inventory attributes record Qualcomm Snapdragon 8 Elite Gen 5 / `sm8850`, QNN and TFLite support.
- **Runtime / target:** asset compatibility metadata names QAIRT `2.50.0.260828221209`; AI Hub compiled a TFLite target model `mqeyzkj5m`.
- **Input:** TUM RGB-D `freiburg1_xyz`, `rgb/1305031102.175304.png`, 640x480 RGB, SHA-256 `331c3fd7a3d5eefb19e3da2026625aa221ca1991f5ae30ff96060e2e3e45e368`. It was resized bilinearly to NCHW `[1,3,256,256]` float32 in `[0,1]`. The image is not included.
- **Compile:** SUCCESS, job `jpe7z89v5`; [AI Hub job](https://workbench.aihub.qualcomm.com/jobs/jpe7z89v5/).
- **Profile:** SUCCESS, job `jpxl43vlp`; [AI Hub job](https://workbench.aihub.qualcomm.com/jobs/jpxl43vlp/). The returned profile reports estimated inference time `1241` microseconds (1.241 ms), first load `886925` microseconds, and warm load `95899` microseconds. Its detailed operator data reports NPU compute units.
- **Inference:** SUCCESS, job `jgnzno2qg`; [AI Hub job](https://workbench.aihub.qualcomm.com/jobs/jgnzno2qg/). Returned target output: `output_0`, float32 `[1,1,256,256]`.
- **Correctness:** a local ONNX Runtime reference was compared to the hosted output. Shapes match; MAE 1.5730768, RMSE 1.8346029, maximum absolute error 5.3421631, normalized RMSE 0.00197487, and Pearson correlation 0.99999566. The predeclared gate was correlation >= 0.999 and normalized RMSE <= 0.01, so the comparison passed.

The evidence chain is: TUM frame -> MiDaS-V2 -> local reference and hosted Qualcomm inference -> numerical comparison -> BuildMesh validation evidence. It does not claim Qualcomm produced the prior PyCOLMAP reconstruction, nor that a depth map by itself creates a semantic construction twin.

See [summary.json](summary.json), the full [profile artifact](profile/ai-hub-profile.json), and [methodology.md](methodology.md). `summary.json` contains only identifiers and metadata; no credential, image, model binary, or tensor payload is committed.
