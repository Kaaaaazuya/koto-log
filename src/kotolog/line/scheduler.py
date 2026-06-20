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
    resp = llm_client.complete([{"role": "user", "content": prompt}])
    one_line = (resp.choices[0].message.content or "").strip()

    return f"おはよう！{day_label}\n{one_line}"


def _run_morning_push() -> None:
    """スケジューラから呼ばれる同期ジョブ本体。"""
    from kotolog.line.push import send_push
    from kotolog.llm.client import LLMClient

    cfg = load_config()
    if not cfg.line_channel_access_token:
        return

    conn = connect(cfg.db_url, cfg.turso_auth_token)
    due_date_str = crud.get_setting(conn, "due_date")
    line_user_id = crud.get_setting(conn, "line_user_id")

    if not due_date_str or not line_user_id:
        return

    try:
        due_date = date.fromisoformat(due_date_str)
    except ValueError:
        return

    remaining = (due_date - date.today()).days
    if remaining < 0:
        return

    llm = LLMClient(cfg)
    text = build_morning_text(remaining, llm)
    send_push(line_user_id, text, cfg.line_channel_access_token)


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

    return "\n".join(lines)


def _run_daily_summary_push() -> None:
    """毎晩 21:00 に当日の記録サマリーを push する。"""
    from kotolog.db.crud import ensure_child, query_records
    from kotolog.line.push import send_push

    cfg = load_config()
    if not cfg.line_channel_access_token:
        return

    conn = connect(cfg.db_url, cfg.turso_auth_token)
    line_user_id = crud.get_setting(conn, "line_user_id")
    if not line_user_id:
        return

    now = datetime.now(JST)
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    child_id = ensure_child(conn, cfg.default_child)
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

    send_push(line_user_id, text, cfg.line_channel_access_token)


async def _morning_job() -> None:
    await asyncio.to_thread(_run_morning_push)


async def _daily_summary_job() -> None:
    await asyncio.to_thread(_run_daily_summary_push)


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
    scheduler.start()
    return scheduler
