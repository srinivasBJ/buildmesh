from __future__ import annotations

import os
import smtplib
from dataclasses import dataclass
from datetime import UTC, datetime
from email.message import EmailMessage
from typing import Any, Protocol

import httpx


class ConnectorError(RuntimeError):
    pass


class WeatherClient(Protocol):
    def forecast(self, latitude: float, longitude: float, horizon_hours: int = 48) -> dict[str, Any]: ...


class OpenMeteoWeatherClient:
    """Fetches sourced hourly forecast context from Open-Meteo's public forecast API."""

    endpoint = "https://api.open-meteo.com/v1/forecast"

    def __init__(self, timeout_seconds: float = 12.0) -> None:
        self.timeout_seconds = timeout_seconds

    def forecast(self, latitude: float, longitude: float, horizon_hours: int = 48) -> dict[str, Any]:
        if not -90 <= latitude <= 90 or not -180 <= longitude <= 180:
            raise ValueError("coordinates are outside WGS84 bounds")
        horizon_hours = max(1, min(horizon_hours, 168))
        params = {
            "latitude": latitude,
            "longitude": longitude,
            "hourly": "precipitation_probability,precipitation,temperature_2m,wind_speed_10m",
            "forecast_hours": horizon_hours,
            "timezone": "auto",
        }
        try:
            response = httpx.get(self.endpoint, params=params, timeout=self.timeout_seconds)
            response.raise_for_status()
            raw = response.json()
        except httpx.HTTPError as exc:
            raise ConnectorError("weather provider request failed") from exc

        hourly = raw.get("hourly", {})
        probabilities = hourly.get("precipitation_probability") or []
        precipitation = hourly.get("precipitation") or []
        times = hourly.get("time") or []
        if not probabilities or not times:
            raise ConnectorError("weather provider returned no hourly precipitation probability")
        peak_index = max(range(len(probabilities)), key=lambda index: probabilities[index])
        return {
            "provider": "open-meteo",
            "retrieved_at": datetime.now(UTC).isoformat(),
            "latitude": latitude,
            "longitude": longitude,
            "horizon_hours": horizon_hours,
            "rain_probability": float(probabilities[peak_index]) / 100,
            "hours_until": peak_index,
            "peak_at": times[peak_index],
            "precipitation_total_mm": round(sum(float(value or 0) for value in precipitation), 2),
            "source_payload": raw,
        }


class Notifier(Protocol):
    def send(self, recipient: str, subject: str, body: str) -> dict[str, str]: ...


@dataclass
class SMTPNotifier:
    """Optional outbound adapter. It is dormant until all SMTP settings are present."""

    host: str
    port: int
    sender: str
    username: str | None = None
    password: str | None = None

    @classmethod
    def from_environment(cls) -> SMTPNotifier | None:
        host, sender = os.getenv("BUILDMESH_SMTP_HOST"), os.getenv("BUILDMESH_SMTP_SENDER")
        if not host or not sender:
            return None
        return cls(host=host, port=int(os.getenv("BUILDMESH_SMTP_PORT", "587")), sender=sender, username=os.getenv("BUILDMESH_SMTP_USERNAME"), password=os.getenv("BUILDMESH_SMTP_PASSWORD"))

    def send(self, recipient: str, subject: str, body: str) -> dict[str, str]:
        message = EmailMessage()
        message["From"], message["To"], message["Subject"] = self.sender, recipient, subject
        message.set_content(body)
        try:
            with smtplib.SMTP(self.host, self.port, timeout=15) as smtp:
                smtp.starttls()
                if self.username and self.password:
                    smtp.login(self.username, self.password)
                smtp.send_message(message)
        except OSError as exc:
            raise ConnectorError("notification delivery failed") from exc
        return {"status": "sent", "channel": "smtp", "recipient": recipient}
