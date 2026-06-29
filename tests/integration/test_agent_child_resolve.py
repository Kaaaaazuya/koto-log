"""Agent.handle の per-request executor・子解決の結合テスト（P9.2）。

Agent は conn を受け取り、handle() は line_user_id を受け取って
呼び出しごとに child_id を解決して executor を生成することを検証する。
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from kotolog.agent.loop import Agent
from kotolog.db import crud


@pytest.fixture(autouse=True)
def _no_extraction():
    with patch("kotolog.agent.loop.extract_records", return_value=([], None)):
        yield


def test_agent_accepts_conn_not_executor(conn, now, fake_llm, resp):
    """Agent は executor ではなく conn を受け取る（P9.2）。"""
    crud.create_child(conn, "baby")
    llm = fake_llm([resp(content="ok", tool_calls=None)])
    agent = Agent(client=llm, conn=conn, _now=lambda: now)
    reply = agent.handle("テスト")
    assert isinstance(reply, str)


def test_agent_conn_attribute_accessible(conn, fake_llm, resp):
    """agent.conn で接続を取得できる（webhook が必要とする）。"""
    crud.create_child(conn, "baby")
    llm = fake_llm([])
    agent = Agent(client=llm, conn=conn)
    assert agent.conn is conn


def test_handle_accepts_line_user_id(conn, now, fake_llm, resp):
    """handle() は line_user_id を受け取れる（P9.2）。"""
    crud.create_child(conn, "baby")
    llm = fake_llm([resp(content="ok", tool_calls=None)])
    agent = Agent(client=llm, conn=conn, _now=lambda: now)
    reply = agent.handle("テスト", line_user_id="U001")
    assert isinstance(reply, str)


def test_handle_resolves_child_from_user(conn, now, fake_llm, resp, tc):
    """users.current_child_id から対象児を解決して記録する。"""
    baby = crud.create_child(conn, "baby")
    taro = crud.create_child(conn, "たろう")
    conn.execute(
        "INSERT INTO users (line_user_id, current_child_id, created_at, updated_at) VALUES (?, ?, ?, ?)",
        ("U001", taro, "2026-01-01T00:00:00", "2026-01-01T00:00:00"),
    )
    conn.commit()

    llm = fake_llm(
        [
            resp(
                tool_calls=[
                    tc("save_record", {"type": "feeding", "amount": 100, "unit": "ml", "started_at": "9時"})
                ]
            ),
            resp(content="記録した"),
        ]
    )
    agent = Agent(client=llm, conn=conn, _now=lambda: now)
    agent.handle("9時にミルク100ml", line_user_id="U001")

    rows = conn.execute("SELECT * FROM records WHERE child_id = ?", (taro,)).fetchall()
    assert len(rows) == 1
    rows_baby = conn.execute("SELECT * FROM records WHERE child_id = ?", (baby,)).fetchall()
    assert len(rows_baby) == 0


def test_confirmation_includes_child_name_when_multiple_children(conn, now, fake_llm, resp):
    """複数児がいる場合、確認文に対象児名が含まれる。"""
    crud.create_child(conn, "たろう")
    crud.create_child(conn, "はなこ")

    record = {"type": "feeding", "sub_type": "ミルク", "amount": 120, "unit": "ml", "started_at": "9時"}
    with patch("kotolog.agent.loop.extract_records", return_value=([record], None)):
        llm = fake_llm([])
        agent = Agent(client=llm, conn=conn, _now=lambda: now)
        reply = agent.handle("9時にミルク120ml")

    assert "たろう" in reply
