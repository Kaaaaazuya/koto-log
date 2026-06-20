"""LINE Push API クライアント。

Push API は replyToken なしにユーザーへ能動的にメッセージを送る。
"""

from __future__ import annotations

import httpx

LINE_PUSH_URL = "https://api.line.me/v2/bot/message/push"


def send_push(user_id: str, text: str, access_token: str) -> None:
    """LINE Push API にテキストメッセージを送信する。"""
    with httpx.Client() as client:
        client.post(
            LINE_PUSH_URL,
            headers={"Authorization": f"Bearer {access_token}"},
            json={
                "to": user_id,
                "messages": [{"type": "text", "text": text}],
            },
            timeout=10,
        )
