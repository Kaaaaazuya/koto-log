"""T2.2 + T2.3: LINE webhook の冪等化と Reply API 配線の結合テスト。

実 DB（in-memory）+ FakeLLM + Reply モック で、
「同一 event_id は1回のみ処理」「テキストで Agent → 返信」を検証する。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json

import pytest
from fastapi.testclient import TestClient

from kotolog.agent.loop import Agent
from kotolog.db import crud
from tests.conftest import NOW, FakeLLM, make_resp

CHANNEL_SECRET = "test_secret"
ACCESS_TOKEN = "test_access_token"


def _sign(body: bytes) -> str:
    h = hmac.new(CHANNEL_SECRET.encode(), body, hashlib.sha256).digest()
    return base64.b64encode(h).decode()


def _text_event(
    text: str,
    event_id: str = "evt_001",
    reply_token: str = "rtok_001",
    user_id: str = "U_test",
) -> bytes:
    return json.dumps(
        {
            "destination": "Uxxx",
            "events": [
                {
                    "type": "message",
                    "webhookEventId": event_id,
                    "replyToken": reply_token,
                    "source": {"type": "user", "userId": user_id},
                    "message": {"type": "text", "id": "msg_001", "text": text},
                }
            ],
        }
    ).encode()


# --- T2.2: 冪等化（crud 層） -------------------------------------------------


def test_is_processed_false_initially(conn):
    assert crud.is_processed(conn, "evt_abc") is False


def test_mark_processed_stores_event_id(conn):
    crud.mark_processed(conn, "evt_abc")
    assert crud.is_processed(conn, "evt_abc") is True


def test_mark_processed_idempotent(conn):
    crud.mark_processed(conn, "evt_abc")
    crud.mark_processed(conn, "evt_abc")  # 2回目はエラーにならない
    assert crud.is_processed(conn, "evt_abc") is True


# --- T2.3: webhook → Agent → reply の配線 -----------------------------------


@pytest.fixture()
def webhook_client(monkeypatch, conn, child_id):
    """in-memory DB + FakeLLM + Reply モックで組んだ TestClient。"""
    import kotolog.line.reply as reply_mod
    import kotolog.line.webhook as wh
    from kotolog.db import crud

    llm = FakeLLM([make_resp(content="記録しました。"), make_resp(content="記録しました。")])
    agent = Agent(client=llm, conn=conn, _now=lambda: NOW)

    sent: list[dict] = []

    def mock_send_reply(reply_token: str, text: str, access_token: str) -> None:
        sent.append({"reply_token": reply_token, "text": text})

    monkeypatch.setattr(wh, "_agent", agent)
    monkeypatch.setattr(reply_mod, "send_reply", mock_send_reply)
    monkeypatch.setenv("LINE_CHANNEL_SECRET", CHANNEL_SECRET)
    monkeypatch.setenv("LINE_CHANNEL_ACCESS_TOKEN", ACCESS_TOKEN)

    # Issue #29: Approve test user by default (tests create users, they should be approved)
    # Create and approve the test user used in tests
    crud.upsert_user(conn, "U_test")
    crud.approve_user(conn, "U_test")

    return TestClient(wh.app, raise_server_exceptions=True), sent


def test_text_event_triggers_reply(webhook_client):
    client, sent = webhook_client
    body = _text_event("こんにちは")
    resp = client.post("/webhook", content=body, headers={"X-Line-Signature": _sign(body)})
    assert resp.status_code == 200
    assert len(sent) == 1
    assert sent[0]["reply_token"] == "rtok_001"
    assert "記録しました" in sent[0]["text"]


def test_duplicate_event_is_processed_once(webhook_client):
    client, sent = webhook_client
    body = _text_event("こんにちは", event_id="evt_dup")

    # 1回目
    resp1 = client.post("/webhook", content=body, headers={"X-Line-Signature": _sign(body)})
    assert resp1.status_code == 200

    # 2回目（同一 event_id）
    resp2 = client.post("/webhook", content=body, headers={"X-Line-Signature": _sign(body)})
    assert resp2.status_code == 200

    # Reply は1回だけ
    assert len(sent) == 1


def test_non_text_event_is_ignored(webhook_client):
    client, sent = webhook_client
    body = json.dumps(
        {
            "destination": "Uxxx",
            "events": [{"type": "follow", "webhookEventId": "evt_follow"}],
        }
    ).encode()
    resp = client.post("/webhook", content=body, headers={"X-Line-Signature": _sign(body)})
    assert resp.status_code == 200
    assert len(sent) == 0


# --- T9.3.1: upsert_user 自動登録 -------------------------------------------


def test_text_event_upserts_user(monkeypatch, conn, child_id):
    """テキストイベント受信時に users テーブルへ自動登録される。"""
    import kotolog.line.reply as reply_mod
    import kotolog.line.webhook as wh
    from kotolog.db import crud as crud_mod

    llm = FakeLLM([make_resp(content="ok")])
    agent = Agent(client=llm, conn=conn, _now=lambda: NOW)

    monkeypatch.setattr(wh, "_agent", agent)
    monkeypatch.setattr(reply_mod, "send_reply", lambda *a: None)
    monkeypatch.setenv("LINE_CHANNEL_SECRET", CHANNEL_SECRET)
    monkeypatch.setenv("LINE_CHANNEL_ACCESS_TOKEN", ACCESS_TOKEN)

    client = TestClient(wh.app, raise_server_exceptions=True)
    body = _text_event("こんにちは", user_id="U_new")
    client.post("/webhook", content=body, headers={"X-Line-Signature": _sign(body)})

    users = crud_mod.list_users(conn)
    assert any(u["line_user_id"] == "U_new" for u in users)


# --- T9.3.3: 切り替えコマンド -----------------------------------------------


def test_switch_child_command_updates_current(monkeypatch, conn, child_id):
    """「〇〇に切り替え」でそのユーザーの current_child_id が更新される。"""
    import kotolog.line.reply as reply_mod
    import kotolog.line.webhook as wh
    from kotolog.db import crud as crud_mod

    hanako = crud_mod.create_child(conn, "はなこ")
    crud_mod.upsert_user(conn, "U001")
    # Issue #29: Approve user so they can use bot functionality
    crud_mod.approve_user(conn, "U001")

    llm = FakeLLM([])
    agent = Agent(client=llm, conn=conn, _now=lambda: NOW)
    monkeypatch.setattr(wh, "_agent", agent)
    monkeypatch.setattr(reply_mod, "send_reply", lambda *a: None)
    monkeypatch.setenv("LINE_CHANNEL_SECRET", CHANNEL_SECRET)
    monkeypatch.setenv("LINE_CHANNEL_ACCESS_TOKEN", ACCESS_TOKEN)

    client = TestClient(wh.app, raise_server_exceptions=True)
    body = _text_event("はなこに切り替え", user_id="U001")
    resp = client.post("/webhook", content=body, headers={"X-Line-Signature": _sign(body)})
    assert resp.status_code == 200

    row = conn.execute("SELECT current_child_id FROM users WHERE line_user_id = ?", ("U001",)).fetchone()
    assert row["current_child_id"] == hanako
