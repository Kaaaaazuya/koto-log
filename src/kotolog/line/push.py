"""LINE Push API クライアント。

Push API は replyToken なしにユーザーへ能動的にメッセージを送る。
"""

from __future__ import annotations

from kotolog.line._line_client import post_line_message

LINE_PUSH_URL = "https://api.line.me/v2/bot/message/push"


def send_push(user_id: str, text: str, access_token: str) -> None:
    """LINE Push API にテキストメッセージを送信する。"""
    payload = {
        "to": user_id,
        "messages": [{"type": "text", "text": text}],
    }
    post_line_message(LINE_PUSH_URL, payload, access_token, event="push")
