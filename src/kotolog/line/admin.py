"""管理画面（設定の確認・編集・テスト送信）。"""

from __future__ import annotations

import asyncio
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from kotolog.db import crud
from kotolog.types import RecordType
from kotolog.utils.subtype import normalize_sub_type

router = APIRouter()
_templates = Jinja2Templates(directory=str(Path(__file__).parent.parent / "templates"))

JST = timezone(timedelta(hours=9))


def _check_token(token: str | None) -> None:
    expected = os.environ.get("KOTOLOG_DASHBOARD_TOKEN", "")
    if expected and token != expected:
        raise HTTPException(status_code=403, detail="Invalid token")


def _get_conn():
    from kotolog.line.webhook import _get_agent

    return _get_agent().executor.conn


def _get_conn_and_child():
    from kotolog.line.webhook import _get_agent

    agent = _get_agent()
    return agent.executor.conn, agent.executor.child_id


def _to_iso_jst(dt_local: str) -> str:
    """datetime-local の "YYYY-MM-DDTHH:MM" を JST ISO8601 へ変換する。"""
    try:
        return datetime.fromisoformat(dt_local).replace(tzinfo=JST).isoformat()
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date format")


def _to_input_value(iso: str | None) -> str:
    """ISO8601 を datetime-local の value（"YYYY-MM-DDTHH:MM"）へ。空なら ""。"""
    if not iso:
        return ""
    return datetime.fromisoformat(iso).strftime("%Y-%m-%dT%H:%M")


def _parse_amount(value: str) -> float | None:
    value = value.strip()
    if not value:
        return None
    try:
        return float(value)
    except ValueError:
        return None


@router.get("/admin", response_class=HTMLResponse)
async def admin_page(
    request: Request,
    token: str | None = None,
    saved: str | None = None,
    sent: str | None = None,
):
    _check_token(token)
    conn = _get_conn()
    return _templates.TemplateResponse(
        request,
        "admin.html",
        {
            "due_date": crud.get_setting(conn, "due_date") or "",
            "line_user_id": crud.get_setting(conn, "line_user_id") or "",
            "token": token or "",
            "saved": saved == "1",
            "sent": sent == "1",
        },
    )


@router.post("/admin", response_class=HTMLResponse)
async def admin_save(
    token: str | None = None,
    due_date: str = Form(default=""),
    line_user_id: str = Form(default=""),
):
    _check_token(token)
    conn = _get_conn()
    if due_date.strip():
        crud.set_setting(conn, "due_date", due_date.strip())
    if line_user_id.strip():
        crud.set_setting(conn, "line_user_id", line_user_id.strip())
    return RedirectResponse(f"/admin?token={token or ''}&saved=1", status_code=303)


@router.post("/admin/test-push")
async def admin_test_push(token: str | None = None):
    _check_token(token)
    from kotolog.line.scheduler import _run_morning_push

    await asyncio.to_thread(_run_morning_push)
    return RedirectResponse(f"/admin?token={token or ''}&sent=1", status_code=303)


# --- 記録 CRUD（AIなし手動編集 / ADR-0003） --------------------------------

_TYPE_LABELS = {
    RecordType.FEEDING: "授乳",
    RecordType.SLEEP: "睡眠",
    RecordType.DIAPER: "おむつ",
    RecordType.TEMP: "体温",
}


@router.get("/admin/records", response_class=HTMLResponse)
async def admin_records(
    request: Request,
    token: str | None = None,
    days: int = 7,
    type: str | None = None,
    saved: str | None = None,
    deleted: str | None = None,
):
    _check_token(token)
    conn, child_id = _get_conn_and_child()
    days = max(1, min(days, 365))
    type_filter = type if type in set(RecordType) else None

    now = datetime.now(JST)
    start = (now - timedelta(days=days)).isoformat()
    end = (now + timedelta(days=1)).isoformat()
    records = crud.query_records(conn, child_id=child_id, start=start, end=end, type=type_filter)
    rows = [dict(r) for r in records]
    for r in rows:
        r["type_label"] = _TYPE_LABELS.get(r["type"], r["type"])
        r["started_at_disp"] = _to_input_value(r.get("started_at")).replace("T", " ")
        r["ended_at_disp"] = _to_input_value(r.get("ended_at")).replace("T", " ")
    rows.sort(key=lambda r: r.get("started_at") or "", reverse=True)

    return _templates.TemplateResponse(
        request,
        "admin_records.html",
        {
            "records": rows,
            "token": token or "",
            "days": days,
            "type": type_filter or "",
            "type_labels": _TYPE_LABELS,
            "saved": saved == "1",
            "deleted": deleted == "1",
        },
    )


@router.get("/admin/records/new", response_class=HTMLResponse)
async def admin_record_new(request: Request, token: str | None = None):
    _check_token(token)
    return _templates.TemplateResponse(
        request,
        "admin_record_form.html",
        {
            "token": token or "",
            "record": None,
            "action": f"/admin/records?token={token or ''}",
            "title": "記録を追加",
            "type_labels": _TYPE_LABELS,
        },
    )


@router.post("/admin/records")
async def admin_record_create(
    token: str | None = None,
    type: str = Form(...),
    sub_type: str = Form(default=""),
    amount: str = Form(default=""),
    unit: str = Form(default="ml"),
    started_at: str = Form(...),
    ended_at: str = Form(default=""),
    note: str = Form(default=""),
):
    _check_token(token)
    if type not in set(RecordType):
        raise HTTPException(status_code=400, detail="Invalid type")
    conn, child_id = _get_conn_and_child()
    start_iso = _to_iso_jst(started_at)
    end_iso = _to_iso_jst(ended_at) if ended_at.strip() else None
    if end_iso and end_iso < start_iso:
        raise HTTPException(status_code=400, detail="ended_at cannot be before started_at")
    crud.insert_record(
        conn,
        child_id=child_id,
        type=type,
        started_at=start_iso,
        sub_type=normalize_sub_type(type, sub_type),
        amount=_parse_amount(amount),
        unit=unit.strip() or None,
        ended_at=end_iso,
        note=note.strip() or None,
    )
    return RedirectResponse(f"/admin/records?token={token or ''}&saved=1", status_code=303)


@router.get("/admin/records/{record_id}/edit", response_class=HTMLResponse)
async def admin_record_edit(request: Request, record_id: int, token: str | None = None):
    _check_token(token)
    conn, _ = _get_conn_and_child()
    rec = crud.get_record(conn, record_id)
    if rec is None:
        raise HTTPException(status_code=404, detail="Record not found")
    r = dict(rec)
    r["started_at_input"] = _to_input_value(r.get("started_at"))
    r["ended_at_input"] = _to_input_value(r.get("ended_at"))
    return _templates.TemplateResponse(
        request,
        "admin_record_form.html",
        {
            "token": token or "",
            "record": r,
            "action": f"/admin/records/{record_id}?token={token or ''}",
            "title": "記録を編集",
            "type_labels": _TYPE_LABELS,
        },
    )


@router.post("/admin/records/{record_id}")
async def admin_record_update(
    record_id: int,
    token: str | None = None,
    type: str = Form(...),
    sub_type: str = Form(default=""),
    amount: str = Form(default=""),
    unit: str = Form(default="ml"),
    started_at: str = Form(...),
    ended_at: str = Form(default=""),
    note: str = Form(default=""),
):
    _check_token(token)
    if type not in set(RecordType):
        raise HTTPException(status_code=400, detail="Invalid type")
    conn, _ = _get_conn_and_child()
    start_iso = _to_iso_jst(started_at)
    end_iso = _to_iso_jst(ended_at) if ended_at.strip() else None
    if end_iso and end_iso < start_iso:
        raise HTTPException(status_code=400, detail="ended_at cannot be before started_at")
    crud.update_record(
        conn,
        record_id,
        {
            "type": type,
            "sub_type": normalize_sub_type(type, sub_type),
            "amount": _parse_amount(amount),
            "unit": unit.strip() or None,
            "started_at": start_iso,
            "ended_at": end_iso,
            "note": note.strip() or None,
        },
    )
    return RedirectResponse(f"/admin/records?token={token or ''}&saved=1", status_code=303)


@router.post("/admin/records/{record_id}/delete")
async def admin_record_delete(record_id: int, token: str | None = None):
    _check_token(token)
    conn, _ = _get_conn_and_child()
    crud.delete_record(conn, record_id)
    return RedirectResponse(f"/admin/records?token={token or ''}&deleted=1", status_code=303)
