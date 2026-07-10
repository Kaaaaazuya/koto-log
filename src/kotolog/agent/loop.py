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
from datetime import datetime, timedelta, timezone

from kotolog.agent.extractor import extract_records, format_confirmation
from kotolog.agent.prompts import load_prompt
from kotolog.db import crud
from kotolog.obs.usage import new_trace_id
from kotolog.tools.definitions import TOOLS
from kotolog.tools.executor import ToolExecutor

JST = timezone(timedelta(hours=9))

MAX_ITERS = 5
# Issue #38: 会話文脈として保持する往復数（history はこの件数×2 メッセージまでに切り詰める）
MAX_CONTEXT_TURNS = 3

PROMPT_VERSION = "v1"
SYSTEM_PROMPT = load_prompt("loop", PROMPT_VERSION)

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
        conn,
        system_prompt: str = SYSTEM_PROMPT,
        max_iters: int = MAX_ITERS,
        config=None,
        _now=None,
    ) -> None:
        self.client = client
        self.conn = conn
        self.config = config
        self.system_prompt = system_prompt
        self.max_iters = max_iters
        self._now = _now or (lambda: datetime.now(JST))

    def handle(
        self,
        user_text: str,
        line_user_id: str | None = None,
        history: list[dict] | None = None,
    ) -> str:
        """1 ターンを処理し、ユーザーへ返す文字列を返す。"""
        # この handle() 内の全 LLM 呼び出し（extract / loop）を 1 トレースに紐付ける。
        new_trace_id()

        # Issue #37: Check LLM call rate limit at the start to avoid unnecessary work
        if (
            line_user_id
            and self.config
            and not crud.check_rate_limit(self.conn, line_user_id, "llm_call", self.config.user_llm_limit)
        ):
            return "申し訳ありません。LLM呼び出しの上限に達しました。しばらく待ってからお試しください。"

        # Issue #38: history が明示指定されなければ、直近の会話文脈をDBから読み込む。
        # LLMが聞き返した後の返答も、この文脈があって初めて正しく解釈できる。
        if history is None and line_user_id:
            history = crud.get_session_context(self.conn, line_user_id)

        extracted, child_name_hint = extract_records(user_text, self.client)

        # Issue #37: Increment LLM call counter after extraction
        if line_user_id and self.config:
            crud.increment_rate_limit(self.conn, line_user_id, "llm_call")
        try:
            child_id = crud.resolve_child_id(
                self.conn, line_user_id=line_user_id, child_name_hint=child_name_hint
            )
        except RuntimeError as e:
            return str(e)
        executor = ToolExecutor(conn=self.conn, child_id=child_id, now=self._now())

        if extracted:
            saved = []
            for record in extracted:
                try:
                    result = executor.execute("save_record", record)
                    if result.get("ok") and result.get("record"):
                        saved.append(result["record"])
                except Exception:  # noqa: BLE001
                    pass
            if saved:
                children = crud.list_children(self.conn)
                display_name = crud.get_child_name(self.conn, child_id) if len(children) > 1 else None
                reply = format_confirmation(saved, child_name=display_name)
                if line_user_id:
                    self._save_session_context(line_user_id, history, user_text, reply)
                return reply

        messages: list[dict] = [{"role": "system", "content": self.system_prompt}]
        if history:
            messages.extend(history)
        messages.append({"role": "user", "content": user_text})

        for _ in range(self.max_iters):
            resp = self.client.complete(messages, tools=TOOLS, operation="loop")
            message = resp.choices[0].message
            calls = _extract_calls(message)

            # Issue #37: Increment LLM call counter after each loop iteration
            if line_user_id and self.config:
                crud.increment_rate_limit(self.conn, line_user_id, "llm_call")

            if not calls:
                reply = message.content or ""
                if line_user_id:
                    self._save_session_context(line_user_id, history, user_text, reply)
                return reply

            messages.append(self._assistant_message(message, calls))
            for call in calls:
                result = self._run_tool(call, executor)
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call.id,
                        "content": json.dumps(result, ensure_ascii=False, default=str),
                    }
                )

        reply = "すみません、うまく処理できませんでした。もう一度お願いします。"
        if line_user_id:
            self._save_session_context(line_user_id, history, user_text, reply)
        return reply

    def _save_session_context(
        self,
        line_user_id: str,
        prior_history: list[dict] | None,
        user_text: str,
        reply_text: str,
    ) -> None:
        """今回の往復を会話文脈に追加し、直近 MAX_CONTEXT_TURNS 往復分に切り詰めて保存する。"""
        context = (prior_history or []) + [
            {"role": "user", "content": user_text},
            {"role": "assistant", "content": reply_text},
        ]
        crud.set_session_context(self.conn, line_user_id, context[-(MAX_CONTEXT_TURNS * 2) :])

    def _run_tool(self, call: _Call, executor: ToolExecutor) -> dict:
        # 未知ツールや不正引数でループを落とさず、結果としてLLMに戻す
        try:
            return executor.execute(call.name, call.args)
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
