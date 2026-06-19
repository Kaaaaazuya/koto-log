"""LINE Reply API クライアント（T2.3）。

reply_token と返信テキストを受け取り、LINE Messaging API へ POST する。
テスト時は send_reply を monkeypatch で差し替える。
"""

from __future__ import annotations

import httpx

LINE_REPLY_URL = "https://api.line.me/v2/bot/message/reply"


def send_reply(reply_token: str, text: str, access_token: str) -> None:
    """LINE Reply API にテキストメッセージを送信する。"""
    with httpx.Client() as client:
        client.post(
            LINE_REPLY_URL,
            headers={"Authorization": f"Bearer {access_token}"},
            json={
                "replyToken": reply_token,
                "messages": [{"type": "text", "text": text}],
            },
            timeout=10,
        )
