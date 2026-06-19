"""エージェント・ループ（T1.5）。

入力 → LLM(ツール定義つき) → ツール実行 → 結果を戻す → … を繰り返し、
最終的に確認サマリ／回答文を返す（Design Doc §7.1）。

小型ローカルLLMは構造化 tool_calls を時々外し、本文に JSON として吐くことが
あるため、その場合のフォールバック解析を備える。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from kotolog.tools.definitions import TOOLS
from kotolog.tools.executor import ToolExecutor

MAX_ITERS = 5

SYSTEM_PROMPT = """あなたは育児記録アシスタントです。授乳・睡眠・おむつなどの記録を、
ツールを使って保存・集計・修正します。

- 記録・集計・修正/取り消しは必ず対応するツールを呼び出して行うこと。
- 時刻はユーザーが言ったまま（「さっき」「3時」「お昼」等の相対表現でよい）ツールに
  渡すこと。自分で絶対時刻に変換しないこと。サーバ側で正規化します。
- 「今日のまとめ」「今週どうだった?」のような振り返りは query_records で集計し、
  返ってくる集計値（by_type / by_sub_type の回数・量）をそのまま使って日本語でまとめること。
  自分で件数や合計を数え直さないこと。
- 「前回の◯◯はいつ?」には query_records を period=latest で呼び、経過時間で答えること。
- 書き込み（保存・修正・削除）の後は、何をどう記録したかを日本語で短く確認してください。
- 量や種別が曖昧で記録に必要な情報が足りない場合は、推測せずユーザーに聞き返してください。
- 健康に関する相談には断定せず、心配なら受診を促してください。
"""

# 本文中に紛れ込んだ {"name":..., "arguments":...} を拾うための緩い JSON 抽出
_JSON_OBJ_RE = re.compile(r"\{.*\}", re.DOTALL)


@dataclass
class _Call:
    id: str
    name: str
    args: dict


def _parse_args(raw) -> dict:
    if isinstance(raw, dict):
        return raw
    try:
        return json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return {}


def _fallback_parse(content: str | None) -> list[_Call]:
    """構造化 tool_calls が無いとき、本文から1件のツール呼び出しを復元する。"""
    if not content:
        return []
    m = _JSON_OBJ_RE.search(content)
    if not m:
        return []
    try:
        obj = json.loads(m.group(0))
    except json.JSONDecodeError:
        return []
    name = obj.get("name")
    if not name:
        return []
    return [_Call(id="fallback_0", name=name, args=_parse_args(obj.get("arguments") or {}))]


def _extract_calls(message) -> list[_Call]:
    tool_calls = getattr(message, "tool_calls", None)
    if tool_calls:
        return [
            _Call(id=tc.id, name=tc.function.name, args=_parse_args(tc.function.arguments))
            for tc in tool_calls
        ]
    return _fallback_parse(getattr(message, "content", None))


class Agent:
    def __init__(
        self,
        client,
        executor: ToolExecutor,
        system_prompt: str = SYSTEM_PROMPT,
        max_iters: int = MAX_ITERS,
    ) -> None:
        self.client = client
        self.executor = executor
        self.system_prompt = system_prompt
        self.max_iters = max_iters

    def handle(self, user_text: str, history: list[dict] | None = None) -> str:
        """1 ターンを処理し、ユーザーへ返す文字列を返す。"""
        messages: list[dict] = [{"role": "system", "content": self.system_prompt}]
        if history:
            messages.extend(history)
        messages.append({"role": "user", "content": user_text})

        for _ in range(self.max_iters):
            resp = self.client.complete(messages, tools=TOOLS)
            message = resp.choices[0].message
            calls = _extract_calls(message)

            if not calls:
                return message.content or ""

            messages.append(self._assistant_message(message, calls))
            for call in calls:
                result = self._run_tool(call)
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call.id,
                        "content": json.dumps(result, ensure_ascii=False, default=str),
                    }
                )

        return "すみません、うまく処理できませんでした。もう一度お願いします。"

    def _run_tool(self, call: _Call) -> dict:
        # 未知ツールや不正引数でループを落とさず、結果としてLLMに戻す
        try:
            return self.executor.execute(call.name, call.args)
        except Exception as e:  # noqa: BLE001 - LLM由来の予期せぬ呼び出しを吸収する
            return {"ok": False, "error": f"{type(e).__name__}: {e}"}

    @staticmethod
    def _assistant_message(message, calls: list[_Call]) -> dict:
        """ネイティブ/フォールバックを問わず OpenAI 形式の assistant ターンへ正規化。"""
        return {
            "role": "assistant",
            "content": getattr(message, "content", "") or "",
            "tool_calls": [
                {
                    "id": c.id,
                    "type": "function",
                    "function": {
                        "name": c.name,
                        "arguments": json.dumps(c.args, ensure_ascii=False),
                    },
                }
                for c in calls
            ],
        }
