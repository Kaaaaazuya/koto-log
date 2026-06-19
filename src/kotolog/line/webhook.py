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

from dotenv import load_dotenv
from fastapi import BackgroundTasks, FastAPI, Header, HTTPException, Request

from kotolog.db import crud

load_dotenv()

app = FastAPI()

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
    """署名検証→即 200 OK、テキストイベントはバックグラウンドで処理。"""
    body = await request.body()
    channel_secret = os.environ.get("LINE_CHANNEL_SECRET", "")
    if not _verify_signature(body, x_line_signature or "", channel_secret):
        raise HTTPException(status_code=401, detail="Invalid signature")

    data = json.loads(body)
    for event in data.get("events", []):
        if event.get("type") == "message" and event.get("message", {}).get("type") == "text":
            background_tasks.add_task(_handle_text_event, event)

    return {"ok": True}


async def _handle_text_event(event: dict) -> None:
    """冪等チェック → Agent 処理 → Reply API。"""
    import traceback

    from kotolog.line import reply as reply_mod

    try:
        event_id = event.get("webhookEventId", event.get("message", {}).get("id", ""))
        agent = _get_agent()
        conn = agent.executor.conn

        if crud.is_processed(conn, event_id):
            return
        crud.mark_processed(conn, event_id)

        text = event["message"]["text"]
        reply_text = await asyncio.to_thread(agent.handle, text)

        reply_token = event.get("replyToken", "")
        access_token = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN", "")
        await asyncio.to_thread(reply_mod.send_reply, reply_token, reply_text, access_token)
    except Exception:
        traceback.print_exc()
