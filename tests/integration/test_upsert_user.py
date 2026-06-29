"""P9.3: upsert_user・list_users・ユーザー管理 CRUD のテスト。"""

from __future__ import annotations

from kotolog.db import crud

# ---------------------------------------------------------------------------
# upsert_user
# ---------------------------------------------------------------------------


def test_upsert_user_creates_new(conn):
    """新規ユーザーを作成する。"""
    crud.upsert_user(conn, "U001")
    rows = conn.execute("SELECT * FROM users WHERE line_user_id = ?", ("U001",)).fetchall()
    assert len(rows) == 1
    assert rows[0]["notify_enabled"] == 1
    assert rows[0]["nickname"] is None


def test_upsert_user_idempotent(conn):
    """同じ line_user_id を複数回呼んでも重複しない。"""
    crud.upsert_user(conn, "U001")
    crud.upsert_user(conn, "U001")
    rows = conn.execute("SELECT COUNT(*) AS n FROM users WHERE line_user_id = ?", ("U001",)).fetchone()
    assert rows["n"] == 1


def test_upsert_user_preserves_nickname(conn):
    """既存ニックネームは nickname=None で呼んでも変わらない。"""
    crud.upsert_user(conn, "U001", nickname="パパ")
    crud.upsert_user(conn, "U001")  # nickname 指定なし
    row = conn.execute("SELECT nickname FROM users WHERE line_user_id = ?", ("U001",)).fetchone()
    assert row["nickname"] == "パパ"


def test_upsert_user_updates_nickname(conn):
    """nickname を指定して呼ぶと更新される。"""
    crud.upsert_user(conn, "U001", nickname="パパ")
    crud.upsert_user(conn, "U001", nickname="ママ")
    row = conn.execute("SELECT nickname FROM users WHERE line_user_id = ?", ("U001",)).fetchone()
    assert row["nickname"] == "ママ"


def test_upsert_user_sets_timestamps(conn):
    """created_at・updated_at が設定される。"""
    crud.upsert_user(conn, "U001")
    row = conn.execute(
        "SELECT created_at, updated_at FROM users WHERE line_user_id = ?", ("U001",)
    ).fetchone()
    assert row["created_at"]
    assert row["updated_at"]


# ---------------------------------------------------------------------------
# list_users
# ---------------------------------------------------------------------------


def test_list_users_returns_all(conn):
    """全ユーザーを返す。"""
    crud.upsert_user(conn, "U001")
    crud.upsert_user(conn, "U002", nickname="ママ")
    users = crud.list_users(conn)
    assert len(users) == 2
    ids = [u["line_user_id"] for u in users]
    assert "U001" in ids
    assert "U002" in ids


def test_list_users_empty(conn):
    """ユーザーがいない場合は空リスト。"""
    assert crud.list_users(conn) == []


# ---------------------------------------------------------------------------
# update_user_notify
# ---------------------------------------------------------------------------


def test_update_user_notify_off(conn):
    """notify_enabled を False に設定する。"""
    crud.upsert_user(conn, "U001")
    crud.update_user_notify(conn, "U001", notify_enabled=False)
    row = conn.execute("SELECT notify_enabled FROM users WHERE line_user_id = ?", ("U001",)).fetchone()
    assert row["notify_enabled"] == 0


def test_update_user_notify_on(conn):
    """notify_enabled を True に戻す。"""
    crud.upsert_user(conn, "U001")
    crud.update_user_notify(conn, "U001", notify_enabled=False)
    crud.update_user_notify(conn, "U001", notify_enabled=True)
    row = conn.execute("SELECT notify_enabled FROM users WHERE line_user_id = ?", ("U001",)).fetchone()
    assert row["notify_enabled"] == 1


# ---------------------------------------------------------------------------
# delete_user
# ---------------------------------------------------------------------------


def test_delete_user(conn):
    """ユーザーを削除する。"""
    crud.upsert_user(conn, "U001")
    crud.delete_user(conn, "U001")
    rows = conn.execute("SELECT COUNT(*) AS n FROM users WHERE line_user_id = ?", ("U001",)).fetchone()
    assert rows["n"] == 0


# ---------------------------------------------------------------------------
# set_user_current_child（切り替え）
# ---------------------------------------------------------------------------


def test_set_user_current_child(conn):
    """current_child_id を更新する。"""
    cid = crud.create_child(conn, "たろう")
    crud.upsert_user(conn, "U001")
    crud.set_user_current_child(conn, "U001", cid)
    row = conn.execute("SELECT current_child_id FROM users WHERE line_user_id = ?", ("U001",)).fetchone()
    assert row["current_child_id"] == cid


# ---------------------------------------------------------------------------
# get_notify_users（fan-out 用）
# ---------------------------------------------------------------------------


def test_get_notify_users_returns_enabled_only(conn):
    """notify_enabled=True のユーザーのみ返す。"""
    crud.upsert_user(conn, "U001")  # notify_enabled=1
    crud.upsert_user(conn, "U002")
    crud.update_user_notify(conn, "U002", notify_enabled=False)
    users = crud.get_notify_users(conn)
    ids = [u["line_user_id"] for u in users]
    assert "U001" in ids
    assert "U002" not in ids


def test_get_notify_users_empty_when_none(conn):
    """全員 OFF なら空リスト。"""
    crud.upsert_user(conn, "U001")
    crud.update_user_notify(conn, "U001", notify_enabled=False)
    assert crud.get_notify_users(conn) == []
