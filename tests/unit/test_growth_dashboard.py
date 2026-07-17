"""P11: 成長記録（身長・体重）ダッシュボードの単体テスト。"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

TOKEN = "test-token"


def _login(client, token=TOKEN):
    """Helper to log in via POST /admin/login."""
    r = client.post("/admin/login", data={"token": token})
    assert r.status_code in (303, 302)
    return client


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setenv("LINE_CHANNEL_SECRET", "secret")
    monkeypatch.setenv("LINE_CHANNEL_ACCESS_TOKEN", "token")
    monkeypatch.setenv("KOTOLOG_DASHBOARD_TOKEN", TOKEN)

    fake_conn = MagicMock()
    fake_conn.execute.return_value.fetchone.return_value = None
    with patch("kotolog.line.dashboard._get_conn_and_child", return_value=(fake_conn, 1)):
        from kotolog.line.webhook import app

        yield TestClient(app, raise_server_exceptions=True, follow_redirects=False)


def test_growth_requires_session(client):
    """/dashboard/growth は セッションなしで /admin/login へリダイレクト。"""
    resp = client.get("/dashboard/growth")
    assert resp.status_code == 303
    assert resp.headers["location"] == "/admin/login"


def test_growth_returns_200_after_login(client):
    """/dashboard/growth はログイン後 200。"""
    _login(client, TOKEN)
    resp = client.get("/dashboard/growth")
    assert resp.status_code == 200


def test_growth_contains_chart(client):
    """成長グラフページに Chart.js キャンバスが含まれる。"""
    _login(client, TOKEN)
    resp = client.get("/dashboard/growth")
    assert "canvas" in resp.text.lower() or "chart" in resp.text.lower()
