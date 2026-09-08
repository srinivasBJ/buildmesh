from __future__ import annotations

import json
import platform
import subprocess
from datetime import UTC, datetime
from typing import Any


def _windows_inventory() -> dict[str, str]:
    """Best-effort local Windows inventory; never substitute guesses for facts."""
    unknown = {"device_manufacturer": "unknown", "device_model": "unknown", "soc": "unknown"}
    if platform.system() != "Windows":
        return unknown
    command = "Get-CimInstance Win32_ComputerSystem | Select-Object Manufacturer,Model | ConvertTo-Json -Compress"
    try:
        result = subprocess.run(["powershell", "-NoProfile", "-Command", command], capture_output=True, text=True, timeout=8, check=True)
        data = json.loads(result.stdout)
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError):
        return unknown
    manufacturer, model = data.get("Manufacturer"), data.get("Model")
    text = f"{manufacturer or ''} {model or ''}".casefold()
    return {
        "device_manufacturer": manufacturer.strip() if isinstance(manufacturer, str) and manufacturer.strip() else "unknown",
        "device_model": model.strip() if isinstance(model, str) and model.strip() else "unknown",
        "soc": "qualcomm-snapdragon" if "qualcomm" in text or "snapdragon" in text else "unknown",
    }


def hardware_metadata() -> dict[str, Any]:
    """A portable, machine-readable provenance record for competition evidence."""
    onnxruntime_version = "unknown"
    qnn_available = False
    try:
        import onnxruntime as ort

        onnxruntime_version = str(ort.__version__)
        qnn_available = "QNNExecutionProvider" in ort.get_available_providers()
    except ImportError:
        pass
    return {
        **_windows_inventory(),
        "os": platform.system() or "unknown",
        "os_version": platform.version() or "unknown",
        "machine": platform.machine() or "unknown",
        "processor": platform.processor() or "unknown",
        "onnxruntime_version": onnxruntime_version,
        "qnn_execution_provider_available": qnn_available,
        "qnn_version": "unknown",
        "captured_at": datetime.now(UTC).isoformat(),
    }
