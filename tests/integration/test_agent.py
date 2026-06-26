"""エージェント・ループのテスト（結合）。

スクリプト化した FakeLLM（conftest）に差し替え、tool-use ループ・確認サマリ・
聞き返し・テキスト混入フォールバック・未知ツールの扱いを決定論的に検証する。

extract_records（P6）は別関心事のため autouse でパッチし、ループ挙動に集中する。
"""

from unittest.mock import patch

import pytest

from kotolog.agent.loop import Agent


@pytest.fixture(autouse=True)
def _no_extraction():
    with patch("kotolog.agent.loop.extract_records", return_value=[]):
        yield


def test_save_flow_returns_confirmation_and_writes_db(executor, conn, fake_llm, resp, tc):
    llm = fake_llm(
        [
            resp(
                tool_calls=[
                    tc(
                        "save_record",
                        {"type": "feeding", "amount": 120, "unit": "ml", "started_at": "3時"},
                    )
                ]
            ),
            resp(content="ミルク120mlを3時に記録しました。"),
        ]
    )
    agent = Agent(client=llm, executor=executor)

    reply = agent.handle("3時にミルク120ml飲んだ")

    assert reply == "ミルク120mlを3時に記録しました。"
    rows = conn.execute("SELECT * FROM records").fetchall()
    assert len(rows) == 1
    assert rows[0]["amount"] == 120
    assert rows[0]["started_at"] == "2026-06-18T03:00:00+09:00"


def test_text_embedded_tool_call_is_recovered(executor, conn, fake_llm, resp):
    # tool_calls が空でも本文中の JSON ツール呼び出しを拾う（7Bの実失敗モード）
    content = 'leton\n{"name": "save_record", "arguments": {"type": "diaper", "started_at": "さっき"}}'
    llm = fake_llm(
        [
            resp(content=content, tool_calls=None),
            resp(content="おむつを記録しました。"),
        ]
    )
    agent = Agent(client=llm, executor=executor)

    reply = agent.handle("さっきおむつ替えた")

    assert reply == "おむつを記録しました。"
    rows = conn.execute("SELECT * FROM records WHERE type='diaper'").fetchall()
    assert len(rows) == 1


def test_clarification_returns_text_without_tool(executor, conn, fake_llm, resp):
    llm = fake_llm([resp(content="ミルクの量はどれくらいですか？", tool_calls=None)])
    agent = Agent(client=llm, executor=executor)

    reply = agent.handle("さっきミルクあげた")

    assert reply == "ミルクの量はどれくらいですか？"
    assert conn.execute("SELECT COUNT(*) AS n FROM records").fetchone()["n"] == 0


def test_query_flow_feeds_result_back(executor, fake_llm, resp, tc):
    executor.execute("save_record", {"type": "feeding", "amount": 100, "unit": "ml", "started_at": "3時"})
    executor.execute("save_record", {"type": "feeding", "amount": 120, "unit": "ml", "started_at": "7時"})
    llm = fake_llm(
        [
            resp(tool_calls=[tc("query_records", {"type": "feeding", "period": "today"})]),
            resp(content="今日は2回、合計220mlです。"),
        ]
    )
    agent = Agent(client=llm, executor=executor)

    reply = agent.handle("今日何回ミルク飲んだ？")

    assert reply == "今日は2回、合計220mlです。"
    # 2回目の補完に tool 結果（count=2）が渡っている
    second_call_msgs = llm.seen_messages[1]
    tool_msgs = [m for m in second_call_msgs if m.get("role") == "tool"]
    assert tool_msgs and '"count": 2' in tool_msgs[0]["content"]


def test_unknown_tool_is_handled_not_raised(executor, fake_llm, resp, tc):
    llm = fake_llm(
        [
            resp(tool_calls=[tc("nope", {})]),
            resp(content="すみません、その操作はできません。"),
        ]
    )
    agent = Agent(client=llm, executor=executor)

    reply = agent.handle("何か変なこと")

    assert reply == "すみません、その操作はできません。"


def test_loop_gives_up_after_max_iters(executor, fake_llm, resp, tc):
    # 毎回ツールを呼び続けるモデル → 上限で打ち切り、例外を出さない
    llm = fake_llm(
        [resp(tool_calls=[tc("save_record", {"type": "feeding", "started_at": "今"})]) for _ in range(10)]
    )
    agent = Agent(client=llm, executor=executor, max_iters=3)

    reply = agent.handle("ループ")

    assert isinstance(reply, str) and reply  # 何らかのフォールバック文
