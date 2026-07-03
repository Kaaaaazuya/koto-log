"""CSRF 保護のテスト（Issue #32）。

管理画面のすべての状態変更エンドポイント（POST）に CSRF トークンを要求する。
トークンはリクエストごとに生成され、フォーム送信時に検証される。
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

_FAKE_USER = {
    "id": 1,
    "line_user_id": "U123456789abcdefghijklmnopqrstu",
    "nickname": "Alice",
    "notify_enabled": True,
    "current_child_id": 1,
}

_FAKE_CHILD = {
    "id": 1,
    "name": "baby",
    "name_alias": "baby",
    "birthday": "2025-01-01",
    "sex": "male",
    "created_at": "2026-01-01T00:00:00+09:00",
}


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setenv("LINE_CHANNEL_SECRET", "secret")
    monkeypatch.setenv("LINE_CHANNEL_ACCESS_TOKEN", "token")
    monkeypatch.setenv("KOTOLOG_DASHBOARD_TOKEN", TOKEN)

    fake_conn = MagicMock()
    with patch("kotolog.line.admin._get_conn_and_child", return_value=(fake_conn, 1)):
        with patch("kotolog.line.admin._get_conn", return_value=fake_conn):
            from kotolog.line.webhook import app

            # redirect を追わず 303 を直接検証できるようにする
            yield TestClient(app, raise_server_exceptions=True, follow_redirects=False)


# --- CSRF トークン生成と注入 --------------------------------------------------


def test_get_admin_page_includes_csrf_token(client):
    """admin ページ（GET）には CSRF トークンが含まれる。"""
    with patch("kotolog.db.crud.get_setting", return_value=None):
        resp = client.get(f"/admin?token={TOKEN}")
    assert resp.status_code == 200
    # CSRF トークンがフォームに含まれている
    assert 'name="csrf_token"' in resp.text


def test_get_records_page_includes_csrf_token(client):
    """記録一覧ページ（GET）には CSRF トークンが含まれる。"""
    with patch("kotolog.db.crud.query_records", return_value=[_FAKE_RECORD]):
        resp = client.get(f"/admin/records?token={TOKEN}")
    assert resp.status_code == 200
    assert 'name="csrf_token"' in resp.text


def test_get_record_new_form_includes_csrf_token(client):
    """記録新規追加フォーム（GET）には CSRF トークンが含まれる。"""
    resp = client.get(f"/admin/records/new?token={TOKEN}")
    assert resp.status_code == 200
    assert 'name="csrf_token"' in resp.text


def test_get_record_edit_form_includes_csrf_token(client):
    """記録編集フォーム（GET）には CSRF トークンが含まれる。"""
    with patch("kotolog.db.crud.get_record", return_value=_FAKE_RECORD):
        resp = client.get(f"/admin/records/7/edit?token={TOKEN}")
    assert resp.status_code == 200
    assert 'name="csrf_token"' in resp.text


def test_get_users_page_includes_csrf_token(client):
    """ユーザー管理ページ（GET）には CSRF トークンが含まれる。"""
    with patch("kotolog.db.crud.list_users", return_value=[_FAKE_USER]):
        with patch("kotolog.db.crud.list_children", return_value=[_FAKE_CHILD]):
            resp = client.get(f"/admin/users?token={TOKEN}")
    assert resp.status_code == 200
    assert 'name="csrf_token"' in resp.text


# --- POST エンドポイント: CSRF トークン必須 ---------------------------------


def test_post_admin_without_csrf_token_returns_403(client):
    """POST /admin（設定保存）: CSRF トークンなしで 403。"""
    resp = client.post(
        f"/admin?token={TOKEN}",
        data={
            "due_date": "2026-06-30",
            "line_user_id": "U123",
        },
    )
    assert resp.status_code == 403


def test_post_admin_with_invalid_csrf_token_returns_403(client):
    """POST /admin（設定保存）: 無効な CSRF トークンで 403。"""
    resp = client.post(
        f"/admin?token={TOKEN}",
        data={
            "due_date": "2026-06-30",
            "line_user_id": "U123",
            "csrf_token": "invalid-token",
        },
    )
    assert resp.status_code == 403


def test_post_admin_with_valid_csrf_token_succeeds(client):
    """POST /admin（設定保存）: 正しい CSRF トークンで成功。"""
    # Step 1: GET ページから CSRF トークンを取得
    with patch("kotolog.db.crud.get_setting", return_value=None):
        resp_get = client.get(f"/admin?token={TOKEN}")
    assert resp_get.status_code == 200
    # response.text から CSRF トークンを抽出
    import re

    match = re.search(r'value="([^"]+)".*?name="csrf_token"', resp_get.text)
    if not match:
        # 異なる順序の場合
        match = re.search(r'name="csrf_token".*?value="([^"]+)"', resp_get.text)
    assert match, "CSRF token not found in response"
    csrf_token = match.group(1)

    # Step 2: POST フォームに CSRF トークンを含める
    with patch("kotolog.db.crud.set_setting", return_value=None):
        resp_post = client.post(
            f"/admin?token={TOKEN}",
            data={
                "due_date": "2026-06-30",
                "line_user_id": "U123",
                "csrf_token": csrf_token,
            },
        )
    assert resp_post.status_code == 303
    assert f"/admin?token={TOKEN}&saved=1" in resp_post.headers.get("location", "")


def test_post_test_push_without_csrf_token_returns_403(client):
    """POST /admin/test-push: CSRF トークンなしで 403。"""
    resp = client.post(f"/admin/test-push?token={TOKEN}")
    assert resp.status_code == 403


def test_post_test_push_with_invalid_csrf_token_returns_403(client):
    """POST /admin/test-push: 無効な CSRF トークンで 403。"""
    resp = client.post(
        f"/admin/test-push?token={TOKEN}",
        data={"csrf_token": "invalid-token"},
    )
    assert resp.status_code == 403


def test_post_test_push_with_valid_csrf_token_succeeds(client):
    """POST /admin/test-push: 正しい CSRF トークンで成功。"""
    import re

    # GET ページから CSRF トークンを取得
    with patch("kotolog.db.crud.get_setting", return_value=None):
        resp_get = client.get(f"/admin?token={TOKEN}")
    match = re.search(r'value="([^"]+)".*?name="csrf_token"', resp_get.text)
    if not match:
        match = re.search(r'name="csrf_token".*?value="([^"]+)"', resp_get.text)
    csrf_token = match.group(1)

    # POST フォームに CSRF トークンを含める
    with patch("asyncio.to_thread", return_value=None):
        resp_post = client.post(
            f"/admin/test-push?token={TOKEN}",
            data={"csrf_token": csrf_token},
        )
    assert resp_post.status_code == 303


def test_post_record_create_without_csrf_token_returns_403(client):
    """POST /admin/records（記録追加）: CSRF トークンなしで 403。"""
    resp = client.post(
        f"/admin/records?token={TOKEN}",
        data={
            "type": "feeding",
            "started_at": "2026-06-26T21:30",
        },
    )
    assert resp.status_code == 403


def test_post_record_create_with_invalid_csrf_token_returns_403(client):
    """POST /admin/records（記録追加）: 無効な CSRF トークンで 403。"""
    resp = client.post(
        f"/admin/records?token={TOKEN}",
        data={
            "type": "feeding",
            "started_at": "2026-06-26T21:30",
            "csrf_token": "invalid-token",
        },
    )
    assert resp.status_code == 403


def test_post_record_create_with_valid_csrf_token_succeeds(client):
    """POST /admin/records（記録追加）: 正しい CSRF トークンで成功。"""
    import re

    # GET フォームから CSRF トークンを取得
    resp_get = client.get(f"/admin/records/new?token={TOKEN}")
    match = re.search(r'value="([^"]+)".*?name="csrf_token"', resp_get.text)
    if not match:
        match = re.search(r'name="csrf_token".*?value="([^"]+)"', resp_get.text)
    csrf_token = match.group(1)

    # POST フォームに CSRF トークンを含める
    with patch("kotolog.db.crud.insert_record", return_value=1):
        resp_post = client.post(
            f"/admin/records?token={TOKEN}",
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
    assert resp_post.status_code == 303


def test_post_record_update_without_csrf_token_returns_403(client):
    """POST /admin/records/{id}（記録編集）: CSRF トークンなしで 403。"""
    resp = client.post(
        f"/admin/records/7?token={TOKEN}",
        data={
            "type": "feeding",
            "started_at": "2026-06-26T21:30",
        },
    )
    assert resp.status_code == 403


def test_post_record_update_with_invalid_csrf_token_returns_403(client):
    """POST /admin/records/{id}（記録編集）: 無効な CSRF トークンで 403。"""
    resp = client.post(
        f"/admin/records/7?token={TOKEN}",
        data={
            "type": "feeding",
            "started_at": "2026-06-26T21:30",
            "csrf_token": "invalid-token",
        },
    )
    assert resp.status_code == 403


def test_post_record_update_with_valid_csrf_token_succeeds(client):
    """POST /admin/records/{id}（記録編集）: 正しい CSRF トークンで成功。"""
    import re

    # GET フォームから CSRF トークンを取得
    with patch("kotolog.db.crud.get_record", return_value=_FAKE_RECORD):
        resp_get = client.get(f"/admin/records/7/edit?token={TOKEN}")
    match = re.search(r'value="([^"]+)".*?name="csrf_token"', resp_get.text)
    if not match:
        match = re.search(r'name="csrf_token".*?value="([^"]+)"', resp_get.text)
    csrf_token = match.group(1)

    # POST フォームに CSRF トークンを含める
    with patch("kotolog.db.crud.update_record", return_value=True):
        resp_post = client.post(
            f"/admin/records/7?token={TOKEN}",
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
    assert resp_post.status_code == 303


def test_post_record_delete_without_csrf_token_returns_403(client):
    """POST /admin/records/{id}/delete（記録削除）: CSRF トークンなしで 403。"""
    resp = client.post(f"/admin/records/7/delete?token={TOKEN}")
    assert resp.status_code == 403


def test_post_record_delete_with_invalid_csrf_token_returns_403(client):
    """POST /admin/records/{id}/delete（記録削除）: 無効な CSRF トークンで 403。"""
    resp = client.post(
        f"/admin/records/7/delete?token={TOKEN}",
        data={"csrf_token": "invalid-token"},
    )
    assert resp.status_code == 403


def test_post_record_delete_with_valid_csrf_token_succeeds(client):
    """POST /admin/records/{id}/delete（記録削除）: 正しい CSRF トークンで成功。"""
    import re

    # GET 一覧ページから CSRF トークンを取得
    with patch("kotolog.db.crud.query_records", return_value=[_FAKE_RECORD]):
        resp_get = client.get(f"/admin/records?token={TOKEN}")
    match = re.search(r'value="([^"]+)".*?name="csrf_token"', resp_get.text)
    if not match:
        match = re.search(r'name="csrf_token".*?value="([^"]+)"', resp_get.text)
    csrf_token = match.group(1)

    # POST フォームに CSRF トークンを含める
    with patch("kotolog.db.crud.delete_record", return_value=True):
        resp_post = client.post(
            f"/admin/records/7/delete?token={TOKEN}",
            data={"csrf_token": csrf_token},
        )
    assert resp_post.status_code == 303


def test_post_user_nickname_without_csrf_token_returns_403(client):
    """POST /admin/users/{id}/nickname: CSRF トークンなしで 403。"""
    resp = client.post(
        f"/admin/users/U123/nickname?token={TOKEN}",
        data={"nickname": "Alice"},
    )
    assert resp.status_code == 403


def test_post_user_nickname_with_invalid_csrf_token_returns_403(client):
    """POST /admin/users/{id}/nickname: 無効な CSRF トークンで 403。"""
    resp = client.post(
        f"/admin/users/U123/nickname?token={TOKEN}",
        data={
            "nickname": "Alice",
            "csrf_token": "invalid-token",
        },
    )
    assert resp.status_code == 403


def test_post_user_nickname_with_valid_csrf_token_succeeds(client):
    """POST /admin/users/{id}/nickname: 正しい CSRF トークンで成功。"""
    import re

    # GET ページから CSRF トークンを取得
    with patch("kotolog.db.crud.list_users", return_value=[_FAKE_USER]):
        with patch("kotolog.db.crud.list_children", return_value=[_FAKE_CHILD]):
            resp_get = client.get(f"/admin/users?token={TOKEN}")
    match = re.search(r'value="([^"]+)".*?name="csrf_token"', resp_get.text)
    if not match:
        match = re.search(r'name="csrf_token".*?value="([^"]+)"', resp_get.text)
    csrf_token = match.group(1)

    # POST フォームに CSRF トークンを含める
    with patch("kotolog.db.crud.set_user_nickname", return_value=None):
        resp_post = client.post(
            f"/admin/users/U123/nickname?token={TOKEN}",
            data={
                "nickname": "Alice",
                "csrf_token": csrf_token,
            },
        )
    assert resp_post.status_code == 303


def test_post_user_notify_without_csrf_token_returns_403(client):
    """POST /admin/users/{id}/notify: CSRF トークンなしで 403。"""
    resp = client.post(
        f"/admin/users/U123/notify?token={TOKEN}",
        data={"enabled": "0"},
    )
    assert resp.status_code == 403


def test_post_user_notify_with_valid_csrf_token_succeeds(client):
    """POST /admin/users/{id}/notify: 正しい CSRF トークンで成功。"""
    import re

    # GET ページから CSRF トークンを取得
    with patch("kotolog.db.crud.list_users", return_value=[_FAKE_USER]):
        with patch("kotolog.db.crud.list_children", return_value=[_FAKE_CHILD]):
            resp_get = client.get(f"/admin/users?token={TOKEN}")
    match = re.search(r'value="([^"]+)".*?name="csrf_token"', resp_get.text)
    if not match:
        match = re.search(r'name="csrf_token".*?value="([^"]+)"', resp_get.text)
    csrf_token = match.group(1)

    # POST フォームに CSRF トークンを含める
    with patch("kotolog.db.crud.update_user_notify", return_value=None):
        resp_post = client.post(
            f"/admin/users/U123/notify?token={TOKEN}",
            data={
                "enabled": "0",
                "csrf_token": csrf_token,
            },
        )
    assert resp_post.status_code == 303


def test_post_user_child_without_csrf_token_returns_403(client):
    """POST /admin/users/{id}/child: CSRF トークンなしで 403。"""
    resp = client.post(
        f"/admin/users/U123/child?token={TOKEN}",
        data={"child_id": "1"},
    )
    assert resp.status_code == 403


def test_post_user_child_with_valid_csrf_token_succeeds(client):
    """POST /admin/users/{id}/child: 正しい CSRF トークンで成功。"""
    import re

    # GET ページから CSRF トークンを取得
    with patch("kotolog.db.crud.list_users", return_value=[_FAKE_USER]):
        with patch("kotolog.db.crud.list_children", return_value=[_FAKE_CHILD]):
            resp_get = client.get(f"/admin/users?token={TOKEN}")
    match = re.search(r'value="([^"]+)".*?name="csrf_token"', resp_get.text)
    if not match:
        match = re.search(r'name="csrf_token".*?value="([^"]+)"', resp_get.text)
    csrf_token = match.group(1)

    # POST フォームに CSRF トークンを含める
    with patch("kotolog.db.crud.set_user_current_child", return_value=None):
        resp_post = client.post(
            f"/admin/users/U123/child?token={TOKEN}",
            data={
                "child_id": "1",
                "csrf_token": csrf_token,
            },
        )
    assert resp_post.status_code == 303


def test_post_user_delete_without_csrf_token_returns_403(client):
    """POST /admin/users/{id}/delete: CSRF トークンなしで 403。"""
    resp = client.post(f"/admin/users/U123/delete?token={TOKEN}")
    assert resp.status_code == 403


def test_post_user_delete_with_valid_csrf_token_succeeds(client):
    """POST /admin/users/{id}/delete: 正しい CSRF トークンで成功。"""
    import re

    # GET ページから CSRF トークンを取得
    with patch("kotolog.db.crud.list_users", return_value=[_FAKE_USER]):
        with patch("kotolog.db.crud.list_children", return_value=[_FAKE_CHILD]):
            resp_get = client.get(f"/admin/users?token={TOKEN}")
    match = re.search(r'value="([^"]+)".*?name="csrf_token"', resp_get.text)
    if not match:
        match = re.search(r'name="csrf_token".*?value="([^"]+)"', resp_get.text)
    csrf_token = match.group(1)

    # POST フォームに CSRF トークンを含める
    with patch("kotolog.db.crud.delete_user", return_value=None):
        resp_post = client.post(
            f"/admin/users/U123/delete?token={TOKEN}",
            data={"csrf_token": csrf_token},
        )
    assert resp_post.status_code == 303


# --- CSRF トークン有効性 ---------------------------------------------------


def test_csrf_token_is_unique_per_session(client):
    """CSRF トークンはセッションごとに一貫している（セッション内では同じ）。"""
    import re

    with patch("kotolog.db.crud.get_setting", return_value=None):
        resp1 = client.get(f"/admin?token={TOKEN}")
        resp2 = client.get(f"/admin?token={TOKEN}")

    match1 = re.search(r'value="([^"]+)".*?name="csrf_token"', resp1.text)
    if not match1:
        match1 = re.search(r'name="csrf_token".*?value="([^"]+)"', resp1.text)
    token1 = match1.group(1)

    match2 = re.search(r'value="([^"]+)".*?name="csrf_token"', resp2.text)
    if not match2:
        match2 = re.search(r'name="csrf_token".*?value="([^"]+)"', resp2.text)
    token2 = match2.group(1)

    # 同じセッション内でトークンが一貫していることを確認
    assert token1 == token2, "CSRF tokens should be consistent per session"


def test_csrf_token_from_different_endpoint_accepted(client):
    """異なるエンドポイントから取得したトークンはセッション内では有効。"""
    import re

    # admin ページから CSRF トークンを取得
    with patch("kotolog.db.crud.get_setting", return_value=None):
        resp_admin = client.get(f"/admin?token={TOKEN}")
    match = re.search(r'value="([^"]+)".*?name="csrf_token"', resp_admin.text)
    if not match:
        match = re.search(r'name="csrf_token".*?value="([^"]+)"', resp_admin.text)
    csrf_token = match.group(1)

    # 記録追加エンドポイントで使用 → セッション内なので有効
    with patch("kotolog.db.crud.insert_record", return_value=1):
        resp = client.post(
            f"/admin/records?token={TOKEN}",
            data={
                "type": "feeding",
                "started_at": "2026-06-26T21:30",
                "csrf_token": csrf_token,
            },
        )
    # セッション内のトークンは有効
    assert resp.status_code == 303
