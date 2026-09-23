# Methodology

The model artifact was retrieved from the official Qualcomm AI Hub Models MiDaS-V2 catalog entry after catalog compatibility metadata reported ONNX Runtime, QAIRT DLC, and TFLite support, including the current QAIRT 2.50 artifact version. Its source model is MiDaS_small and its published source license is MIT.

The configured `qai-hub` CLI was verified at package version 0.55.0. Its active Python was 3.14.7; because the catalog helper currently supports Python `<3.14`, a temporary Python 3.13.15 environment ran the official helper and API scripts, using the same existing SDK configuration. No configuration file or token was read into this package.

The selected hosted device was verified immediately before submission through the authenticated AI Hub inventory: Snapdragon 8 Elite Gen 5 QRD, Android 16, Qualcomm vendor attributes, chipset attributes `qualcomm-snapdragon-8-elite-gen5` and `sm8850`, and QNN/TFLite support. The live framework inventory included QAIRT 2.45, 2.49, and 2.50; the catalog-compatible 2.50 asset was used.

The input was a non-redistributed TUM RGB-D freiburg1_xyz RGB frame. It was converted to RGB, resized with Pillow bilinear interpolation to 256x256, transposed HWC to NCHW, converted to float32, and divided by 255. The local reference used ONNX Runtime only for correctness; no local duration is reported as Qualcomm performance.

The same compiled target model produced by compile job `jpe7z89v5` was passed to profile job `jpxl43vlp` and inference job `jgnzno2qg`. AI Hub changed the source ONNX output name `depth_estimates` to target output name `output_0`; both are the sole `[1,1,256,256]` float32 output and were compared by order and shape.

The tolerance was fixed before reading the hosted output: equal shape, Pearson correlation >= 0.999, and normalized RMSE <= 0.01, where normalized RMSE is RMSE divided by the reference output range. The raw returned profile is preserved in `profile/ai-hub-profile.json`; raw model, image, and tensor files are intentionally omitted.
