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


# --- トークン保護 -----------------------------------------------------------


def test_records_no_token_returns_403(client):
    assert client.get("/admin/records").status_code == 403


def test_records_wrong_token_returns_403(client):
    assert client.get("/admin/records?token=wrong").status_code == 403


def test_create_wrong_token_returns_403(client):
    resp = client.post(
        "/admin/records?token=wrong",
        data={"type": "feeding", "started_at": "2026-06-26T21:30"},
    )
    assert resp.status_code == 403


# --- 一覧 -------------------------------------------------------------------


def test_records_list_renders(client):
    with patch("kotolog.db.crud.query_records", return_value=[_FAKE_RECORD]):
        resp = client.get(f"/admin/records?token={TOKEN}")
    assert resp.status_code == 200
    assert "ミルク" in resp.text
    assert "120" in resp.text
    # 編集/削除リンクが各行にある
    assert "/admin/records/7/edit" in resp.text
    assert "/admin/records/7/delete" in resp.text


def test_records_list_type_filter_passed_to_query(client):
    with patch("kotolog.db.crud.query_records", return_value=[]) as q:
        resp = client.get(f"/admin/records?token={TOKEN}&type=sleep")
    assert resp.status_code == 200
    assert q.call_args.kwargs["type"] == "sleep"


def test_records_list_invalid_type_ignored(client):
    with patch("kotolog.db.crud.query_records", return_value=[]) as q:
        resp = client.get(f"/admin/records?token={TOKEN}&type=bogus")
    assert resp.status_code == 200
    assert q.call_args.kwargs["type"] is None


# --- 追加 -------------------------------------------------------------------


def test_new_form_renders(client):
    resp = client.get(f"/admin/records/new?token={TOKEN}")
    assert resp.status_code == 200
    assert 'name="started_at"' in resp.text
    assert "datetime-local" in resp.text


def test_create_inserts_with_jst_iso(client):
    with patch("kotolog.db.crud.insert_record", return_value=1) as ins:
        resp = client.post(
            f"/admin/records?token={TOKEN}",
            data={
                "type": "feeding",
                "sub_type": "ミルク",
                "amount": "120",
                "unit": "ml",
                "started_at": "2026-06-26T21:30",
                "ended_at": "",
                "note": "memo",
            },
        )
    assert resp.status_code == 303
    assert resp.headers["location"] == f"/admin/records?token={TOKEN}&saved=1"
    kwargs = ins.call_args.kwargs
    assert kwargs["started_at"] == "2026-06-26T21:30:00+09:00"
    assert kwargs["amount"] == 120.0
    assert kwargs["ended_at"] is None
    assert kwargs["child_id"] == 1


def test_create_invalid_type_returns_400(client):
    resp = client.post(
        f"/admin/records?token={TOKEN}",
        data={"type": "bogus", "started_at": "2026-06-26T21:30"},
    )
    assert resp.status_code == 400


def test_create_normalizes_sub_type(client):
    with patch("kotolog.db.crud.insert_record", return_value=1) as ins:
        client.post(
            f"/admin/records?token={TOKEN}",
            data={
                "type": "diaper",
                "sub_type": "便",  # → うんち へ正規化
                "started_at": "2026-06-26T10:00",
            },
        )
    assert ins.call_args.kwargs["sub_type"] == "うんち"


# --- 編集 -------------------------------------------------------------------


def test_edit_form_prefills(client):
    with patch("kotolog.db.crud.get_record", return_value=_FAKE_RECORD):
        resp = client.get(f"/admin/records/7/edit?token={TOKEN}")
    assert resp.status_code == 200
    # ISO -> datetime-local の value にプリフィル
    assert 'value="2026-06-26T21:30"' in resp.text
    assert "ミルク" in resp.text


def test_edit_missing_returns_404(client):
    with patch("kotolog.db.crud.get_record", return_value=None):
        resp = client.get(f"/admin/records/999/edit?token={TOKEN}")
    assert resp.status_code == 404


def test_update_calls_update_record(client):
    with patch("kotolog.db.crud.update_record", return_value=True) as upd:
        resp = client.post(
            f"/admin/records/7?token={TOKEN}",
            data={
                "type": "feeding",
                "sub_type": "ミルク",
                "amount": "100",
                "unit": "ml",
                "started_at": "2026-06-26T22:00",
                "ended_at": "",
                "note": "",
            },
        )
    assert resp.status_code == 303
    _conn, record_id, new_values = upd.call_args.args
    assert record_id == 7
    assert new_values["started_at"] == "2026-06-26T22:00:00+09:00"
    assert new_values["amount"] == 100.0
    assert new_values["note"] is None


# --- 削除 -------------------------------------------------------------------


def test_delete_calls_delete_record(client):
    with patch("kotolog.db.crud.delete_record", return_value=True) as dele:
        resp = client.post(f"/admin/records/7/delete?token={TOKEN}")
    assert resp.status_code == 303
    assert resp.headers["location"] == f"/admin/records?token={TOKEN}&deleted=1"
    assert dele.call_args.args[1] == 7


# --- トークン未設定時 -------------------------------------------------------


def test_no_token_env_allows_access(monkeypatch):
    monkeypatch.setenv("LINE_CHANNEL_SECRET", "secret")
    monkeypatch.setenv("LINE_CHANNEL_ACCESS_TOKEN", "token")
    monkeypatch.delenv("KOTOLOG_DASHBOARD_TOKEN", raising=False)

    fake_conn = MagicMock()
    with patch("kotolog.line.admin._get_conn_and_child", return_value=(fake_conn, 1)):
        with patch("kotolog.db.crud.query_records", return_value=[]):
            from kotolog.line.webhook import app

            resp = TestClient(app, follow_redirects=False).get("/admin/records")
    assert resp.status_code == 200
