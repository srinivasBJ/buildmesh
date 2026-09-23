# Model and device selection

MiDaS V2 was selected because it performs monocular depth estimation, a direct visual-spatial precursor relevant to BuildMesh's external spatial validation. The live Qualcomm catalog metadata identified the `midas` artifact as Midas-V2 / MiDaS_small, with input `image` float32 `[1,3,256,256]` in `[0,1]`, output `depth_estimates` float32 `[1,1,256,256]`, MIT source licensing, and a QAIRT 2.50-compatible artifact.

The preferred Snapdragon 8 Elite Gen 5 QRD was retained only after current inventory and artifact compatibility checks. The live device advertised Android 16, Qualcomm vendor, `qualcomm-snapdragon-8-elite-gen5`, `sm8850`, QNN, and TFLite attributes. Compile acceptance and completed profile/inference jobs supplied the final compatibility evidence; no target fallback was needed.
