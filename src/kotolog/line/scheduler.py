"""Push 通知スケジューラー。

- 毎朝 7:00 JST: 出産予定日カウントダウン + LLM 一言
- 毎晩 21:00 JST: 当日の育児記録サマリー（記録なしはスキップ）
"""

from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta, timezone

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from kotolog.config import load_config
from kotolog.db import crud
from kotolog.db.connection import connect

JST = timezone(timedelta(hours=9))


def build_morning_text(remaining: int, llm_client) -> str:
    """残り日数と LLM 一言を組み合わせてメッセージを生成する。"""
    if remaining == 0:
        day_label = "今日が予定日！"
    elif remaining == 1:
        day_label = "明日が予定日！"
    else:
        day_label = f"予定日まであと {remaining} 日。"

    prompt = (
        f"出産予定日まであと{remaining}日の妊婦への応援メッセージを常態で1文だけ返せ。"
        "余計な前置きや説明は不要。"
    )
    resp = llm_client.complete([{"role": "user", "content": prompt}], operation="push")
    one_line = (resp.choices[0].message.content or "").strip()

    return f"おはよう！{day_label}\n{one_line}"


def _run_morning_push() -> None:
    """スケジューラから呼ばれる同期ジョブ本体。notify_enabled=True の全員へ送信。"""
    from kotolog.llm.client import LLMClient
    from kotolog.obs.usage import new_trace_id, sink_from_config

    cfg = load_config()
    if not cfg.line_channel_access_token:
        return

    conn = connect(cfg.db_url, cfg.turso_auth_token)
    due_date_str = crud.get_setting(conn, "due_date")

    if not due_date_str:
        return

    try:
        due_date = date.fromisoformat(due_date_str)
    except ValueError:
        return

    remaining = (due_date - date.today()).days
    if remaining < 0:
        return

    new_trace_id()  # この push ジョブの LLM 呼び出しを 1 トレースに紐付ける。
    llm = LLMClient(cfg, sink=sink_from_config(cfg))
    text = build_morning_text(remaining, llm)
    _fanout_push(conn, text, cfg.line_channel_access_token)


def build_daily_summary_text(today_str: str, records: list[dict]) -> str | None:
    """当日の記録からサマリーテキストを生成する。記録がなければ None。"""
    if not records:
        return None

    from collections import Counter

    feeding = [r for r in records if r["type"] == "feeding"]
    sleep = [r for r in records if r["type"] == "sleep"]
    diaper = [r for r in records if r["type"] == "diaper"]
    temp = [r for r in records if r["type"] == "temp"]

    lines = [f"今日のまとめ（{today_str}）", ""]

    if feeding:
        total_ml = sum(r["amount"] or 0 for r in feeding if r.get("unit") in ("ml", None))
        sub = Counter(r["sub_type"] for r in feeding if r.get("sub_type"))
        detail = "・".join(f"{k}{v}回" for k, v in sub.items()) if sub else ""
        ml_str = f" 計{int(total_ml)}ml" if total_ml else ""
        lines.append(f"授乳: {len(feeding)}回{ml_str}" + (f"（{detail}）" if detail else ""))

    if sleep:
        lines.append(f"睡眠: {len(sleep)}回")

    if diaper:
        sub = Counter(r["sub_type"] for r in diaper if r.get("sub_type"))
        detail = "・".join(f"{k}{v}回" for k, v in sub.items()) if sub else ""
        lines.append(f"おむつ: {len(diaper)}回" + (f"（{detail}）" if detail else ""))

    if temp:
        temps = [r["amount"] for r in temp if r.get("amount")]
        if temps:
            lines.append(f"体温: 最高{max(temps):.1f}℃")

    # Issue #39: 後から追加した記録種別もサマリー対象にする
    if baby_food := [r for r in records if r["type"] == "baby_food"]:
        lines.append(f"離乳食: {len(baby_food)}回")

    if any(r["type"] == "bath" for r in records):
        lines.append("お風呂: 済み")

    if medicine := [r for r in records if r["type"] == "medicine"]:
        lines.append(f"薬: {len(medicine)}回")

    if hospital := [r for r in records if r["type"] == "hospital"]:
        lines.append(f"病院: {len(hospital)}回")

    if outing := [r for r in records if r["type"] == "outing"]:
        lines.append(f"外出: {len(outing)}回")

    return "\n".join(lines)


def _fanout_push(conn, text: str, access_token: str, send_fn=None) -> None:
    """notify_enabled=True の全ユーザーにテキストを Push する。

    send_fn はテスト用の差し替えポイント。省略時は send_push を使う。
    """
    if send_fn is None:
        from kotolog.line.push import send_push as send_fn  # noqa: PLC0415

    for user in crud.get_notify_users(conn):
        try:
            send_fn(user["line_user_id"], text, access_token)
        except Exception as exc:  # noqa: BLE001
            print(f"[fanout] push failed for {user['line_user_id']}: {exc}", flush=True)


def _run_daily_summary_push() -> None:
    """毎晩 21:00 に当日の記録サマリーを notify_enabled=True の全員へ push する。"""
    from kotolog.db.crud import get_default_child_id, query_records

    cfg = load_config()
    if not cfg.line_channel_access_token:
        return

    conn = connect(cfg.db_url, cfg.turso_auth_token)
    child_id = get_default_child_id(conn)
    if child_id is None:
        return

    now = datetime.now(JST)
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    records = [
        dict(r)
        for r in query_records(
            conn,
            child_id=child_id,
            start=day_start.isoformat(),
            end=now.isoformat(),
        )
    ]

    text = build_daily_summary_text(now.strftime("%-m/%-d"), records)
    if text is None:
        return

    _fanout_push(conn, text, cfg.line_channel_access_token)


async def _morning_job() -> None:
    await asyncio.to_thread(_run_morning_push)


async def _daily_summary_job() -> None:
    await asyncio.to_thread(_run_daily_summary_push)


def _run_processed_events_cleanup() -> None:
    """毎日深夜、期限切れの processed_events（Issue #47）を削除する。"""
    cfg = load_config()
    conn = connect(cfg.db_url, cfg.turso_auth_token)
    deleted = crud.cleanup_old_processed_events(conn)
    if deleted:
        print(f"[cleanup] removed {deleted} old processed_events rows", flush=True)


async def _cleanup_job() -> None:
    await asyncio.to_thread(_run_processed_events_cleanup)


def start_scheduler() -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        _morning_job,
        CronTrigger(hour=7, minute=0, timezone="Asia/Tokyo"),
    )
    scheduler.add_job(
        _daily_summary_job,
        CronTrigger(hour=21, minute=0, timezone="Asia/Tokyo"),
    )
    scheduler.add_job(
        _cleanup_job,
        CronTrigger(hour=3, minute=0, timezone="Asia/Tokyo"),
    )
    scheduler.start()
    return scheduler
