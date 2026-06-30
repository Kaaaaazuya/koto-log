"""まとめ入力の一括抽出（P6）。

単一の LLM 呼び出し（force tool calling）でメッセージから全育児記録を抽出する。
記録でない（質問・集計・修正など）場合は空リストを返し、呼び出し元が
通常のツール使用ループにフォールバックする。
"""

from __future__ import annotations

import json

from kotolog.types import RECORD_TYPE_LABELS

_EXTRACT_TOOL = {
    "type": "function",
    "function": {
        "name": "extract_records",
        "description": (
            "メッセージに含まれる全ての育児記録（授乳・睡眠・おむつ・体温・離乳食・お風呂・薬・病院・外出）を抽出して返す。"
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
                                "enum": [
                                    "feeding",
                                    "sleep",
                                    "diaper",
                                    "temp",
                                    "baby_food",
                                    "bath",
                                    "medicine",
                                    "hospital",
                                    "outing",
                                ],
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
                },
                "child": {
                    "type": "string",
                    "description": "記録対象の子の名前（複数児がいる場合にユーザーが明示した場合のみ設定）",
                },
            },
            "required": ["records"],
        },
    },
}

_EXTRACT_SYSTEM = (
    "育児記録抽出アシスタント。"
    "メッセージから授乳・睡眠・おむつ・体温・離乳食・お風呂・薬・病院・外出の記録を全て抽出せよ。"
    "記録でない（質問・集計・修正など）は records を空リストにする。"
)

_TYPE_LABELS = RECORD_TYPE_LABELS


def extract_records(text: str, llm_client) -> tuple[list[dict], str | None]:
    """テキストから育児記録リストを抽出する。

    Returns:
        (records, child_name): records は抽出された記録リスト（記録でない場合は空リスト）、
        child_name はユーザーが明示した対象児名（未指定なら None）。
    """
    resp = llm_client.complete(
        messages=[
            {"role": "system", "content": _EXTRACT_SYSTEM},
            {"role": "user", "content": text},
        ],
        tools=[_EXTRACT_TOOL],
        tool_choice={"type": "function", "function": {"name": "extract_records"}},
        operation="extract",
    )
    message = resp.choices[0].message
    tool_calls = getattr(message, "tool_calls", None)
    if not tool_calls:
        return [], None
    try:
        args = json.loads(tool_calls[0].function.arguments)
    except (json.JSONDecodeError, AttributeError):
        return [], None
    records = args.get("records") or []
    child_name = args.get("child") or None
    return records, child_name


def format_confirmation(saved: list[dict], child_name: str | None = None) -> str:
    """保存済み記録リストをテンプレートで確認文に変換する。

    child_name が指定された場合は先頭に「【child_name】\n」を付与する。
    """
    lines = []
    for r in saved:
        label = _TYPE_LABELS.get(r.get("type", ""), r.get("type", ""))
        time = r.get("started_at", "")
        if len(time) >= 16:
            time = time[11:16]
        sub = f"({r['sub_type']})" if r.get("sub_type") else ""
        if r.get("amount"):
            try:
                amt = float(r["amount"])
                amt_str = str(int(amt)) if amt == int(amt) else str(amt)
            except (ValueError, TypeError):
                amt_str = str(r["amount"])
            unit = r.get("unit") or ("ml" if r.get("type") == "feeding" else "")
            amount = f" {amt_str}{unit}"
        else:
            amount = ""
        lines.append(f"{label}{sub}{amount}（{time}）記録した")
    body = "\n".join(lines)
    if child_name:
        return f"【{child_name}】\n{body}"
    return body
