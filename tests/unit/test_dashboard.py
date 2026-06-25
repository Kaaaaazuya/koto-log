"""ダッシュボード: トークン認証と画面レンダリングの単体テスト。"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

TOKEN = "test-dashboard-token"

_FAKE_FEEDING = {
    "id": 1,
    "child_id": 1,
    "type": "feeding",
    "sub_type": "ミルク",
    "amount": 120.0,
    "unit": "ml",
    "started_at": "2024-01-01T14:00:00+09:00",
    "ended_at": None,
    "note": None,
    "created_at": "2024-01-01T14:01:00+09:00",
    "updated_at": "2024-01-01T14:01:00+09:00",
}
_FAKE_SLEEP = {
    "id": 2,
    "child_id": 1,
    "type": "sleep",
    "sub_type": None,
    "amount": None,
    "unit": None,
    "started_at": "2024-01-01T21:00:00+09:00",
    "ended_at": "2024-01-01T23:00:00+09:00",
    "note": None,
    "created_at": "2024-01-01T21:01:00+09:00",
    "updated_at": "2024-01-01T21:01:00+09:00",
}
_FAKE_DIAPER = {
    "id": 3,
    "child_id": 1,
    "type": "diaper",
    "sub_type": "うんち",
    "amount": None,
    "unit": None,
    "started_at": "2024-01-01T10:00:00+09:00",
    "ended_at": None,
    "note": None,
    "created_at": "2024-01-01T10:01:00+09:00",
    "updated_at": "2024-01-01T10:01:00+09:00",
}


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setenv("LINE_CHANNEL_SECRET", "secret")
    monkeypatch.setenv("LINE_CHANNEL_ACCESS_TOKEN", "token")
    monkeypatch.setenv("KOTOLOG_DASHBOARD_TOKEN", TOKEN)

    fake_conn = MagicMock()
    with patch("kotolog.line.dashboard._get_conn_and_child", return_value=(fake_conn, 1)):
        from kotolog.line.webhook import app

        yield TestClient(app, raise_server_exceptions=True)


def test_no_token_returns_403(client):
    resp = client.get("/dashboard")
    assert resp.status_code == 403


def test_wrong_token_returns_403(client):
    resp = client.get("/dashboard?token=wrong")
    assert resp.status_code == 403


def test_valid_token_returns_200(client):
    with patch("kotolog.db.crud.query_records", return_value=[]):
        resp = client.get(f"/dashboard?token={TOKEN}")
    assert resp.status_code == 200


def test_dashboard_renders_feedings(client):
    with patch("kotolog.db.crud.query_records", return_value=[_FAKE_FEEDING]):
        resp = client.get(f"/dashboard?token={TOKEN}")
    assert resp.status_code == 200
    assert "ミルク" in resp.text
    assert "120" in resp.text


def test_dashboard_renders_empty_state(client):
    with patch("kotolog.db.crud.query_records", return_value=[]):
        resp = client.get(f"/dashboard?token={TOKEN}")
    assert resp.status_code == 200
    assert "今日の記録はまだありません" in resp.text


def test_dashboard_renders_sleep_tab(client):
    with patch("kotolog.db.crud.query_records", return_value=[_FAKE_SLEEP]):
        resp = client.get(f"/dashboard?token={TOKEN}")
    assert resp.status_code == 200
    assert "睡眠" in resp.text


def test_dashboard_renders_diaper_tab(client):
    with patch("kotolog.db.crud.query_records", return_value=[_FAKE_DIAPER]):
        resp = client.get(f"/dashboard?token={TOKEN}")
    assert resp.status_code == 200
    assert "おむつ" in resp.text


def test_dashboard_renders_timeline(client):
    """今日タブにタイムライン（今日のながれ）が表示される。"""
    with patch("kotolog.db.crud.query_records", return_value=[_FAKE_FEEDING]):
        resp = client.get(f"/dashboard?token={TOKEN}")
    assert resp.status_code == 200
    assert "今日のながれ" in resp.text
    assert "🍼" in resp.text


def test_dashboard_accepts_days_param(client):
    """?days=14 が受け付けられ、カードタイトルに期間が反映される。"""
    with patch("kotolog.db.crud.query_records", return_value=[]):
        resp = client.get(f"/dashboard?token={TOKEN}&days=14")
    assert resp.status_code == 200
    assert "14日間" in resp.text


def test_dashboard_sleep_summary_str(client):
    """睡眠合計時間がサマリーカードに表示される。"""
    with patch("kotolog.db.crud.query_records", return_value=[_FAKE_SLEEP]):
        resp = client.get(f"/dashboard?token={TOKEN}")
    assert resp.status_code == 200
    assert "2h" in resp.text


def test_no_token_env_allows_access(monkeypatch):
    """KOTOLOG_DASHBOARD_TOKEN 未設定時はトークンなしでアクセス可。"""
    monkeypatch.setenv("LINE_CHANNEL_SECRET", "secret")
    monkeypatch.setenv("LINE_CHANNEL_ACCESS_TOKEN", "token")
    monkeypatch.delenv("KOTOLOG_DASHBOARD_TOKEN", raising=False)

    fake_conn = MagicMock()
    with patch("kotolog.line.dashboard._get_conn_and_child", return_value=(fake_conn, 1)):
        with patch("kotolog.db.crud.query_records", return_value=[]):
            from kotolog.line.webhook import app

            client = TestClient(app, raise_server_exceptions=True)
            resp = client.get("/dashboard")
    assert resp.status_code == 200
