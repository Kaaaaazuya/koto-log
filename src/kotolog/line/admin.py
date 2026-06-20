"""管理画面（設定の確認・編集・テスト送信）。"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from kotolog.db import crud

router = APIRouter()
_templates = Jinja2Templates(directory=str(Path(__file__).parent.parent / "templates"))


def _check_token(token: str | None) -> None:
    expected = os.environ.get("KOTOLOG_DASHBOARD_TOKEN", "")
    if expected and token != expected:
        raise HTTPException(status_code=403, detail="Invalid token")


def _get_conn():
    from kotolog.line.webhook import _get_agent

    return _get_agent().executor.conn


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
