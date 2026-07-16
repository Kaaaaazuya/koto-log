"""Static assets serving and integration test (Issue #98).

Verifies that Chart.js is served from /static and referenced in templates.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

TOKEN = "test-dashboard-token"


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setenv("LINE_CHANNEL_SECRET", "secret")
    monkeypatch.setenv("LINE_CHANNEL_ACCESS_TOKEN", "token")
    monkeypatch.setenv("KOTOLOG_DASHBOARD_TOKEN", TOKEN)

    fake_conn = MagicMock()
    with patch("kotolog.line.dashboard._get_conn_and_child", return_value=(fake_conn, 1)):
        from kotolog.line.webhook import app

        yield TestClient(app, raise_server_exceptions=True)


def test_static_chart_js_returns_200(client):
    """GET /static/chart.umd.min.js returns 200 and JavaScript content-type."""
    resp = client.get("/static/chart.umd.min.js")
    assert resp.status_code == 200
    assert "javascript" in resp.headers.get("content-type", "").lower()


def test_static_chart_js_contains_chart_version(client):
    """The served chart.js file contains Chart.js v4.4.0 marker."""
    resp = client.get("/static/chart.umd.min.js")
    assert resp.status_code == 200
    assert "Chart.js v4.4.0" in resp.text


def test_dashboard_references_local_chart_js(client):
    """Dashboard HTML references /static/chart.umd.min.js, not CDN."""
    with patch("kotolog.db.crud.query_records", return_value=[]):
        resp = client.get(f"/dashboard?token={TOKEN}")
    assert resp.status_code == 200
    assert "/static/chart.umd.min.js" in resp.text
    assert "cdn.jsdelivr.net" not in resp.text


def test_growth_dashboard_references_local_chart_js(client):
    """Growth dashboard HTML references /static/chart.umd.min.js, not CDN."""
    resp = client.get(f"/dashboard/growth?token={TOKEN}")
    assert resp.status_code == 200
    assert "/static/chart.umd.min.js" in resp.text
    assert "cdn.jsdelivr.net" not in resp.text
