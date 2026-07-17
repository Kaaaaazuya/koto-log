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
_FAKE_BABY_FOOD = {
    "id": 4,
    "child_id": 1,
    "type": "baby_food",
    "sub_type": None,
    "amount": 50.0,
    "unit": "g",
    "started_at": "2024-01-01T12:00:00+09:00",
    "ended_at": None,
    "note": None,
    "created_at": "2024-01-01T12:01:00+09:00",
    "updated_at": "2024-01-01T12:01:00+09:00",
}
_FAKE_OUTING = {
    "id": 5,
    "child_id": 1,
    "type": "outing",
    "sub_type": "公園",
    "amount": None,
    "unit": None,
    "started_at": "2024-01-01T15:00:00+09:00",
    "ended_at": None,
    "note": None,
    "created_at": "2024-01-01T15:01:00+09:00",
    "updated_at": "2024-01-01T15:01:00+09:00",
}


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
    with patch("kotolog.line.dashboard._get_conn_and_child", return_value=(fake_conn, 1)):
        from kotolog.line.webhook import app

        yield TestClient(app, raise_server_exceptions=True, follow_redirects=False)


def test_no_session_redirects_to_login(client):
    """Unauthenticated GET /dashboard redirects to /admin/login (303)."""
    resp = client.get("/dashboard")
    assert resp.status_code == 303
    assert resp.headers["location"] == "/admin/login"


def test_query_token_alone_does_not_auth(client):
    """Query token in URL alone no longer grants access (must use session)."""
    resp = client.get("/dashboard?token=wrong")
    assert resp.status_code == 303


def test_session_auth_returns_200(client):
    """Dashboard returns 200 after login (session-based)."""
    with patch("kotolog.db.crud.query_records", return_value=[]):
        _login(client, TOKEN)
        resp = client.get("/dashboard")
    assert resp.status_code == 200


def test_dashboard_renders_feedings(client):
    """Dashboard renders feedings after login."""
    with patch("kotolog.db.crud.query_records", return_value=[_FAKE_FEEDING]):
        _login(client, TOKEN)
        resp = client.get("/dashboard")
    assert resp.status_code == 200
    assert "ミルク" in resp.text
    assert "120" in resp.text


def test_dashboard_renders_empty_state(client):
    """Dashboard renders empty state after login."""
    with patch("kotolog.db.crud.query_records", return_value=[]):
        _login(client, TOKEN)
        resp = client.get("/dashboard")
    assert resp.status_code == 200
    assert "今日の記録はまだありません" in resp.text


def test_dashboard_renders_sleep_tab(client):
    """Dashboard renders sleep tab after login."""
    with patch("kotolog.db.crud.query_records", return_value=[_FAKE_SLEEP]):
        _login(client, TOKEN)
        resp = client.get("/dashboard")
    assert resp.status_code == 200
    assert "睡眠" in resp.text


def test_dashboard_renders_diaper_tab(client):
    """Dashboard renders diaper tab after login."""
    with patch("kotolog.db.crud.query_records", return_value=[_FAKE_DIAPER]):
        _login(client, TOKEN)
        resp = client.get("/dashboard")
    assert resp.status_code == 200
    assert "おむつ" in resp.text


def test_dashboard_renders_timeline(client):
    """今日タブにタイムライン（今日のながれ）が表示される。"""
    with patch("kotolog.db.crud.query_records", return_value=[_FAKE_FEEDING]):
        _login(client, TOKEN)
        resp = client.get("/dashboard")
    assert resp.status_code == 200
    assert "今日のながれ" in resp.text
    assert "🍼" in resp.text


def test_dashboard_accepts_days_param(client):
    """?days=14 が受け付けられ、カードタイトルに期間が反映される。"""
    with patch("kotolog.db.crud.query_records", return_value=[]):
        _login(client, TOKEN)
        resp = client.get("/dashboard?days=14")
    assert resp.status_code == 200
    assert "14日間" in resp.text


def test_dashboard_sleep_summary_str(client):
    """睡眠合計時間がサマリーカードに表示される。"""
    with patch("kotolog.db.crud.query_records", return_value=[_FAKE_SLEEP]):
        _login(client, TOKEN)
        resp = client.get("/dashboard")
    assert resp.status_code == 200
    assert "2h" in resp.text


def test_dashboard_timeline_includes_baby_food(client):
    """Issue #39: 離乳食など後から追加した種別も今日のタイムラインに表示される。"""
    with patch("kotolog.db.crud.query_records", return_value=[_FAKE_BABY_FOOD]):
        _login(client, TOKEN)
        resp = client.get("/dashboard")
    assert resp.status_code == 200
    assert "離乳食" in resp.text
    assert "🍚" in resp.text
    assert "50g" in resp.text


def test_dashboard_timeline_includes_outing_with_sub_type(client):
    """Issue #39: sub_type を持つ新種別（外出）もタイムラインに詳細付きで表示される。"""
    with patch("kotolog.db.crud.query_records", return_value=[_FAKE_OUTING]):
        _login(client, TOKEN)
        resp = client.get("/dashboard")
    assert resp.status_code == 200
    assert "外出" in resp.text
    assert "🚶" in resp.text
    assert "公園" in resp.text


def test_no_session_denies_access(monkeypatch):
    """Issue #27: Default-deny means access denied without session."""
    monkeypatch.setenv("LINE_CHANNEL_SECRET", "secret")
    monkeypatch.setenv("LINE_CHANNEL_ACCESS_TOKEN", "token")
    monkeypatch.delenv("KOTOLOG_DASHBOARD_TOKEN", raising=False)

    fake_conn = MagicMock()
    with patch("kotolog.line.dashboard._get_conn_and_child", return_value=(fake_conn, 1)):
        with patch("kotolog.db.crud.query_records", return_value=[]):
            from kotolog.line.webhook import app

            client = TestClient(app, raise_server_exceptions=True, follow_redirects=False)
            resp = client.get("/dashboard")
    assert resp.status_code == 303
    assert resp.headers["location"] == "/admin/login"


# --- Issue #39: _timeline_label の汎用フォールバック -------------------------


def test_timeline_label_baby_food_with_amount():
    from kotolog.line.dashboard import _timeline_label

    assert _timeline_label({"type": "baby_food", "amount": 50, "unit": "g"}) == "50g"


def test_timeline_label_medicine_with_sub_type():
    from kotolog.line.dashboard import _timeline_label

    assert _timeline_label({"type": "medicine", "sub_type": "ビオフェルミン"}) == "ビオフェルミン"


def test_timeline_label_bath_falls_back_to_type_label():
    from kotolog.line.dashboard import _timeline_label

    assert _timeline_label({"type": "bath"}) == "お風呂"


def test_timeline_label_unknown_type_falls_back_to_raw_type():
    from kotolog.line.dashboard import _timeline_label

    assert _timeline_label({"type": "something_new"}) == "something_new"
