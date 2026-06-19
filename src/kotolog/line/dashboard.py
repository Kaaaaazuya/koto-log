"""ダッシュボード画面（授乳タイムライン・日次サマリ）。"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from kotolog.db import crud
from kotolog.types import RecordType

router = APIRouter()
_templates = Jinja2Templates(directory=str(Path(__file__).parent.parent / "templates"))

JST = timezone(timedelta(hours=9))


def _check_token(token: str | None) -> None:
    expected = os.environ.get("KOTOLOG_DASHBOARD_TOKEN", "")
    if expected and token != expected:
        raise HTTPException(status_code=403, detail="Invalid token")


def _get_conn_and_child():
    from kotolog.line.webhook import _get_agent

    agent = _get_agent()
    return agent.executor.conn, agent.executor.child_id


@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request, token: str | None = None):
    _check_token(token)
    conn, child_id = _get_conn_and_child()

    now = datetime.now(JST)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

    feedings_today = crud.query_records(
        conn,
        child_id=child_id,
        start=today_start.isoformat(),
        end=now.isoformat(),
        type=RecordType.FEEDING,
    )

    daily_summaries = []
    for i in range(6, -1, -1):
        day = now - timedelta(days=i)
        day_start = day.replace(hour=0, minute=0, second=0, microsecond=0)
        day_end = day.replace(hour=23, minute=59, second=59)
        records = crud.query_records(
            conn,
            child_id=child_id,
            start=day_start.isoformat(),
            end=day_end.isoformat(),
            type=RecordType.FEEDING,
        )
        total_ml = sum(r["amount"] or 0 for r in records if r["unit"] in ("ml", None))
        daily_summaries.append(
            {
                "date": day.strftime("%m/%d"),
                "count": len(records),
                "total_ml": int(total_ml),
            }
        )

    return _templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "feedings_today": [dict(r) for r in feedings_today],
            "daily_summaries": daily_summaries,
            "now": now.strftime("%Y/%m/%d %H:%M"),
            "token": token or "",
        },
    )
