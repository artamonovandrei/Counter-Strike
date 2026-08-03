"""Prometheus /metrics exposition used by the monitoring stack."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import api


def test_prometheus_metrics_exposition():
    with TestClient(api) as client:
        r = client.get("/metrics")
    assert r.status_code == 200
    body = r.text
    assert "webstrike_up 1" in body
    assert "webstrike_rooms" in body
    assert "webstrike_players" in body
    assert "# TYPE webstrike_uptime_seconds gauge" in body


def test_json_metrics_still_available():
    with TestClient(api) as client:
        r = client.get("/api/metrics")
    assert r.status_code == 200
    data = r.json()
    assert "rooms" in data
    assert "uptime" in data
