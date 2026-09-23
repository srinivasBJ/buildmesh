# Qualcomm validation methodology

This validation is intentionally narrow: it proves that one BuildMesh-relevant depth model can be compiled, profiled, and inferred on a hosted Qualcomm device and that its output remains numerically close to a local reference for one real RGB frame.

The output is evidence, not authoritative BuildMesh project state. It does not establish construction-grade reconstruction, semantic understanding, general accuracy improvement, or an end-to-end BuildMesh performance benefit. Profile values must be attributed to the exact hosted AI Hub run and input shape.

The comparison accepts only equal output shape, correlation >= 0.999, and normalized RMSE <= 0.01. MAE, RMSE, maximum error, correlation, and the fixed policy are all retained in the result package. No source image, model binary, output tensor, or API credential is committed.
