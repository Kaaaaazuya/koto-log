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

    def complete(self, messages, tools=None, tool_choice=None):
        self.last_tools = tools
        self.last_tool_choice = tool_choice
        return self._response


# ---------------------------------------------------------------------------
# extract_records
# ---------------------------------------------------------------------------


def test_extract_returns_empty_for_query():
    """質問文には空リストが返る。"""
    llm = FakeLLMClient(_extract_resp([]))
    result = extract_records("今日は何回飲んだ？", llm)
    assert result == []


def test_extract_returns_single_record():
    """1 件の授乳記録が正しく抽出される。"""
    records = [{"type": "feeding", "sub_type": "ミルク", "amount": 120, "unit": "ml", "started_at": "9時"}]
    llm = FakeLLMClient(_extract_resp(records))
    result = extract_records("9時にミルク120ml", llm)
    assert len(result) == 1
    assert result[0]["type"] == "feeding"
    assert result[0]["amount"] == 120


def test_extract_returns_multiple_records():
    """複数記録が全件抽出される。"""
    records = [
        {"type": "feeding", "sub_type": "母乳", "started_at": "9時"},
        {"type": "diaper", "sub_type": "おしっこ", "started_at": "9時"},
        {"type": "sleep", "started_at": "10時", "ended_at": "11時"},
    ]
    llm = FakeLLMClient(_extract_resp(records))
    result = extract_records("9時に母乳とおしっこ交換、10時から11時まで睡眠", llm)
    assert len(result) == 3
    types = [r["type"] for r in result]
    assert "feeding" in types
    assert "diaper" in types
    assert "sleep" in types


def test_extract_forces_tool_choice():
    """tool_choice が extract_records ツールを指定することを確認。"""
    llm = FakeLLMClient(_extract_resp([]))
    extract_records("テスト", llm)
    assert llm.last_tool_choice is not None
    assert llm.last_tool_choice.get("name") == "extract_records"


def test_extract_passes_extract_tool_only():
    """EXTRACT_TOOL のみが tools として渡される（通常ツール一覧ではない）。"""
    llm = FakeLLMClient(_extract_resp([]))
    extract_records("テスト", llm)
    assert llm.last_tools is not None
    assert len(llm.last_tools) == 1
    assert llm.last_tools[0]["function"]["name"] == "extract_records"


def test_extract_returns_empty_when_no_tool_calls():
    """tool_calls なし（テキスト返答）の場合は空リストを返す。"""
    llm = FakeLLMClient(_no_tool_resp())
    result = extract_records("テスト", llm)
    assert result == []


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
