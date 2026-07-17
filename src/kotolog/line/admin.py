"""管理画面（設定の確認・編集・テスト送信）。"""

from __future__ import annotations

import asyncio
import os
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from kotolog.db import crud
from kotolog.line.csrf import check_csrf_token, get_or_create_csrf_token
from kotolog.types import RECORD_TYPE_LABELS, RecordType
from kotolog.utils.subtype import normalize_sub_type

router = APIRouter()
_templates = Jinja2Templates(directory=str(Path(__file__).parent.parent / "templates"))

JST = timezone(timedelta(hours=9))


def _redirect_if_unauth(request: Request):
    """GET ページ用: 未認証なら /admin/login へ 303 リダイレクト。認証済みなら None。"""
    if not request.session.get("authenticated"):
        return RedirectResponse("/admin/login", status_code=303)
    return None


def _require_auth(request: Request) -> None:
    """POST アクション用: 未認証なら 403。"""
    if not request.session.get("authenticated"):
        raise HTTPException(status_code=403, detail="Not authenticated")


def _get_conn():
    from kotolog.line.webhook import _get_agent

    return _get_agent().conn


def _get_conn_and_child():
    from kotolog.line.webhook import _get_agent

    conn = _get_agent().conn
    child_id = crud.get_default_child_id(conn)
    return conn, child_id


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


# --- Issue #28: Cookie-based session authentication ----------------------------


@router.get("/admin/login", response_class=HTMLResponse)
async def admin_login(request: Request):
    """Display login form."""
    return _templates.TemplateResponse(
        request,
        "admin_login.html",
        {},
    )


@router.post("/admin/login")
async def admin_login_post(
    request: Request,
    token: str = Form(default=""),
):
    """Validate token and set session cookie.

    Issue #28: Accept token from form, validate it, and set session cookie.
    """
    expected = os.environ.get("KOTOLOG_DASHBOARD_TOKEN", "")

    # Default-deny: reject unless token is explicitly configured and matches
    if not expected or not token or not secrets.compare_digest(token, expected):
        raise HTTPException(status_code=403, detail="Invalid token")

    # Set session cookie
    request.session["authenticated"] = True
    response = RedirectResponse(url="/admin", status_code=303)
    return response


@router.post("/admin/logout")
async def admin_logout(request: Request):
    """Clear session cookie and redirect to login."""
    request.session.clear()
    response = RedirectResponse(url="/admin/login", status_code=303)
    return response


@router.get("/admin", response_class=HTMLResponse)
async def admin_page(
    request: Request,
    saved: str | None = None,
    sent: str | None = None,
):
    if (redir := _redirect_if_unauth(request)) is not None:
        return redir
    conn = _get_conn()
    csrf_token = get_or_create_csrf_token(request)
    return _templates.TemplateResponse(
        request,
        "admin.html",
        {
            "due_date": crud.get_setting(conn, "due_date") or "",
            "line_user_id": crud.get_setting(conn, "line_user_id") or "",
            "csrf_token": csrf_token,
            "saved": saved == "1",
            "sent": sent == "1",
        },
    )


@router.post("/admin", response_class=HTMLResponse)
async def admin_save(
    request: Request,
    due_date: str = Form(default=""),
    line_user_id: str = Form(default=""),
):
    _require_auth(request)
    # Issue #32: Validate CSRF token (get from form data)
    form_data = await request.form()
    check_csrf_token(request, dict(form_data))
    conn = _get_conn()
    if due_date.strip():
        crud.set_setting(conn, "due_date", due_date.strip())
    if line_user_id.strip():
        crud.set_setting(conn, "line_user_id", line_user_id.strip())
    return RedirectResponse("/admin?saved=1", status_code=303)


@router.post("/admin/test-push")
async def admin_test_push(request: Request):
    _require_auth(request)
    # Issue #32: Validate CSRF token (get from form data)
    form_data = await request.form()
    check_csrf_token(request, dict(form_data))
    from kotolog.line.scheduler import _run_morning_push

    await asyncio.to_thread(_run_morning_push)
    return RedirectResponse("/admin?sent=1", status_code=303)


# --- 記録 CRUD（AIなし手動編集 / ADR-0003） --------------------------------

_TYPE_LABELS = RECORD_TYPE_LABELS


@router.get("/admin/records", response_class=HTMLResponse)
async def admin_records(
    request: Request,
    days: int = 7,
    type: str | None = None,
    saved: str | None = None,
    deleted: str | None = None,
):
    if (redir := _redirect_if_unauth(request)) is not None:
        return redir
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

    csrf_token = get_or_create_csrf_token(request)
    return _templates.TemplateResponse(
        request,
        "admin_records.html",
        {
            "records": rows,
            "csrf_token": csrf_token,
            "days": days,
            "type": type_filter or "",
            "type_labels": _TYPE_LABELS,
            "saved": saved == "1",
            "deleted": deleted == "1",
        },
    )


@router.get("/admin/records/new", response_class=HTMLResponse)
async def admin_record_new(request: Request):
    if (redir := _redirect_if_unauth(request)) is not None:
        return redir
    csrf_token = get_or_create_csrf_token(request)
    return _templates.TemplateResponse(
        request,
        "admin_record_form.html",
        {
            "csrf_token": csrf_token,
            "record": None,
            "action": "/admin/records",
            "title": "記録を追加",
            "type_labels": _TYPE_LABELS,
        },
    )


@router.post("/admin/records")
async def admin_record_create(
    request: Request,
    type: str = Form(...),
    sub_type: str = Form(default=""),
    amount: str = Form(default=""),
    unit: str = Form(default="ml"),
    started_at: str = Form(...),
    ended_at: str = Form(default=""),
    note: str = Form(default=""),
):
    _require_auth(request)
    # Issue #32: Validate CSRF token (get from form data)
    form_data = await request.form()
    check_csrf_token(request, dict(form_data))
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
    return RedirectResponse("/admin/records?saved=1", status_code=303)


@router.get("/admin/records/{record_id}/edit", response_class=HTMLResponse)
async def admin_record_edit(request: Request, record_id: int):
    if (redir := _redirect_if_unauth(request)) is not None:
        return redir
    conn, _ = _get_conn_and_child()
    rec = crud.get_record(conn, record_id)
    if rec is None:
        raise HTTPException(status_code=404, detail="Record not found")
    r = dict(rec)
    r["started_at_input"] = _to_input_value(r.get("started_at"))
    r["ended_at_input"] = _to_input_value(r.get("ended_at"))
    csrf_token = get_or_create_csrf_token(request)
    return _templates.TemplateResponse(
        request,
        "admin_record_form.html",
        {
            "csrf_token": csrf_token,
            "record": r,
            "action": f"/admin/records/{record_id}",
            "title": "記録を編集",
            "type_labels": _TYPE_LABELS,
        },
    )


@router.post("/admin/records/{record_id}")
async def admin_record_update(
    request: Request,
    record_id: int,
    type: str = Form(...),
    sub_type: str = Form(default=""),
    amount: str = Form(default=""),
    unit: str = Form(default="ml"),
    started_at: str = Form(...),
    ended_at: str = Form(default=""),
    note: str = Form(default=""),
):
    _require_auth(request)
    # Issue #32: Validate CSRF token (get from form data)
    form_data = await request.form()
    check_csrf_token(request, dict(form_data))
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
    return RedirectResponse("/admin/records?saved=1", status_code=303)


@router.post("/admin/records/{record_id}/delete")
async def admin_record_delete(request: Request, record_id: int, csrf_token: str = Form(default="")):
    _require_auth(request)
    # Issue #32: Validate CSRF token
    check_csrf_token(request, {"csrf_token": csrf_token})
    conn, _ = _get_conn_and_child()
    crud.delete_record(conn, record_id)
    return RedirectResponse("/admin/records?deleted=1", status_code=303)


# --- T9.3.2: ユーザー管理 ----------------------------------------------------


@router.get("/admin/users", response_class=HTMLResponse)
async def admin_users(request: Request, saved: bool = False, deleted: bool = False):
    if (redir := _redirect_if_unauth(request)) is not None:
        return redir
    conn = _get_conn()
    users = [dict(u) for u in crud.list_users(conn)]
    children = [dict(c) for c in crud.list_children(conn)]
    csrf_token = get_or_create_csrf_token(request)
    return _templates.TemplateResponse(
        request,
        "admin_users.html",
        {
            "users": users,
            "children": children,
            "csrf_token": csrf_token,
            "saved": saved,
            "deleted": deleted,
        },
    )


@router.post("/admin/users/{line_user_id}/nickname")
async def admin_user_nickname(
    request: Request,
    line_user_id: str,
    nickname: str = Form(""),
    csrf_token: str = Form(default=""),
):
    _require_auth(request)
    # Issue #32: Validate CSRF token
    check_csrf_token(request, {"csrf_token": csrf_token})
    conn = _get_conn()
    crud.set_user_nickname(conn, line_user_id, nickname.strip() or None)
    return RedirectResponse("/admin/users?saved=1", status_code=303)


@router.post("/admin/users/{line_user_id}/notify")
async def admin_user_notify(
    request: Request,
    line_user_id: str,
    enabled: str = Form("1"),
    csrf_token: str = Form(default=""),
):
    _require_auth(request)
    # Issue #32: Validate CSRF token
    check_csrf_token(request, {"csrf_token": csrf_token})
    conn = _get_conn()
    crud.update_user_notify(conn, line_user_id, notify_enabled=(enabled == "1"))
    return RedirectResponse("/admin/users?saved=1", status_code=303)


@router.post("/admin/users/{line_user_id}/child")
async def admin_user_child(
    request: Request,
    line_user_id: str,
    child_id: str = Form(""),
    csrf_token: str = Form(default=""),
):
    _require_auth(request)
    # Issue #32: Validate CSRF token
    check_csrf_token(request, {"csrf_token": csrf_token})
    conn = _get_conn()
    cid = int(child_id) if child_id.strip() else None
    crud.set_user_current_child(conn, line_user_id, cid)
    return RedirectResponse("/admin/users?saved=1", status_code=303)


@router.post("/admin/users/{line_user_id}/delete")
async def admin_user_delete(request: Request, line_user_id: str, csrf_token: str = Form(default="")):
    _require_auth(request)
    # Issue #32: Validate CSRF token
    check_csrf_token(request, {"csrf_token": csrf_token})
    conn = _get_conn()
    crud.delete_user(conn, line_user_id)
    return RedirectResponse("/admin/users?deleted=1", status_code=303)


# --- Issue #29: ユーザー承認フロー -------------------------------------------


@router.get("/admin/approvals", response_class=HTMLResponse)
async def admin_approvals(request: Request, approved: bool = False):
    """未承認ユーザーの一覧ページ。"""
    if (redir := _redirect_if_unauth(request)) is not None:
        return redir
    conn = _get_conn()
    pending_users = [dict(u) for u in crud.list_pending_approvals(conn)]
    csrf_token = get_or_create_csrf_token(request)
    return _templates.TemplateResponse(
        request,
        "admin_approvals.html",
        {
            "pending_users": pending_users,
            "csrf_token": csrf_token,
            "approved": approved,
        },
    )


@router.post("/admin/approvals/{line_user_id}/approve")
async def admin_approve_user(request: Request, line_user_id: str):
    """ユーザーを承認する。"""
    _require_auth(request)
    # Issue #32: Validate CSRF token (get from form data)
    form_data = await request.form()
    check_csrf_token(request, dict(form_data))
    conn = _get_conn()
    crud.approve_user(conn, line_user_id)
    return RedirectResponse("/admin/approvals?approved=1", status_code=303)


@router.post("/admin/approvals/{line_user_id}/reject")
async def admin_reject_user(request: Request, line_user_id: str):
    """ユーザーを却下（削除）する。"""
    _require_auth(request)
    # Issue #32: Validate CSRF token (get from form data)
    form_data = await request.form()
    check_csrf_token(request, dict(form_data))
    conn = _get_conn()
    crud.reject_user(conn, line_user_id)
    return RedirectResponse("/admin/approvals", status_code=303)
