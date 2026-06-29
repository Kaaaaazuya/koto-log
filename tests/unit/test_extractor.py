"""agent/extractor.py の単体テスト。

FakeLLMClient でレスポンスをスクリプト化し、抽出ロジックと確認文生成を検証する。
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock

from kotolog.agent.extractor import extract_records, format_confirmation

# ---------------------------------------------------------------------------
# フェイク
# ---------------------------------------------------------------------------


def _extract_resp(records: list[dict]) -> MagicMock:
    """extract_records ツール呼び出しレスポンスを生成する。"""
    tc = MagicMock()
    tc.function.arguments = json.dumps({"records": records}, ensure_ascii=False)

    msg = MagicMock()
    msg.tool_calls = [tc]
    msg.content = None

    resp = MagicMock()
    resp.choices = [SimpleNamespace(message=msg)]
    return resp


def _no_tool_resp() -> MagicMock:
    """tool_calls なし（テキストのみ）レスポンスを生成する。"""
    msg = MagicMock()
    msg.tool_calls = None
    msg.content = "わかりません"

    resp = MagicMock()
    resp.choices = [SimpleNamespace(message=msg)]
    return resp


class FakeLLMClient:
    def __init__(self, response) -> None:
        self._response = response
        self.last_tools: list | None = None
        self.last_tool_choice: dict | None = None
        self.last_operation: str | None = None

    def complete(self, messages, tools=None, tool_choice=None, *, operation="loop"):
        self.last_tools = tools
        self.last_tool_choice = tool_choice
        self.last_operation = operation
        return self._response


# ---------------------------------------------------------------------------
# extract_records
# ---------------------------------------------------------------------------


def test_extract_returns_empty_for_query():
    """質問文には空リストが返る。"""
    llm = FakeLLMClient(_extract_resp([]))
    records, _ = extract_records("今日は何回飲んだ？", llm)
    assert records == []


def test_extract_returns_single_record():
    """1 件の授乳記録が正しく抽出される。"""
    items = [{"type": "feeding", "sub_type": "ミルク", "amount": 120, "unit": "ml", "started_at": "9時"}]
    llm = FakeLLMClient(_extract_resp(items))
    records, _ = extract_records("9時にミルク120ml", llm)
    assert len(records) == 1
    assert records[0]["type"] == "feeding"
    assert records[0]["amount"] == 120


def test_extract_returns_multiple_records():
    """複数記録が全件抽出される。"""
    items = [
        {"type": "feeding", "sub_type": "母乳", "started_at": "9時"},
        {"type": "diaper", "sub_type": "おしっこ", "started_at": "9時"},
        {"type": "sleep", "started_at": "10時", "ended_at": "11時"},
    ]
    llm = FakeLLMClient(_extract_resp(items))
    records, _ = extract_records("9時に母乳とおしっこ交換、10時から11時まで睡眠", llm)
    assert len(records) == 3
    types = [r["type"] for r in records]
    assert "feeding" in types
    assert "diaper" in types
    assert "sleep" in types


def test_extract_forces_tool_choice():
    """tool_choice が OpenAI 仕様で extract_records を強制することを確認。

    LiteLLM は OpenAI 仕様 {"type": "function", "function": {"name": ...}} を
    要求する。Anthropic ネイティブ形式だと本番で検証エラーになるため固定する。
    """
    llm = FakeLLMClient(_extract_resp([]))
    extract_records("テスト", llm)
    assert llm.last_tool_choice == {
        "type": "function",
        "function": {"name": "extract_records"},
    }


def test_extract_passes_extract_tool_only():
    """EXTRACT_TOOL のみが tools として渡される（通常ツール一覧ではない）。"""
    llm = FakeLLMClient(_extract_resp([]))
    extract_records("テスト", llm)
    assert llm.last_tools is not None
    assert len(llm.last_tools) == 1
    assert llm.last_tools[0]["function"]["name"] == "extract_records"


def test_extract_tags_operation_extract():
    """抽出呼び出しは operation="extract" で計測される（ADR-0002）。"""
    llm = FakeLLMClient(_extract_resp([]))
    extract_records("テスト", llm)
    assert llm.last_operation == "extract"


def test_extract_returns_empty_when_no_tool_calls():
    """tool_calls なし（テキスト返答）の場合は空リストを返す。"""
    llm = FakeLLMClient(_no_tool_resp())
    records, child_name = extract_records("テスト", llm)
    assert records == []
    assert child_name is None


# ---------------------------------------------------------------------------
# format_confirmation
# ---------------------------------------------------------------------------


def test_format_single_feeding_with_amount():
    saved = [
        {
            "type": "feeding",
            "sub_type": "ミルク",
            "amount": 120.0,
            "unit": "ml",
            "started_at": "2024-01-01T09:00:00+09:00",
        }
    ]
    text = format_confirmation(saved)
    assert "授乳" in text
    assert "ミルク" in text
    assert "120" in text
    assert "09:00" in text
    assert "記録した" in text


def test_format_multiple_records_one_per_line():
    saved = [
        {
            "type": "feeding",
            "sub_type": "母乳",
            "amount": None,
            "unit": None,
            "started_at": "2024-01-01T09:00:00+09:00",
        },
        {
            "type": "diaper",
            "sub_type": "おしっこ",
            "amount": None,
            "unit": None,
            "started_at": "2024-01-01T09:05:00+09:00",
        },
    ]
    text = format_confirmation(saved)
    lines = text.strip().split("\n")
    assert len(lines) == 2
    assert "授乳" in lines[0]
    assert "おむつ" in lines[1]


def test_format_sleep_no_amount():
    saved = [
        {
            "type": "sleep",
            "sub_type": None,
            "amount": None,
            "unit": None,
            "started_at": "2024-01-01T22:00:00+09:00",
        }
    ]
    text = format_confirmation(saved)
    assert "睡眠" in text
    assert "22:00" in text
    assert "記録した" in text


# ---------------------------------------------------------------------------
# P9.2: extract_records タプル戻り値 / child フィールド
# ---------------------------------------------------------------------------


def _extract_resp_with_child(records: list[dict], child_name: str) -> MagicMock:
    """child フィールドを含む extract_records レスポンスを生成する。"""
    tc = MagicMock()
    tc.function.arguments = json.dumps({"records": records, "child": child_name}, ensure_ascii=False)
    msg = MagicMock()
    msg.tool_calls = [tc]
    msg.content = None
    resp = MagicMock()
    resp.choices = [SimpleNamespace(message=msg)]
    return resp


def test_extract_returns_tuple():
    """extract_records は (records, child_name) のタプルを返す（P9.2）。"""
    llm = FakeLLMClient(_extract_resp([]))
    result = extract_records("テスト", llm)
    assert isinstance(result, tuple) and len(result) == 2


def test_extract_child_name_in_response():
    """child フィールドが存在する場合 child_name に値が返る。"""
    records = [{"type": "feeding", "started_at": "9時"}]
    llm = FakeLLMClient(_extract_resp_with_child(records, "たろう"))
    result_records, child_name = extract_records("たろうに授乳した", llm)
    assert child_name == "たろう"
    assert len(result_records) == 1


def test_extract_child_name_none_when_absent():
    """child フィールドがない場合 child_name は None。"""
    records = [{"type": "feeding", "started_at": "9時"}]
    llm = FakeLLMClient(_extract_resp(records))
    result_records, child_name = extract_records("授乳した", llm)
    assert child_name is None
