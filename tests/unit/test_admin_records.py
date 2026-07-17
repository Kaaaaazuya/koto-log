"""管理画面の記録 CRUD（ADR-0003 / P8）の単体テスト。

トークン保護・一覧・追加・編集・削除・時刻変換（datetime-local → JST ISO）を検証する。
AI・LiteLLM は一切経由しない（crud.* 直呼び）。
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

TOKEN = "test-admin-token"

_FAKE_RECORD = {
    "id": 7,
    "child_id": 1,
    "type": "feeding",
    "sub_type": "ミルク",
    "amount": 120.0,
    "unit": "ml",
    "started_at": "2026-06-26T21:30:00+09:00",
    "ended_at": None,
    "note": "memo",
    "created_at": "2026-06-26T21:31:00+09:00",
    "updated_at": "2026-06-26T21:31:00+09:00",
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
    with patch("kotolog.line.admin._get_conn_and_child", return_value=(fake_conn, 1)):
        from kotolog.line.webhook import app

        # redirect を追わず 303 を直接検証できるようにする
        yield TestClient(app, raise_server_exceptions=True, follow_redirects=False)


def _get_csrf_token(client, url):
    """Fetch a form page and extract the CSRF token."""
    import re

    resp = client.get(url)
    match = re.search(r'value="([^"]+)".*?name="csrf_token"', resp.text)
    if not match:
        match = re.search(r'name="csrf_token".*?value="([^"]+)"', resp.text)
    if match:
        return match.group(1)
    return None


# --- トークン保護 -----------------------------------------------------------


def test_records_no_session_redirects_to_login(client):
    """Unauthenticated GET /admin/records redirects to /admin/login."""
    assert client.get("/admin/records").status_code == 303
    assert client.get("/admin/records").headers["location"] == "/admin/login"


def test_records_query_token_alone_no_longer_works(client):
    """Query token alone no longer authenticates."""
    resp = client.get("/admin/records?token=wrong")
    assert resp.status_code == 303


def test_create_no_session_returns_403(client):
    """POST /admin/records without session returns 403."""
    resp = client.post(
        "/admin/records",
        data={"type": "feeding", "started_at": "2026-06-26T21:30"},
    )
    assert resp.status_code == 403


# --- 一覧 -------------------------------------------------------------------


def test_records_list_renders(client):
    _login(client, TOKEN)
    with patch("kotolog.db.crud.query_records", return_value=[_FAKE_RECORD]):
        resp = client.get("/admin/records")
    assert resp.status_code == 200
    assert "ミルク" in resp.text
    assert "120" in resp.text
    # 編集/削除リンクが各行にあり、トークンなしになった
    assert "/admin/records/7/edit" in resp.text
    assert "/admin/records/7/delete" in resp.text
    assert "?token=" not in resp.text  # No token in URLs


def test_records_list_type_filter_passed_to_query(client):
    _login(client, TOKEN)
    with patch("kotolog.db.crud.query_records", return_value=[]) as q:
        resp = client.get("/admin/records?type=sleep")
    assert resp.status_code == 200
    assert q.call_args.kwargs["type"] == "sleep"


def test_records_list_invalid_type_ignored(client):
    _login(client, TOKEN)
    with patch("kotolog.db.crud.query_records", return_value=[]) as q:
        resp = client.get("/admin/records?type=bogus")
    assert resp.status_code == 200
    assert q.call_args.kwargs["type"] is None


# --- 追加 -------------------------------------------------------------------


def test_new_form_renders(client):
    _login(client, TOKEN)
    resp = client.get("/admin/records/new")
    assert resp.status_code == 200
    assert 'name="started_at"' in resp.text
    assert "datetime-local" in resp.text


def test_create_inserts_with_jst_iso(client):
    _login(client, TOKEN)
    csrf_token = _get_csrf_token(client, "/admin/records/new")
    with patch("kotolog.db.crud.insert_record", return_value=1) as ins:
        resp = client.post(
            "/admin/records",
            data={
                "type": "feeding",
                "sub_type": "ミルク",
                "amount": "120",
                "unit": "ml",
                "started_at": "2026-06-26T21:30",
                "ended_at": "",
                "note": "memo",
                "csrf_token": csrf_token,
            },
        )
    assert resp.status_code == 303
    assert resp.headers["location"] == "/admin/records?saved=1"
    kwargs = ins.call_args.kwargs
    assert kwargs["started_at"] == "2026-06-26T21:30:00+09:00"
    assert kwargs["amount"] == 120.0
    assert kwargs["ended_at"] is None
    assert kwargs["child_id"] == 1


def test_create_invalid_type_returns_400(client):
    _login(client, TOKEN)
    csrf_token = _get_csrf_token(client, "/admin/records/new")
    resp = client.post(
        "/admin/records",
        data={"type": "bogus", "started_at": "2026-06-26T21:30", "csrf_token": csrf_token},
    )
    assert resp.status_code == 400


def test_create_normalizes_sub_type(client):
    _login(client, TOKEN)
    csrf_token = _get_csrf_token(client, "/admin/records/new")
    with patch("kotolog.db.crud.insert_record", return_value=1) as ins:
        client.post(
            "/admin/records",
            data={
                "type": "diaper",
                "sub_type": "便",  # → うんち へ正規化
                "started_at": "2026-06-26T10:00",
                "csrf_token": csrf_token,
            },
        )
    assert ins.call_args.kwargs["sub_type"] == "うんち"


# --- 編集 -------------------------------------------------------------------


def test_edit_form_prefills(client):
    _login(client, TOKEN)
    with patch("kotolog.db.crud.get_record", return_value=_FAKE_RECORD):
        resp = client.get("/admin/records/7/edit")
    assert resp.status_code == 200
    # ISO -> datetime-local の value にプリフィル
    assert 'value="2026-06-26T21:30"' in resp.text
    assert "ミルク" in resp.text


def test_edit_missing_returns_404(client):
    _login(client, TOKEN)
    with patch("kotolog.db.crud.get_record", return_value=None):
        resp = client.get("/admin/records/999/edit")
    assert resp.status_code == 404


def test_update_calls_update_record(client):
    _login(client, TOKEN)
    with patch("kotolog.db.crud.get_record", return_value=_FAKE_RECORD):
        csrf_token = _get_csrf_token(client, "/admin/records/7/edit")
    with patch("kotolog.db.crud.update_record", return_value=True) as upd:
        resp = client.post(
            "/admin/records/7",
            data={
                "type": "feeding",
                "sub_type": "ミルク",
                "amount": "100",
                "unit": "ml",
                "started_at": "2026-06-26T22:00",
                "ended_at": "",
                "note": "",
                "csrf_token": csrf_token,
            },
        )
    assert resp.status_code == 303
    _conn, record_id, new_values = upd.call_args.args
    assert record_id == 7
    assert new_values["started_at"] == "2026-06-26T22:00:00+09:00"
    assert new_values["amount"] == 100.0
    assert new_values["note"] is None


# --- 削除 -------------------------------------------------------------------


def test_create_invalid_date_returns_400(client):
    _login(client, TOKEN)
    csrf_token = _get_csrf_token(client, "/admin/records/new")
    resp = client.post(
        "/admin/records",
        data={"type": "feeding", "started_at": "not-a-date", "csrf_token": csrf_token},
    )
    assert resp.status_code == 400


def test_create_ended_before_started_returns_400(client):
    _login(client, TOKEN)
    csrf_token = _get_csrf_token(client, "/admin/records/new")
    resp = client.post(
        "/admin/records",
        data={
            "type": "sleep",
            "started_at": "2026-06-26T10:00",
            "ended_at": "2026-06-26T09:00",
            "csrf_token": csrf_token,
        },
    )
    assert resp.status_code == 400


def test_update_ended_before_started_returns_400(client):
    _login(client, TOKEN)
    with patch("kotolog.db.crud.get_record", return_value=_FAKE_RECORD):
        csrf_token = _get_csrf_token(client, "/admin/records/7/edit")
    resp = client.post(
        "/admin/records/7",
        data={
            "type": "sleep",
            "started_at": "2026-06-26T10:00",
            "ended_at": "2026-06-26T09:00",
            "csrf_token": csrf_token,
        },
    )
    assert resp.status_code == 400


def test_delete_calls_delete_record(client):
    _login(client, TOKEN)
    with patch("kotolog.db.crud.query_records", return_value=[_FAKE_RECORD]):
        csrf_token = _get_csrf_token(client, "/admin/records")
    with patch("kotolog.db.crud.delete_record", return_value=True) as dele:
        resp = client.post("/admin/records/7/delete", data={"csrf_token": csrf_token})
    assert resp.status_code == 303
    assert resp.headers["location"] == "/admin/records?deleted=1"
    assert dele.call_args.args[1] == 7


# --- トークン未設定時 -------------------------------------------------------


def test_no_session_denies_access(monkeypatch):
    """Issue #27: Default-deny means access denied without session."""
    monkeypatch.setenv("LINE_CHANNEL_SECRET", "secret")
    monkeypatch.setenv("LINE_CHANNEL_ACCESS_TOKEN", "token")
    monkeypatch.delenv("KOTOLOG_DASHBOARD_TOKEN", raising=False)

    fake_conn = MagicMock()
    with patch("kotolog.line.admin._get_conn_and_child", return_value=(fake_conn, 1)):
        with patch("kotolog.db.crud.query_records", return_value=[]):
            from kotolog.line.webhook import app

            resp = TestClient(app, follow_redirects=False).get("/admin/records")
    assert resp.status_code == 303
    assert resp.headers["location"] == "/admin/login"
