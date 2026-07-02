"""Issue #29: Webhook integration tests for user approval flow.

Tests that unapproved users are blocked from using bot functionality.
"""

from __future__ import annotations

from kotolog.db import crud


def test_unapproved_user_cannot_access_session_functions(conn):
    """未承認ユーザーは セッション機能（resolve_child_id など）にアクセスできない。"""
    # Simulate external user registration
    crud.upsert_user(conn, "U_EXTERNAL_NEW")

    # Try to use the bot: should fail because user is unapproved
    is_approved = crud.is_user_approved(conn, "U_EXTERNAL_NEW")
    assert is_approved is False


def test_approved_user_can_access_session_functions(conn):
    """承認済みユーザーはセッション機能にアクセスできる。"""
    # Create and approve user
    crud.upsert_user(conn, "U_APPROVED_USER")
    crud.approve_user(conn, "U_APPROVED_USER")

    # Can access session
    is_approved = crud.is_user_approved(conn, "U_APPROVED_USER")
    assert is_approved is True

    # get_user should return the user
    user = crud.get_user(conn, "U_APPROVED_USER")
    assert user is not None
    assert user["line_user_id"] == "U_APPROVED_USER"


def test_webhook_creates_unapproved_user_on_first_message(conn):
    """最初のメッセージで外部ユーザーが未承認で登録される。"""
    # Simulate webhook calling upsert_user
    crud.upsert_user(conn, "U_EXT_WEBHOOK_001")

    # User is created but unapproved
    user = crud.get_user(conn, "U_EXT_WEBHOOK_001")
    assert user is None  # get_user returns None for unapproved

    # But the user exists in the DB
    row = conn.execute(
        "SELECT * FROM users WHERE line_user_id = ?", ("U_EXT_WEBHOOK_001",)
    ).fetchone()
    assert row is not None
    assert row["approved"] == 0


def test_multiple_messages_from_unapproved_user_dont_change_status(conn):
    """未承認ユーザーからの複数メッセージは承認状態を変えない。"""
    # First message
    crud.upsert_user(conn, "U_EXT_PERSISTENT")

    # Second message (same user)
    crud.upsert_user(conn, "U_EXT_PERSISTENT", nickname="テスト")

    # Still unapproved
    is_approved = crud.is_user_approved(conn, "U_EXT_PERSISTENT")
    assert is_approved is False

    # Verify only 1 user row exists
    rows = conn.execute(
        "SELECT COUNT(*) AS n FROM users WHERE line_user_id = ?",
        ("U_EXT_PERSISTENT",),
    ).fetchone()
    assert rows["n"] == 1


def test_approval_flow_for_external_user(conn):
    """外部ユーザーの完全な承認フロー。"""
    external_user = "U_EXT_FULL_FLOW"

    # 1. User sends first message (webhook)
    crud.upsert_user(conn, external_user)
    assert crud.is_user_approved(conn, external_user) is False

    # 2. Admin views pending approvals
    pending = crud.list_pending_approvals(conn)
    ids = [u["line_user_id"] for u in pending]
    assert external_user in ids

    # 3. Admin approves the user
    crud.approve_user(conn, external_user)
    assert crud.is_user_approved(conn, external_user) is True

    # 4. User is no longer in pending list
    pending = crud.list_pending_approvals(conn)
    ids = [u["line_user_id"] for u in pending]
    assert external_user not in ids

    # 5. User can now access functions (get_user returns user)
    user = crud.get_user(conn, external_user)
    assert user is not None


def test_rejection_flow_for_external_user(conn):
    """外部ユーザーの却下フロー。"""
    external_user = "U_EXT_REJECT_FLOW"

    # 1. User sends message
    crud.upsert_user(conn, external_user)
    assert crud.is_user_approved(conn, external_user) is False

    # 2. Admin rejects the user
    crud.reject_user(conn, external_user)

    # 3. User is deleted from DB
    row = conn.execute(
        "SELECT * FROM users WHERE line_user_id = ?", (external_user,)
    ).fetchone()
    assert row is None

    # 4. If user sends another message, they'll be re-registered as unapproved
    crud.upsert_user(conn, external_user)
    assert crud.is_user_approved(conn, external_user) is False
