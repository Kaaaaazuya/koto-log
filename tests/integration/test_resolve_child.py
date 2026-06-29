"""resolve_child_id の結合テスト（P9.2 / ADR-0006）。

名前明示 → users.current_child_id → default_child_id → 単一児 の優先順で
子 ID を解決することを検証する。
"""

from __future__ import annotations

import pytest

from kotolog.db import crud


def test_resolve_by_name_hint(conn):
    """名前明示 → children.name_alias で解決する。"""
    cid = crud.create_child(conn, "たろう")
    assert crud.resolve_child_id(conn, child_name_hint="たろう") == cid


def test_resolve_name_hint_no_match_falls_to_default(conn):
    """名前が一致しない場合は default_child_id にフォールスルー。"""
    default = crud.create_child(conn, "はなこ")
    result = crud.resolve_child_id(conn, child_name_hint="存在しない子")
    assert result == default


def test_resolve_by_user_current_child(conn):
    """users.current_child_id から解決される。"""
    cid = crud.create_child(conn, "はなこ")
    conn.execute(
        "INSERT INTO users (line_user_id, current_child_id, created_at, updated_at) VALUES (?, ?, ?, ?)",
        ("U001", cid, "2026-01-01T00:00:00", "2026-01-01T00:00:00"),
    )
    conn.commit()
    assert crud.resolve_child_id(conn, line_user_id="U001") == cid


def test_resolve_user_current_takes_priority_over_default(conn):
    """users.current_child_id は default_child_id より優先される。"""
    _default = crud.create_child(conn, "はなこ")  # auto-set as default
    current_cid = crud.create_child(conn, "たろう")
    conn.execute(
        "INSERT INTO users (line_user_id, current_child_id, created_at, updated_at) VALUES (?, ?, ?, ?)",
        ("U001", current_cid, "2026-01-01T00:00:00", "2026-01-01T00:00:00"),
    )
    conn.commit()
    assert crud.resolve_child_id(conn, line_user_id="U001") == current_cid


def test_resolve_user_no_current_falls_to_default(conn):
    """users レコードがあっても current_child_id が未設定なら default にフォールスルー。"""
    default_cid = crud.create_child(conn, "はなこ")
    conn.execute(
        "INSERT INTO users (line_user_id, created_at, updated_at) VALUES (?, ?, ?)",
        ("U001", "2026-01-01T00:00:00", "2026-01-01T00:00:00"),
    )
    conn.commit()
    assert crud.resolve_child_id(conn, line_user_id="U001") == default_cid


def test_resolve_by_default_child_id(conn):
    """line_user_id なし・ヒントなしの場合は default_child_id を返す。"""
    cid = crud.create_child(conn, "はなこ")
    assert crud.resolve_child_id(conn) == cid


def test_resolve_single_child_fallback(conn):
    """default 未設定・子が 1 人のみの場合はその子を返す。"""
    cur = conn.execute("INSERT INTO children (name_alias) VALUES (?)", ("たろう",))
    conn.commit()
    cid = cur.lastrowid
    assert crud.get_default_child_id(conn) is None
    assert crud.resolve_child_id(conn) == cid


def test_resolve_no_children_raises(conn):
    """子が存在しない場合は RuntimeError。"""
    with pytest.raises(RuntimeError):
        crud.resolve_child_id(conn)
