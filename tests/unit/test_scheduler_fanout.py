"""P9.3: scheduler の Push fan-out ロジックのテスト。

実際の Push API は呼ばない。notify_enabled による送信・スキップだけを検証する。
"""

from __future__ import annotations

from kotolog.db import crud

# ---------------------------------------------------------------------------
# fan-out ヘルパー関数のテスト
# ---------------------------------------------------------------------------


def test_fanout_sends_to_notify_enabled_users_only(conn):
    """notify_enabled=True のユーザーにのみ送信する。"""
    from kotolog.line.scheduler import _fanout_push

    crud.upsert_user(conn, "U001")  # notify_enabled=True
    crud.upsert_user(conn, "U002")
    crud.update_user_notify(conn, "U002", notify_enabled=False)

    sent: list[str] = []

    def mock_send(user_id: str, text: str, token: str) -> None:
        sent.append(user_id)

    _fanout_push(conn, "テスト送信", "fake_token", send_fn=mock_send)

    assert "U001" in sent
    assert "U002" not in sent


def test_fanout_sends_to_all_enabled_users(conn):
    """notify_enabled=True の全ユーザーに送信する。"""
    from kotolog.line.scheduler import _fanout_push

    crud.upsert_user(conn, "U001")
    crud.upsert_user(conn, "U002")

    sent: list[str] = []
    _fanout_push(conn, "おはよう", "token", send_fn=lambda uid, t, tok: sent.append(uid))

    assert sorted(sent) == ["U001", "U002"]


def test_fanout_no_users_sends_nothing(conn):
    """ユーザーが 0 人でも例外にならない。"""
    from kotolog.line.scheduler import _fanout_push

    sent: list[str] = []
    _fanout_push(conn, "テスト", "token", send_fn=lambda uid, t, tok: sent.append(uid))
    assert sent == []
