"""会話文脈の永続化と再利用のテスト（Issue #38）。

LLM が聞き返した後、次のメッセージがその文脈込みで処理されることを検証する。
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from kotolog.agent.loop import Agent
from kotolog.db import crud

pytestmark = pytest.mark.usefixtures("child_id")


@pytest.fixture(autouse=True)
def _no_extraction():
    """extract_records は常に空リストを返す（tool-use ループに入らせる）。"""
    with patch("kotolog.agent.loop.extract_records", return_value=([], None)):
        yield


# --- crud: get_session_context / set_session_context ------------------------


def test_get_session_context_returns_none_when_absent(conn):
    assert crud.get_session_context(conn, "U001") is None


def test_set_and_get_session_context_round_trips(conn):
    context = [{"role": "user", "content": "薬飲んだ"}, {"role": "assistant", "content": "どのお薬？"}]
    crud.set_session_context(conn, "U001", context)
    assert crud.get_session_context(conn, "U001") == context


def test_set_session_context_overwrites_existing(conn):
    crud.set_session_context(conn, "U001", [{"role": "user", "content": "1回目"}])
    crud.set_session_context(conn, "U001", [{"role": "user", "content": "2回目"}])
    assert crud.get_session_context(conn, "U001") == [{"role": "user", "content": "2回目"}]


# --- Agent.handle: 文脈の自動読み込み・保存 -----------------------------------


def test_clarifying_question_is_saved_as_context(conn, now, fake_llm, resp):
    """LLMが聞き返した（tool_calls なし）とき、その往復が session context に保存される。"""
    llm = fake_llm([resp(content="どのお薬ですか？", tool_calls=None)])
    agent = Agent(client=llm, conn=conn, _now=lambda: now)

    agent.handle("薬飲んだ", line_user_id="U001")

    saved = crud.get_session_context(conn, "U001")
    assert saved == [
        {"role": "user", "content": "薬飲んだ"},
        {"role": "assistant", "content": "どのお薬ですか？"},
    ]


def test_next_call_loads_prior_context_into_messages(conn, now, fake_llm, resp):
    """次の呼び出しで、直前の聞き返しが LLM への messages に含まれる。"""
    llm1 = fake_llm([resp(content="どのお薬ですか？", tool_calls=None)])
    agent = Agent(client=llm1, conn=conn, _now=lambda: now)
    agent.handle("薬飲んだ", line_user_id="U001")

    llm2 = fake_llm([resp(content="ビオフェルミンを記録した", tool_calls=None)])
    agent2 = Agent(client=llm2, conn=conn, _now=lambda: now)
    agent2.handle("ビオフェルミン", line_user_id="U001")

    # extract_records は fixture でモック済み（complete を呼ばない）ため、
    # seen_messages[0] は tool-use ループへ送られた最初の messages になる。
    sent_messages = llm2.seen_messages[0]
    contents = [m["content"] for m in sent_messages]
    assert "薬飲んだ" in contents
    assert "どのお薬ですか？" in contents
    assert "ビオフェルミン" in contents


def test_explicit_history_overrides_db_context(conn, now, fake_llm, resp):
    """history を明示指定した場合は DB の文脈を読み込まず、渡された history を使う。"""
    crud.set_session_context(conn, "U001", [{"role": "user", "content": "DBに保存された古い文脈"}])

    llm = fake_llm([resp(content="ok", tool_calls=None)])
    agent = Agent(client=llm, conn=conn, _now=lambda: now)
    explicit_history = [{"role": "user", "content": "明示された履歴"}]
    agent.handle("テスト", line_user_id="U001", history=explicit_history)

    sent_messages = llm.seen_messages[0]
    contents = [m["content"] for m in sent_messages]
    assert "明示された履歴" in contents
    assert "DBに保存された古い文脈" not in contents


def test_context_is_trimmed_to_max_turns(conn, now, fake_llm, resp):
    """MAX_CONTEXT_TURNS を超える往復は古いものから切り詰められる。"""
    from kotolog.agent.loop import MAX_CONTEXT_TURNS

    for i in range(MAX_CONTEXT_TURNS + 2):
        llm = fake_llm([resp(content=f"reply{i}", tool_calls=None)])
        agent = Agent(client=llm, conn=conn, _now=lambda: now)
        agent.handle(f"turn{i}", line_user_id="U001")

    saved = crud.get_session_context(conn, "U001")
    assert len(saved) == MAX_CONTEXT_TURNS * 2
    # 最新の往復は必ず含まれ、最古の往復は落ちている
    contents = [m["content"] for m in saved]
    assert f"turn{MAX_CONTEXT_TURNS + 1}" in contents
    assert "turn0" not in contents


def test_no_line_user_id_does_not_persist_context(conn, now, fake_llm, resp):
    """line_user_id が無い場合は会話文脈を保存しない（保存キーが無いため）。"""
    llm = fake_llm([resp(content="ok", tool_calls=None)])
    agent = Agent(client=llm, conn=conn, _now=lambda: now)
    agent.handle("テスト")

    rows = conn.execute("SELECT * FROM sessions").fetchall()
    assert len(rows) == 0
