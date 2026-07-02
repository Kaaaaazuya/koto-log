"""ダッシュボード画面（授乳タイムライン・日次サマリ）。"""

from __future__ import annotations

import json
import os
import secrets
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from importlib import resources
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from kotolog.db import crud
from kotolog.types import DiaperSubType, RecordType

router = APIRouter()
_templates = Jinja2Templates(directory=str(Path(__file__).parent.parent / "templates"))

JST = timezone(timedelta(hours=9))

_ICONS: dict[str, str] = {
    "feeding": "🍼",
    "sleep": "🌙",
    "diaper": "💧",
    "temp": "🌡️",
}
_TYPE_LABELS: dict[str, str] = {
    "feeding": "授乳",
    "sleep": "睡眠",
    "diaper": "おむつ",
    "temp": "体温",
}


def _timeline_label(r: dict) -> str:
    t = r.get("type", "")
    sub = r.get("sub_type") or ""
    amount = r.get("amount")
    unit = r.get("unit") or "ml"
    if t == "feeding":
        parts = [sub] if sub else []
        if amount:
            parts.append(f"{int(amount)}{unit}")
        return " · ".join(parts) or "授乳"
    if t == "sleep":
        if r.get("ended_at"):
            try:
                s = datetime.fromisoformat(r["started_at"])
                e = datetime.fromisoformat(r["ended_at"])
                mins = int((e - s).total_seconds() / 60)
                h, m = divmod(mins, 60)
                return f"{h}h{m:02d}m" if m else f"{h}h"
            except Exception:
                pass
        return "睡眠中"
    if t == "diaper":
        return sub or "おむつ"
    if t == "temp":
        return f"{amount}℃" if amount else "体温"
    return t


def _check_token(token: str | None) -> None:
    """Default-deny authentication: always require a valid token.

    Issue #27: Dashboard MUST require authentication regardless of environment.
    Uses secrets.compare_digest for timing-attack resistant token comparison.
    """
    expected = os.environ.get("KOTOLOG_DASHBOARD_TOKEN", "")
    # Default-deny: reject unless token is explicitly configured and matches
    if not expected or not token or not secrets.compare_digest(token, expected):
        raise HTTPException(status_code=403, detail="Invalid token")


def _get_conn_and_child():
    from kotolog.line.webhook import _get_agent

    conn = _get_agent().conn
    child_id = crud.get_default_child_id(conn)
    return conn, child_id


@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request, token: str | None = None, days: int = 7):
    _check_token(token)
    days = max(1, min(days, 90))
    conn, child_id = _get_conn_and_child()

    now = datetime.now(JST)

    def _day_records(type, offset_days=None, *, day=None):
        if day is None:
            day = now - timedelta(days=offset_days)
        start = day.replace(hour=0, minute=0, second=0, microsecond=0)
        end = day.replace(hour=23, minute=59, second=59)
        return crud.query_records(
            conn, child_id=child_id, start=start.isoformat(), end=end.isoformat(), type=type
        )

    def _sleep_hours(records) -> float:
        total = 0.0
        for r in records:
            if r["ended_at"] and r["started_at"]:
                try:
                    s = datetime.fromisoformat(r["started_at"])
                    e = datetime.fromisoformat(r["ended_at"])
                    total += (e - s).total_seconds() / 3600
                except ValueError:
                    pass
        return round(total, 1)

    feedings_today = _day_records(RecordType.FEEDING, day=now)
    sleeps_today = _day_records(RecordType.SLEEP, day=now)
    diapers_today = _day_records(RecordType.DIAPER, day=now)

    # 今日のタイムライン（全カテゴリを時系列降順に並べる）
    _all_today = [dict(r) for r in feedings_today + sleeps_today + diapers_today]
    for _r in _all_today:
        _r["icon"] = _ICONS.get(_r["type"], "📝")
        _r["type_label"] = _TYPE_LABELS.get(_r["type"], _r["type"])
        _r["detail"] = _timeline_label(_r)
    timeline_today = sorted(_all_today, key=lambda r: r.get("started_at", ""), reverse=True)

    # 今日の睡眠合計（サマリーカード表示用）
    sleep_hours_today = _sleep_hours(sleeps_today)
    _sh = int(sleep_hours_today)
    _sm = int(round((sleep_hours_today - _sh) * 60))
    sleep_today_str = f"{_sh}h{_sm:02d}m" if sleep_hours_today > 0 else "—"

    feeding_summaries, sleep_summaries, diaper_summaries = [], [], []
    for i in range(days - 1, -1, -1):
        day = now - timedelta(days=i)
        label = day.strftime("%m/%d")

        f_recs = _day_records(RecordType.FEEDING, day=day)
        total_ml = sum(r["amount"] or 0 for r in f_recs if r["unit"] in ("ml", None))
        feeding_summaries.append({"date": label, "count": len(f_recs), "total_ml": int(total_ml)})

        s_recs = _day_records(RecordType.SLEEP, day=day)
        sleep_summaries.append({"date": label, "count": len(s_recs), "hours": _sleep_hours(s_recs)})

        d_recs = _day_records(RecordType.DIAPER, day=day)
        poo = sum(1 for r in d_recs if r["sub_type"] == DiaperSubType.POO)
        pee = sum(1 for r in d_recs if r["sub_type"] == DiaperSubType.PEE)
        diaper_summaries.append({"date": label, "total": len(d_recs), "poo": poo, "pee": pee})

    return _templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "feedings_today": [dict(r) for r in feedings_today],
            "sleeps_today": [dict(r) for r in sleeps_today],
            "diapers_today": [dict(r) for r in diapers_today],
            "timeline_today": timeline_today,
            "sleep_today_str": sleep_today_str,
            "feeding_summaries": feeding_summaries,
            "sleep_summaries": sleep_summaries,
            "diaper_summaries": diaper_summaries,
            "days": days,
            "now": now.strftime("%Y/%m/%d %H:%M"),
            "token": token or "",
        },
    )


@lru_cache(maxsize=1)
def _load_growth_standards() -> dict:
    text = resources.files("kotolog").joinpath("data", "growth_standards.json").read_text(encoding="utf-8")
    return json.loads(text)


@router.get("/dashboard/growth", response_class=HTMLResponse)
async def dashboard_growth(request: Request, token: str | None = None):
    """成長曲線ページ（身長・体重と標準値の比較）。"""
    _check_token(token)
    conn, child_id = _get_conn_and_child()

    now = datetime.now(JST)
    start = (now - timedelta(days=365 * 3)).isoformat()

    height_records = crud.query_records(
        conn, child_id=child_id, start=start, end=now.isoformat(), type=RecordType.HEIGHT
    )
    weight_records = crud.query_records(
        conn, child_id=child_id, start=start, end=now.isoformat(), type=RecordType.WEIGHT
    )

    child_row = conn.execute("SELECT birthday, sex FROM children WHERE id = ?", (child_id,)).fetchone()
    birthday = child_row["birthday"] if child_row else None
    sex = (child_row["sex"] if child_row else None) or "male"

    bd = None
    if birthday:
        try:
            bd = datetime.fromisoformat(birthday).replace(tzinfo=None)
        except (ValueError, TypeError):
            pass

    def _age_months(iso_date: str) -> float | None:
        if bd is None:
            return None
        try:
            rec = datetime.fromisoformat(iso_date).replace(tzinfo=None)
            return max(0.0, (rec - bd).days / 30.44)
        except (ValueError, TypeError):
            return None

    def _to_chart_data(records) -> list[dict]:
        points = []
        for r in records:
            age = _age_months(r["started_at"])
            if age is not None and r["amount"] is not None:
                points.append({"x": round(age, 1), "y": r["amount"]})
        return sorted(points, key=lambda p: p["x"])

    standards = _load_growth_standards()
    std = standards.get(sex, standards["male"])

    return _templates.TemplateResponse(
        request,
        "dashboard_growth.html",
        {
            "token": token or "",
            "height_data": json.dumps(_to_chart_data(height_records)),
            "weight_data": json.dumps(_to_chart_data(weight_records)),
            "std_months": json.dumps(std["height"]["months"]),
            "height_p3": json.dumps(std["height"]["p3"]),
            "height_p50": json.dumps(std["height"]["p50"]),
            "height_p97": json.dumps(std["height"]["p97"]),
            "weight_p3": json.dumps(std["weight"]["p3"]),
            "weight_p50": json.dumps(std["weight"]["p50"]),
            "weight_p97": json.dumps(std["weight"]["p97"]),
            "has_birthday": bool(birthday),
            "sex": sex,
        },
    )
