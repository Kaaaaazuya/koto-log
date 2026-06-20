"""agent/loop.py の単体テスト。

FakeLLMClient でレスポンスをスクリプト化し、ループロジックを検証する。
実際の LLM・DB・ネットワークは一切使わない。
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from kotolog.agent.loop import Agent

# ---------------------------------------------------------------------------
# テスト用フェイク
# ---------------------------------------------------------------------------


def _tool_resp(name: str, args: dict, call_id: str = "c1") -> MagicMock:
    """tool_calls を持つ LLM レスポンスを生成する。"""
    tc = MagicMock()
    tc.id = call_id
    tc.function.name = name
    tc.function.arguments = json.dumps(args, ensure_ascii=False)

    msg = MagicMock()
    msg.tool_calls = [tc]
    msg.content = None

    resp = MagicMock()
    resp.choices = [SimpleNamespace(message=msg)]
    return resp


def _text_resp(content: str) -> MagicMock:
    """テキストのみの LLM レスポンスを生成する（ツール呼び出しなし）。"""
    msg = MagicMock()
    msg.tool_calls = None
    msg.content = content

    resp = MagicMock()
    resp.choices = [SimpleNamespace(message=msg)]
    return resp


class FakeLLMClient:
    """scripted responses を順番に返す偽 LLM クライアント。"""

    def __init__(self, responses: list) -> None:
        self._responses = list(responses)
        self.calls: list[list[dict]] = []

    def complete(self, messages, tools=None, tool_choice=None):
        self.calls.append(messages)
        return self._responses.pop(0)


@pytest.fixture(autouse=True)
def no_extraction():
    """抽出フェーズをスキップしてツール使用ループのテストに集中する。"""
    with patch("kotolog.agent.loop.extract_records", return_value=[]):
        yield


class FakeExecutor:
    """ツール呼び出しを記録し、固定の結果を返す偽エグゼキューター。"""

    def __init__(self, result: dict | None = None) -> None:
        self._result = result or {"ok": True}
        self.executed: list[tuple[str, dict]] = []

    def execute(self, name: str, args: dict) -> dict:
        self.executed.append((name, args))
        return self._result


# ---------------------------------------------------------------------------
# テスト
# ---------------------------------------------------------------------------


def test_direct_text_no_tool_call():
    """ツール呼び出しなしで即テキスト返答するケース。"""
    llm = FakeLLMClient([_text_resp("おむつを記録した")])
    agent = Agent(client=llm, executor=FakeExecutor())

    result = agent.handle("うんち")

    assert result == "おむつを記録した"
    assert len(llm.calls) == 1  # LLM は1回だけ呼ばれる


def test_single_tool_call_then_response():
    """ツールを1回呼び出し、その後テキストで返答するケース。"""
    executor = FakeExecutor(result={"ok": True, "id": 42})
    llm = FakeLLMClient(
        [
            _tool_resp("save_record", {"type": "diaper", "started_at": "now"}),
            _text_resp("おむつ（うんち）を記録した"),
        ]
    )
    agent = Agent(client=llm, executor=executor)

    result = agent.handle("うんち")

    assert result == "おむつ（うんち）を記録した"
    assert len(executor.executed) == 1
    assert executor.executed[0] == ("save_record", {"type": "diaper", "started_at": "now"})
    assert len(llm.calls) == 2  # ツール結果を受け取ってもう一度 LLM を呼ぶ


def test_tool_result_is_passed_back_to_llm():
    """ツール実行結果が次の LLM 呼び出しのメッセージに含まれることを確認。"""
    executor = FakeExecutor(result={"ok": True, "record_id": 7})
    llm = FakeLLMClient(
        [
            _tool_resp("save_record", {"type": "feeding", "started_at": "now"}, call_id="call_abc"),
            _text_resp("母乳を記録した"),
        ]
    )
    agent = Agent(client=llm, executor=executor)
    agent.handle("母乳")

    # 2回目の LLM 呼び出しに tool ロールのメッセージが含まれること
    second_call_messages = llm.calls[1]
    tool_msgs = [m for m in second_call_messages if m.get("role") == "tool"]
    assert len(tool_msgs) == 1
    assert tool_msgs[0]["tool_call_id"] == "call_abc"
    assert "record_id" in tool_msgs[0]["content"]


def test_max_iters_fallback():
    """max_iters を超えたらフォールバックメッセージを返す。"""
    llm = FakeLLMClient(
        [
            _tool_resp("save_record", {"type": "diaper", "started_at": "now"}),
            _tool_resp("save_record", {"type": "diaper", "started_at": "now"}),
            _tool_resp("save_record", {"type": "diaper", "started_at": "now"}),
        ]
    )
    agent = Agent(client=llm, executor=FakeExecutor(), max_iters=3)

    result = agent.handle("うんち")

    assert "すみません" in result
    assert len(llm.calls) == 3  # max_iters 分だけ呼ばれる


def test_tool_error_is_swallowed_and_loop_continues():
    """ツール実行が例外を投げてもループが落ちず、エラーを LLM に返す。"""

    class BrokenExecutor:
        def execute(self, name, args):
            raise ValueError("DB 接続エラー")

    llm = FakeLLMClient(
        [
            _tool_resp("save_record", {"type": "feeding", "started_at": "now"}),
            _text_resp("エラーが発生した"),
        ]
    )
    agent = Agent(client=llm, executor=BrokenExecutor())

    result = agent.handle("母乳")

    # 例外で落ちず、エラーを受け取った LLM の最終返答が得られること
    assert result == "エラーが発生した"


def test_fallback_json_parse_in_content():
    """tool_calls がなく本文に JSON が混入した場合のフォールバック解析。"""
    fallback_content = '{"name": "save_record", "arguments": {"type": "diaper", "started_at": "now"}}'

    msg = MagicMock()
    msg.tool_calls = None
    msg.content = fallback_content

    fallback_resp = MagicMock()
    fallback_resp.choices = [SimpleNamespace(message=msg)]

    executor = FakeExecutor()
    llm = FakeLLMClient([fallback_resp, _text_resp("記録した")])
    agent = Agent(client=llm, executor=executor)

    result = agent.handle("うんち")

    assert result == "記録した"
    assert executor.executed[0][0] == "save_record"


def test_empty_content_returns_empty_string():
    """LLM が空レスポンスを返した場合、空文字を返す。"""
    llm = FakeLLMClient([_text_resp("")])
    agent = Agent(client=llm, executor=FakeExecutor())

    result = agent.handle("テスト")

    assert result == ""
