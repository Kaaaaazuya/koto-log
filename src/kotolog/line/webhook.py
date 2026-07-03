"""LINE Messaging API Webhook（T2.1〜T2.3）。

POST /webhook でテキストイベントを受け取り、Agent で処理して Reply API で返信する。
署名検証(HMAC-SHA256)・冪等化(processed_events)・1秒即ACK を担う。
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import os
import re
import secrets
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import BackgroundTasks, FastAPI, Header, HTTPException, Request
from starlette.middleware.sessions import SessionMiddleware

from kotolog.db import crud
from kotolog.line.admin import router as admin_router
from kotolog.line.dashboard import router as dashboard_router

load_dotenv()


@asynccontextmanager
async def lifespan(app: FastAPI):
    import logging

    from kotolog.line.scheduler import start_scheduler

    logger = logging.getLogger(__name__)

    # Issue #31: Verify required environment variables for LINE webhook
    line_channel_secret = os.environ.get("LINE_CHANNEL_SECRET", "").strip()
    line_channel_access_token = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN", "").strip()

    if not line_channel_secret or not line_channel_access_token:
        missing_vars = []
        if not line_channel_secret:
            missing_vars.append("LINE_CHANNEL_SECRET")
        if not line_channel_access_token:
            missing_vars.append("LINE_CHANNEL_ACCESS_TOKEN")
        logger.warning(
            f"LINE webhook is disabled: missing required environment variables: {', '.join(missing_vars)}. "
            "Set these variables to enable LINE message handling."
        )
        yield
        return

    scheduler = start_scheduler()
    try:
        yield
    finally:
        scheduler.shutdown()


app = FastAPI(lifespan=lifespan)

# Session middleware for cookie-based authentication (Issue #28)
# Use a strong secret key from environment or generate a random one
_SESSION_SECRET = os.environ.get("SESSION_SECRET_KEY")
if not _SESSION_SECRET:
    import logging

    logger = logging.getLogger(__name__)
    logger.warning(
        "SESSION_SECRET_KEY not set; generating random key. "
        "Sessions will be lost on server restart. Set SESSION_SECRET_KEY in production."
    )
    _SESSION_SECRET = secrets.token_urlsafe(32)
_SESSION_COOKIE_SECURE = os.environ.get("SESSION_COOKIE_SECURE", "true").lower() == "true"
app.add_middleware(
    SessionMiddleware,
    secret_key=_SESSION_SECRET,
    session_cookie="kotolog_session",
    max_age=86400 * 7,  # 7 days
    path="/admin",
    https_only=_SESSION_COOKIE_SECURE,
    same_site="strict",
)

app.include_router(dashboard_router)
app.include_router(admin_router)

_HELP_COMMANDS = {"操作一覧", "help", "ヘルプ", "?", "？"}

_HELP_TEXT = """\
操作一覧

授乳: 「母乳」「ミルク 120ml」「搾母乳 80ml」
おむつ: 「うんち」「おしっこ」「両方」
睡眠: 「寝た」「起きた」「寝た〜起きた 7時から8時」
体温: 「熱 37.2」
離乳食: 「離乳食 50g」「おかゆ食べた」
お風呂: 「お風呂入れた」
薬: 「薬飲んだ」「ビオフェルミン 0.5g」
病院: 「病院行った」「小児科受診」
外出: 「外出した」「公園に行った」
身長: 「身長 60cm」「60センチになった」
体重: 「体重 5.2kg」「5.2キロ」
確認: 「今日のまとめ」「前回の授乳は？」
修正: 「さっきのなし」「150に直して」\
"""


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}


# テスト時は monkeypatch でこの変数を差し替える
_agent = None


def _get_agent():
    global _agent
    if _agent is None:
        from kotolog.cli import build_agent

        _agent = build_agent()
    return _agent


def _verify_signature(body: bytes, signature: str, channel_secret: str) -> bool:
    """X-Line-Signature の HMAC-SHA256 を検証する。"""
    if not signature:
        return False
    h = hmac.new(channel_secret.encode(), body, hashlib.sha256).digest()
    return hmac.compare_digest(base64.b64encode(h).decode(), signature)


@app.post("/webhook")
async def webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    x_line_signature: str = Header(None),
):
    """署名検証→即 200 OK、テキストイベントはバックグラウンドで処理。

    Issue #31: LINE_CHANNEL_SECRET と LINE_CHANNEL_ACCESS_TOKEN を必須環境変数として厳密に検証。
    """
    body = await request.body()
    channel_secret = os.environ.get("LINE_CHANNEL_SECRET", "").strip()
    access_token = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN", "").strip()
    if not channel_secret or not access_token:
        raise HTTPException(status_code=503, detail="LINE webhook not configured")
    if not _verify_signature(body, x_line_signature or "", channel_secret):
        raise HTTPException(status_code=401, detail="Invalid signature")

    data = json.loads(body)
    for event in data.get("events", []):
        if event.get("type") == "message" and event.get("message", {}).get("type") == "text":
            background_tasks.add_task(_handle_text_event, event)

    return {"ok": True}


_SWITCH_RE = re.compile(r"^(.+?)に切り替え(?:て)?[。、]?$")


def _try_switch_child(conn, user_id: str | None, text: str) -> str | None:
    """「〇〇に切り替え[て]」コマンドを処理して返信文を返す。非該当なら None。"""
    if not user_id:
        return None
    m = _SWITCH_RE.match(text.strip())
    if not m:
        return None
    name = m.group(1)
    row = conn.execute("SELECT id FROM children WHERE name_alias = ?", (name,)).fetchone()
    if row is None:
        return f"「{name}」という子は登録されていません。"
    crud.set_user_current_child(conn, user_id, row["id"])
    return f"対象児を「{name}」に切り替えました。"


async def _handle_text_event(event: dict) -> None:
    """冪等チェック → Agent 処理 → Reply API。"""
    import traceback

    from kotolog.line import reply as reply_mod

    try:
        event_id = event.get("webhookEventId", event.get("message", {}).get("id", ""))
        agent = _get_agent()
        conn = agent.conn

        if crud.is_processed(conn, event_id):
            return
        crud.mark_processed(conn, event_id)

        user_id = event.get("source", {}).get("userId", "") or None
        if user_id:
            crud.upsert_user(conn, user_id)
            # Issue #29: Check if user is approved
            if not crud.is_user_approved(conn, user_id):
                reply_text = (
                    "ご登録ありがとうございます。\n"
                    "システム管理者による承認後にご利用いただけます。\n"
                    "お手数ですがお待ちください。"
                )
                reply_token = event.get("replyToken", "")
                access_token = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN", "")
                await asyncio.to_thread(reply_mod.send_reply, reply_token, reply_text, access_token)
                crud.mark_processed(conn, event_id)
                return

        text = event["message"]["text"]
        if text.strip() in _HELP_COMMANDS:
            reply_text = _HELP_TEXT
        elif switch_reply := await asyncio.to_thread(_try_switch_child, conn, user_id, text):
            reply_text = switch_reply
        else:
            reply_text = await asyncio.to_thread(agent.handle, text, user_id)

        reply_token = event.get("replyToken", "")
        access_token = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN", "")
        await asyncio.to_thread(reply_mod.send_reply, reply_token, reply_text, access_token)
    except Exception:
        traceback.print_exc()
