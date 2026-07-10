"""トークン使用量の最小計測（P7 / ADR-0002）。

全 LLM 呼び出しは `LLMClient.complete()` を通るので、ここで使用量を 1 か所で捕捉し
差し替え可能な Sink へ流す。当面の実装は標準ログへ 1 行 JSON を吐く `JsonLogSink`。
Langfuse 移行時は `LangfuseSink` を 1 つ足すだけで呼び出し側は無変更。

計測スキーマは OTel GenAI / Langfuse に対応づく形（ADR-0002 の表）。
**育児ログ本文・引数値はイベントに一切含めない**（[[project-pii-check]]）。
"""

from __future__ import annotations

import contextvars
import json
import logging
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Protocol

import litellm

JST = timezone(timedelta(hours=9))

logger = logging.getLogger("kotolog.usage")

# 1 handle()／1 push ジョブ = 1 トレース。complete() がこれを読んでイベントへ載せる。
_trace_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar("kotolog_trace_id", default=None)


def new_trace_id() -> str:
    """新しいトレース ID を発行し contextvar にセットして返す。"""
    tid = uuid.uuid4().hex
    _trace_id_var.set(tid)
    return tid


def current_trace_id() -> str | None:
    """現在のトレース ID（未設定なら None）。"""
    return _trace_id_var.get()


@dataclass
class UsageEvent:
    """1 回の LLM 呼び出し = 1 ジェネレーション。本文・引数値は持たない。"""

    trace_id: str
    operation: str  # "extract" | "loop" | "push"
    model: str
    input_tokens: int
    output_tokens: int
    total_tokens: int
    cache_read_input_tokens: int
    cache_creation_input_tokens: int
    cost_usd: float | None
    ts: str


class UsageSink(Protocol):
    def record(self, event: UsageEvent) -> None: ...


class NullSink:
    """計測オフ時のデフォルト。何もしない。"""

    def record(self, event: UsageEvent) -> None:  # noqa: D102
        pass


class JsonLogSink:
    """当面の実装：1 行 JSON を標準ログに出す。"""

    def record(self, event: UsageEvent) -> None:  # noqa: D102
        logger.info("usage %s", json.dumps(asdict(event), ensure_ascii=False))


class DbSink:
    """`usage_log` テーブルへ永続化する Sink（Issue #68 / ADR-0002 DB永続化）。

    UsageEvent のフィールドをそのまま1行 INSERT する。line_user_id・本文・引数値は
    そもそも UsageEvent に含まれないため、ここでも永続化されるのは固定フィールドのみ
    （PII 最小化。列は [[project-pii-check]] 準拠）。
    例外は握りつぶさない：`LLMClient._record_usage` が best-effort で呼ぶため、
    ここで投げても本処理（記録・返信）は止まらない。
    """

    def __init__(self, conn: Any) -> None:
        self._conn = conn

    def record(self, event: UsageEvent) -> None:  # noqa: D102
        data = asdict(event)
        columns = ", ".join(data.keys())
        placeholders = ", ".join("?" for _ in data)
        # columns は UsageEvent の dataclass フィールド名のみ（外部入力ではない）。
        self._conn.execute(
            f"INSERT INTO usage_log ({columns}) VALUES ({placeholders})",  # nosec B608
            list(data.values()),
        )
        self._conn.commit()


class FanOutSink:
    """複数 Sink へ同一イベントを転送する（例: JsonLogSink と DbSink の併用）。"""

    def __init__(self, sinks: list[UsageSink]) -> None:
        self._sinks = sinks

    def record(self, event: UsageEvent) -> None:  # noqa: D102
        for sink in self._sinks:
            sink.record(event)


def sink_from_config(config: Any, conn: Any = None) -> UsageSink:
    """config のフラグから Sink を組み立てる。

    - `usage_log` が真: `JsonLogSink`（標準ログへ1行JSON）を加える
    - `usage_db` が真かつ `conn` が渡されている: `DbSink`（`usage_log` テーブル永続化）を加える
    - 両方有効なら `FanOutSink` で両方に流す。両方無効、または `usage_db` のみ有効で
      `conn` が無い場合は `NullSink`。
      （`conn` 未提供時に `usage_db` を無視するのは意図的な graceful degrade。
      DB 接続を持たない呼び出し元に conn 必須を強制しないための設計判断）
    """
    sinks: list[UsageSink] = []
    if getattr(config, "usage_log", False):
        sinks.append(JsonLogSink())
    if getattr(config, "usage_db", False) and conn is not None:
        sinks.append(DbSink(conn))
    if not sinks:
        return NullSink()
    if len(sinks) == 1:
        return sinks[0]
    return FanOutSink(sinks)


def _attr(obj: Any, key: str) -> Any:
    """litellm のレスポンスは属性／dict の両方がありうるので防御的に取り出す。"""
    if obj is None:
        return None
    if isinstance(obj, dict):
        return obj.get(key)
    return getattr(obj, key, None)


def _safe_cost(response: Any) -> float | None:
    """litellm.completion_cost を試み、失敗・0・未対応モデルは None とする。

    単価表が新モデルに追随していないと 0／例外になりうる（ADR-0002 リスク）。
    トークン数は別途必ず残すので、ここは None フォールバックで割り切る。
    """
    try:
        cost = litellm.completion_cost(completion_response=response)
    except Exception:  # noqa: BLE001 - 単価不明はすべて未取得扱い
        return None
    return float(cost) if cost else None


def _now_iso() -> str:
    return datetime.now(JST).isoformat()


def build_event(response: Any, *, operation: str, trace_id: str | None, ts: str | None = None) -> UsageEvent:
    """litellm レスポンスから UsageEvent を組み立てる。欠損は 0 埋め・防御的取得。"""
    usage = _attr(response, "usage")
    input_tokens = int(_attr(usage, "prompt_tokens") or 0)
    output_tokens = int(_attr(usage, "completion_tokens") or 0)
    total = _attr(usage, "total_tokens")
    total_tokens = int(total) if total else input_tokens + output_tokens

    details = _attr(usage, "prompt_tokens_details")
    cache_read = int(_attr(details, "cached_tokens") or 0)
    cache_creation = int(_attr(usage, "cache_creation_input_tokens") or 0)

    return UsageEvent(
        trace_id=trace_id or "",
        operation=operation,
        model=str(_attr(response, "model") or ""),
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=total_tokens,
        cache_read_input_tokens=cache_read,
        cache_creation_input_tokens=cache_creation,
        cost_usd=_safe_cost(response),
        ts=ts or _now_iso(),
    )
