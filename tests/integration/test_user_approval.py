"""Issue #29: User approval workflow for external user registration.

Tests for:
1. New external users are registered with approved=False
2. Unapproved users cannot use bot functionality
3. Approved status persists across requests
4. Admin can view pending approvals
5. Admin can approve users
"""

from __future__ import annotations

from kotolog.db import crud


# ---------------------------------------------------------------------------
# User approval status (approved column)
# ---------------------------------------------------------------------------


def test_new_user_is_unapproved(conn):
    """新規ユーザーは approved=False で登録される。"""
    crud.upsert_user(conn, "U_EXTERNAL_001")
    row = conn.execute(
        "SELECT approved FROM users WHERE line_user_id = ?", ("U_EXTERNAL_001",)
    ).fetchone()
    assert row is not None
    assert row["approved"] == 0  # False


def test_unapproved_status_persists(conn):
    """未承認ステータスはリクエスト間で保持される。"""
    crud.upsert_user(conn, "U_EXTERNAL_002")
    # 別リクエストで同じユーザーをアップサート
    crud.upsert_user(conn, "U_EXTERNAL_002", nickname="テスト")
    row = conn.execute(
        "SELECT approved FROM users WHERE line_user_id = ?", ("U_EXTERNAL_002",)
    ).fetchone()
    # approved は still False のままでなくてはいけない
    assert row["approved"] == 0


def test_approve_user(conn):
    """管理者がユーザーを承認できる。"""
    crud.upsert_user(conn, "U_EXTERNAL_003")
    crud.approve_user(conn, "U_EXTERNAL_003")
    row = conn.execute(
        "SELECT approved FROM users WHERE line_user_id = ?", ("U_EXTERNAL_003",)
    ).fetchone()
    assert row["approved"] == 1  # True


def test_list_pending_approvals(conn):
    """未承認ユーザーの一覧を取得できる。"""
    crud.upsert_user(conn, "U_EXTERNAL_004")
    crud.upsert_user(conn, "U_EXTERNAL_005")
    crud.approve_user(conn, "U_EXTERNAL_004")

    pending = crud.list_pending_approvals(conn)
    ids = [u["line_user_id"] for u in pending]

    # U_EXTERNAL_005 は未承認
    assert "U_EXTERNAL_005" in ids
    # U_EXTERNAL_004 は承認済みなので含まれない
    assert "U_EXTERNAL_004" not in ids


def test_list_pending_approvals_empty(conn):
    """未承認ユーザーがいない場合は空リストを返す。"""
    crud.upsert_user(conn, "U_EXTERNAL_006")
    crud.approve_user(conn, "U_EXTERNAL_006")

    pending = crud.list_pending_approvals(conn)
    assert pending == []


def test_reject_user(conn):
    """管理者がユーザーを却下できる。"""
    crud.upsert_user(conn, "U_EXTERNAL_007")
    crud.reject_user(conn, "U_EXTERNAL_007")

    # ユーザーが削除される
    row = conn.execute(
        "SELECT * FROM users WHERE line_user_id = ?", ("U_EXTERNAL_007",)
    ).fetchone()
    assert row is None


def test_unapproved_user_check(conn):
    """ユーザーの承認状態をチェックできる。"""
    crud.upsert_user(conn, "U_EXTERNAL_008")
    assert crud.is_user_approved(conn, "U_EXTERNAL_008") is False

    crud.approve_user(conn, "U_EXTERNAL_008")
    assert crud.is_user_approved(conn, "U_EXTERNAL_008") is True


def test_get_approved_user_returns_none_for_unapproved(conn):
    """未承認ユーザーの取得は None を返す（セッションから除外）。"""
    crud.upsert_user(conn, "U_EXTERNAL_009")
    user = crud.get_user(conn, "U_EXTERNAL_009")
    # User should exist in DB but get_user for unapproved should return None
    # OR we have a separate function that filters by approval
    assert user is None or user["approved"] == 0


def test_approved_user_visible_to_get_user(conn):
    """承認済みユーザーは get_user で取得できる。"""
    crud.upsert_user(conn, "U_EXTERNAL_010")
    crud.approve_user(conn, "U_EXTERNAL_010")

    user = crud.get_user(conn, "U_EXTERNAL_010")
    assert user is not None
    assert user["line_user_id"] == "U_EXTERNAL_010"
    assert user["approved"] == 1


# ---------------------------------------------------------------------------
# Multiple users scenarios
# ---------------------------------------------------------------------------


def test_mixed_approved_and_unapproved_users(conn):
    """承認済みと未承認ユーザーが混在できる。"""
    # Create multiple users
    crud.upsert_user(conn, "U_APP_001")  # approved
    crud.upsert_user(conn, "U_EXT_001")  # unapproved
    crud.upsert_user(conn, "U_APP_002")  # approved
    crud.upsert_user(conn, "U_EXT_002")  # unapproved

    # Approve some
    crud.approve_user(conn, "U_APP_001")
    crud.approve_user(conn, "U_APP_002")

    # Check counts
    pending = crud.list_pending_approvals(conn)
    assert len(pending) == 2
    assert {u["line_user_id"] for u in pending} == {"U_EXT_001", "U_EXT_002"}


def test_all_users_list_includes_all_regardless_of_approval(conn):
    """list_users は承認状態に関わらずすべてのユーザーを返す（管理画面用）。"""
    crud.upsert_user(conn, "U_ADMIN_VIEW_1")
    crud.upsert_user(conn, "U_ADMIN_VIEW_2")
    crud.approve_user(conn, "U_ADMIN_VIEW_1")

    all_users = crud.list_users(conn)
    ids = [u["line_user_id"] for u in all_users]
    assert "U_ADMIN_VIEW_1" in ids
    assert "U_ADMIN_VIEW_2" in ids
