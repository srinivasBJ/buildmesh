#!/usr/bin/env python3
"""Generate deterministic README SVG figures from Qualcomm validation evidence."""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from xml.sax.saxutils import escape


PROFILE_KEYS = ("estimated_inference_time", "first_load_time", "warm_load_time")


def read_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"cannot read required JSON artifact: {path}") from error
    if not isinstance(value, dict):
        raise ValueError(f"required artifact is not an object: {path}")
    return value


def value(mapping: dict, key: str) -> float:
    item = mapping.get(key)
    if not isinstance(item, (int, float)) or isinstance(item, bool) or not math.isfinite(item):
        raise ValueError(f"required numeric metric is absent or invalid: {key}")
    return float(item)


def svg_profile(summary: dict, profile: dict) -> str:
    summary_metrics = summary.get("profile", {}).get("execution_summary", {})
    profile_metrics = profile.get("execution_summary", profile)
    if any(summary_metrics.get(key) != profile_metrics.get(key) for key in PROFILE_KEYS):
        raise ValueError("summary and profile artifacts disagree about profile metrics")
    timings = [("Estimated inference", value(profile_metrics, PROFILE_KEYS[0]) / 1000), ("First app load", value(profile_metrics, PROFILE_KEYS[1]) / 1000), ("Subsequent app load", value(profile_metrics, PROFILE_KEYS[2]) / 1000)]
    width, height, left, right = 1080, 430, 240, 80
    plot_width = width - left - right
    log_min, log_max = -1.0, 3.0
    position = lambda number: left + (math.log10(number) - log_min) / (log_max - log_min) * plot_width
    ticks = [(0.1, "0.1"), (1, "1"), (10, "10"), (100, "100"), (1000, "1000")]
    grid = "".join(f'<line x1="{position(mark):.1f}" y1="82" x2="{position(mark):.1f}" y2="340" class="grid"/><text x="{position(mark):.1f}" y="368" class="tick">{label}</text>' for mark, label in ticks)
    bars = "".join(f'<text x="{left - 18}" y="{136 + index * 78}" class="label" text-anchor="end">{escape(name)}</text><rect x="{left}" y="{112 + index * 78}" width="{max(1, position(milliseconds) - left):.1f}" height="34" class="bar"/><text x="{min(width - right + 8, position(milliseconds) + 10):.1f}" y="{136 + index * 78}" class="value">{milliseconds:.3f} ms</text>' for index, (name, milliseconds) in enumerate(timings))
    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="1080" height="430" viewBox="0 0 1080 430" role="img" aria-labelledby="title desc"><title id="title">Qualcomm AI Hub profile timing metrics</title><desc id="desc">Log-scale timing chart for one validated Midas V2 run on Snapdragon 8 Elite Gen 5 QRD.</desc><style>.bg{{fill:#fff}}.title{{font:600 22px system-ui,sans-serif;fill:#0f172a}}.sub{{font:14px system-ui,sans-serif;fill:#475569}}.label,.value,.tick{{font:14px system-ui,sans-serif;fill:#0f172a}}.tick{{fill:#475569;text-anchor:middle}}.grid{{stroke:#cbd5e1;stroke-width:1}}.axis{{stroke:#64748b;stroke-width:1.5}}.bar{{fill:#2563eb}}.note{{font:12px system-ui,sans-serif;fill:#64748b}}</style><rect class="bg" width="1080" height="430"/><text class="title" x="48" y="40">Qualcomm AI Hub Profile — Snapdragon 8 Elite Gen 5 QRD</text><text class="sub" x="48" y="64">Midas-V2 compiled target · timings use a logarithmic millisecond axis</text>{grid}<line x1="{left}" y1="340" x2="{width-right}" y2="340" class="axis"/>{bars}<text class="sub" x="{(left + width - right) / 2:.1f}" y="401" text-anchor="middle">Milliseconds (log scale)</text><text class="note" x="48" y="416">Source: results/qualcomm-validation/profile/ai-hub-profile.json · single hosted profile run</text></svg>'''


def svg_comparison(summary: dict) -> str:
    comparison = summary.get("comparison")
    if not isinstance(comparison, dict) or comparison.get("passed") is not True:
        raise ValueError("complete numerical comparison evidence is required")
    rows = [
        ("Pearson correlation", value(comparison, "pearson_correlation"), ">= 0.999"),
        ("Normalized RMSE", value(comparison, "normalized_rmse"), "<= 0.01"),
        ("Mean absolute error", value(comparison, "mae"), "reported"),
        ("Maximum absolute error", value(comparison, "max_absolute_error"), "reported"),
    ]
    cards = "".join(f'<rect x="{48 + index * 250}" y="116" width="220" height="166" class="card"/><text x="{158 + index * 250}" y="150" class="metric" text-anchor="middle">{escape(label)}</text><text x="{158 + index * 250}" y="205" class="number" text-anchor="middle">{metric:.8g}</text><text x="{158 + index * 250}" y="244" class="threshold" text-anchor="middle">{escape(threshold)}</text>' for index, (label, metric, threshold) in enumerate(rows))
    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="1080" height="370" viewBox="0 0 1080 370" role="img" aria-labelledby="title desc"><title id="title">Reference versus Qualcomm numerical validation</title><desc id="desc">Directly labeled output-comparison metrics for the Midas V2 reference and hosted Qualcomm output.</desc><style>.bg{{fill:#fff}}.title{{font:600 22px system-ui,sans-serif;fill:#0f172a}}.sub{{font:14px system-ui,sans-serif;fill:#475569}}.card{{fill:#f8fafc;stroke:#cbd5e1;stroke-width:1.5;rx:10}}.metric{{font:600 14px system-ui,sans-serif;fill:#0f172a}}.number{{font:600 25px ui-monospace,SFMono-Regular,monospace;fill:#0f766e}}.threshold{{font:13px system-ui,sans-serif;fill:#475569}}.note{{font:12px system-ui,sans-serif;fill:#64748b}}</style><rect class="bg" width="1080" height="370"/><text class="title" x="48" y="42">Reference vs Qualcomm Output Validation</text><text class="sub" x="48" y="67">Same real RGB input · float32 output shape [1, 1, 256, 256] · values are not accuracy percentages</text>{cards}<text class="note" x="48" y="333">Source: results/qualcomm-validation/summary.json · correlation and normalized RMSE use predeclared acceptance gates</text></svg>'''


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results", type=Path, default=Path("results/qualcomm-validation"))
    args = parser.parse_args()
    summary = read_json(args.results / "summary.json")
    profile = read_json(args.results / "profile" / "ai-hub-profile.json")
    if summary.get("status") != "COMPLETE":
        raise ValueError("figures require a COMPLETE Qualcomm validation result")
    output = args.results / "figures"
    output.mkdir(parents=True, exist_ok=True)
    (output / "profile-metrics.svg").write_text(svg_profile(summary, profile) + "\n", encoding="utf-8")
    (output / "numerical-validation.svg").write_text(svg_comparison(summary) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
