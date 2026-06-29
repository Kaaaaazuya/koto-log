"""複数子・既定児・users テーブルのテスト（結合 / P9.1・ADR-0006）。

`conn` フィクスチャ（初期化済みインメモリDB）は conftest が提供する。
"""

from kotolog.db import crud


def _table_exists(conn, name: str) -> bool:
    row = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name = ?", (name,)).fetchone()
    return row is not None


def test_users_table_exists(conn):
    """マイグレーション 0002 で users テーブルが作られる。"""
    assert _table_exists(conn, "users")
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(users)").fetchall()}
    assert {"line_user_id", "nickname", "notify_enabled", "current_child_id"} <= cols


def test_create_child_returns_id_and_lists(conn):
    a = crud.create_child(conn, "たろう")
    b = crud.create_child(conn, "はなこ")
    assert a != b
    names = [c["name_alias"] for c in crud.list_children(conn)]
    assert names == ["たろう", "はなこ"]


def test_list_children_orders_by_birthday_then_id(conn):
    """birthday 昇順、NULL は末尾、同一/NULL は id 昇順タイブレーク。"""
    c_nodate = crud.create_child(conn, "未設定")  # birthday NULL → 末尾
    c_old = crud.create_child(conn, "上の子", birthday="2024-01-01")
    c_young = crud.create_child(conn, "下の子", birthday="2026-01-01")
    ordered = [c["id"] for c in crud.list_children(conn)]
    assert ordered == [c_old, c_young, c_nodate]


def test_create_child_sets_default_when_first(conn):
    """最初の子作成時に既定児が自動設定される。"""
    assert crud.get_default_child_id(conn) is None
    first = crud.create_child(conn, "たろう")
    assert crud.get_default_child_id(conn) == first
    # 2人目では既定児は変わらない
    crud.create_child(conn, "はなこ")
    assert crud.get_default_child_id(conn) == first


def test_set_default_child_id(conn):
    a = crud.create_child(conn, "たろう")
    b = crud.create_child(conn, "はなこ")
    crud.set_default_child_id(conn, b)
    assert crud.get_default_child_id(conn) == b
    assert a != b


def test_get_or_create_default_child_bootstraps_and_is_idempotent(conn):
    """既定児が無ければ seed 児を作成して既定化。再呼び出しで増えない。"""
    cid = crud.get_or_create_default_child(conn, "baby")
    assert crud.get_default_child_id(conn) == cid
    again = crud.get_or_create_default_child(conn, "baby")
    assert again == cid
    assert len(crud.list_children(conn)) == 1


def test_get_or_create_default_child_uses_existing(conn):
    """既存の子がいれば（既定未設定でも）それを既定として返し新規作成しない。"""
    existing = crud.create_child(conn, "たろう")
    crud.set_default_child_id(conn, existing)
    cid = crud.get_or_create_default_child(conn, "baby")
    assert cid == existing
    assert len(crud.list_children(conn)) == 1
