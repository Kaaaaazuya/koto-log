"""朝のカウントダウン Push 通知スケジューラー。

APScheduler AsyncIOScheduler で毎朝 7:00 JST に起動し、
出産予定日までの残り日数と LLM 生成の一言を LINE Push で送る。
"""

from __future__ import annotations

import asyncio
from datetime import date, timedelta, timezone

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from kotolog.config import load_config

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
    if not cfg.due_date or not cfg.line_user_id or not cfg.line_channel_access_token:
        return

    today = date.today()
    remaining = (cfg.due_date - today).days
    if remaining < 0:
        return

    llm = LLMClient(cfg)
    text = build_morning_text(remaining, llm)
    send_push(cfg.line_user_id, text, cfg.line_channel_access_token)


async def _morning_job() -> None:
    await asyncio.to_thread(_run_morning_push)


def start_scheduler() -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        _morning_job,
        CronTrigger(hour=7, minute=0, timezone="Asia/Tokyo"),
    )
    scheduler.start()
    return scheduler
