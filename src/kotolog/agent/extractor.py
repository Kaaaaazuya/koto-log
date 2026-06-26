"""まとめ入力の一括抽出（P6）。

単一の LLM 呼び出し（force tool calling）でメッセージから全育児記録を抽出する。
記録でない（質問・集計・修正など）場合は空リストを返し、呼び出し元が
通常のツール使用ループにフォールバックする。
"""

from __future__ import annotations

import json

_EXTRACT_TOOL = {
    "type": "function",
    "function": {
        "name": "extract_records",
        "description": (
            "メッセージに含まれる全ての育児記録（授乳・睡眠・おむつ・体温）を抽出して返す。"
            "記録でない場合（質問・集計・修正・設定変更など）は records を空リストにする。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "records": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "type": {
                                "type": "string",
                                "enum": ["feeding", "sleep", "diaper", "temp"],
                            },
                            "sub_type": {
                                "type": "string",
                                "description": "授乳: 母乳/ミルク/搾母乳。おむつ: おしっこ/うんち",
                            },
                            "amount": {"type": "number"},
                            "unit": {"type": "string", "description": "ml など"},
                            "started_at": {
                                "type": "string",
                                "description": "相対表現のままでよい（例: 9時、さっき、14:30）",
                            },
                            "ended_at": {"type": "string"},
                            "note": {"type": "string"},
                        },
                        "required": ["type", "started_at"],
                    },
                }
            },
            "required": ["records"],
        },
    },
}

_EXTRACT_SYSTEM = (
    "育児記録抽出アシスタント。"
    "メッセージから授乳・睡眠・おむつ・体温の記録を全て抽出せよ。"
    "記録でない（質問・集計・修正など）は records を空リストにする。"
)

_TYPE_LABELS = {"feeding": "授乳", "sleep": "睡眠", "diaper": "おむつ", "temp": "体温"}


def extract_records(text: str, llm_client) -> list[dict]:
    """テキストから育児記録リストを抽出する。記録でない場合は空リストを返す。"""
    resp = llm_client.complete(
        messages=[
            {"role": "system", "content": _EXTRACT_SYSTEM},
            {"role": "user", "content": text},
        ],
        tools=[_EXTRACT_TOOL],
        tool_choice={"type": "function", "function": {"name": "extract_records"}},
    )
    message = resp.choices[0].message
    tool_calls = getattr(message, "tool_calls", None)
    if not tool_calls:
        return []
    try:
        args = json.loads(tool_calls[0].function.arguments)
    except (json.JSONDecodeError, AttributeError):
        return []
    return args.get("records") or []


def format_confirmation(saved: list[dict]) -> str:
    """保存済み記録リストをテンプレートで確認文に変換する。"""
    lines = []
    for r in saved:
        label = _TYPE_LABELS.get(r.get("type", ""), r.get("type", ""))
        time = r.get("started_at", "")
        if len(time) >= 16:
            time = time[11:16]
        sub = f"({r['sub_type']})" if r.get("sub_type") else ""
        amount = f" {int(r['amount'])}{r.get('unit') or 'ml'}" if r.get("amount") else ""
        lines.append(f"{label}{sub}{amount}（{time}）記録した")
    return "\n".join(lines)
