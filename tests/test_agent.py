"""T1.5: エージェント・ループのテスト。

LLM をスクリプト化した FakeLLM に差し替え、tool-use ループ・確認サマリ・
聞き返し・テキスト混入フォールバック・未知ツールの扱いを決定論的に検証する。
"""

import json
from types import SimpleNamespace

import pytest

from kotolog.agent.loop import Agent
from kotolog.db import crud
from kotolog.db.connection import connect
from kotolog.tools.executor import ToolExecutor
from datetime import datetime, timezone, timedelta

JST = timezone(timedelta(hours=9))
NOW = datetime(2026, 6, 18, 10, 0, 0, tzinfo=JST)


def _resp(content=None, tool_calls=None):
    msg = SimpleNamespace(content=content, tool_calls=tool_calls)
    return SimpleNamespace(choices=[SimpleNamespace(message=msg)])


def _tc(name, args, id="call_1"):
    return SimpleNamespace(
        id=id, function=SimpleNamespace(name=name, arguments=json.dumps(args))
    )


class FakeLLM:
    """scripted な応答を順に返し、渡された messages を記録する。"""

    def __init__(self, scripted):
        self.scripted = list(scripted)
        self.seen_messages = []

    def complete(self, messages, tools=None):
        self.seen_messages.append(messages)
        return self.scripted.pop(0)


@pytest.fixture()
def env():
    conn = connect(":memory:")
    crud.init_db(conn)
    child_id = crud.ensure_child(conn, "baby")
    executor = ToolExecutor(conn=conn, child_id=child_id, now=NOW)
    yield SimpleNamespace(conn=conn, child_id=child_id, executor=executor)
    conn.close()


def test_save_flow_returns_confirmation_and_writes_db(env):
    llm = FakeLLM([
        _resp(tool_calls=[_tc("save_record",
              {"type": "feeding", "amount": 120, "unit": "ml", "started_at": "3時"})]),
        _resp(content="ミルク120mlを3時に記録しました。"),
    ])
    agent = Agent(client=llm, executor=env.executor)

    reply = agent.handle("3時にミルク120ml飲んだ")

    assert reply == "ミルク120mlを3時に記録しました。"
    rows = env.conn.execute("SELECT * FROM records").fetchall()
    assert len(rows) == 1
    assert rows[0]["amount"] == 120
    assert rows[0]["started_at"] == "2026-06-18T03:00:00+09:00"


def test_text_embedded_tool_call_is_recovered(env):
    # tool_calls が空でも本文中の JSON ツール呼び出しを拾う（7Bの実失敗モード）
    content = 'leton\n{"name": "save_record", "arguments": {"type": "diaper", "started_at": "さっき"}}'
    llm = FakeLLM([
        _resp(content=content, tool_calls=None),
        _resp(content="おむつを記録しました。"),
    ])
    agent = Agent(client=llm, executor=env.executor)

    reply = agent.handle("さっきおむつ替えた")

    assert reply == "おむつを記録しました。"
    rows = env.conn.execute("SELECT * FROM records WHERE type='diaper'").fetchall()
    assert len(rows) == 1


def test_clarification_returns_text_without_tool(env):
    llm = FakeLLM([_resp(content="ミルクの量はどれくらいですか？", tool_calls=None)])
    agent = Agent(client=llm, executor=env.executor)

    reply = agent.handle("さっきミルクあげた")

    assert reply == "ミルクの量はどれくらいですか？"
    assert env.conn.execute("SELECT COUNT(*) AS n FROM records").fetchone()["n"] == 0


def test_query_flow_feeds_result_back(env):
    env.executor.execute("save_record", {"type": "feeding", "amount": 100, "unit": "ml", "started_at": "3時"})
    env.executor.execute("save_record", {"type": "feeding", "amount": 120, "unit": "ml", "started_at": "7時"})
    llm = FakeLLM([
        _resp(tool_calls=[_tc("query_records", {"type": "feeding", "period": "today"})]),
        _resp(content="今日は2回、合計220mlです。"),
    ])
    agent = Agent(client=llm, executor=env.executor)

    reply = agent.handle("今日何回ミルク飲んだ？")

    assert reply == "今日は2回、合計220mlです。"
    # 2回目の補完に tool 結果（count=2）が渡っている
    second_call_msgs = llm.seen_messages[1]
    tool_msgs = [m for m in second_call_msgs if m.get("role") == "tool"]
    assert tool_msgs and '"count": 2' in tool_msgs[0]["content"]


def test_unknown_tool_is_handled_not_raised(env):
    llm = FakeLLM([
        _resp(tool_calls=[_tc("nope", {})]),
        _resp(content="すみません、その操作はできません。"),
    ])
    agent = Agent(client=llm, executor=env.executor)

    reply = agent.handle("何か変なこと")

    assert reply == "すみません、その操作はできません。"


def test_loop_gives_up_after_max_iters(env):
    # 毎回ツールを呼び続けるモデル → 上限で打ち切り、例外を出さない
    llm = FakeLLM([
        _resp(tool_calls=[_tc("save_record", {"type": "feeding", "started_at": "今"})])
        for _ in range(10)
    ])
    agent = Agent(client=llm, executor=env.executor, max_iters=3)

    reply = agent.handle("ループ")

    assert isinstance(reply, str) and reply  # 何らかのフォールバック文
