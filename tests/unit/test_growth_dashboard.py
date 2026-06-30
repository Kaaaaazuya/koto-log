"""P11: 成長記録（身長・体重）ダッシュボードの単体テスト。"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

TOKEN = "test-token"


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setenv("LINE_CHANNEL_SECRET", "secret")
    monkeypatch.setenv("LINE_CHANNEL_ACCESS_TOKEN", "token")
    monkeypatch.setenv("KOTOLOG_DASHBOARD_TOKEN", TOKEN)

    fake_conn = MagicMock()
    fake_conn.execute.return_value.fetchone.return_value = None
    with patch("kotolog.line.dashboard._get_conn_and_child", return_value=(fake_conn, 1)):
        from kotolog.line.webhook import app

        yield TestClient(app, raise_server_exceptions=True)


def test_growth_requires_token(client):
    """/dashboard/growth はトークンなしで 403。"""
    resp = client.get("/dashboard/growth")
    assert resp.status_code == 403


def test_growth_returns_200(client):
    """/dashboard/growth はトークンありで 200。"""
    resp = client.get(f"/dashboard/growth?token={TOKEN}")
    assert resp.status_code == 200


def test_growth_contains_chart(client):
    """成長グラフページに Chart.js キャンバスが含まれる。"""
    resp = client.get(f"/dashboard/growth?token={TOKEN}")
    assert "canvas" in resp.text.lower() or "chart" in resp.text.lower()
