"""LINE Reply API クライアント（T2.3）。

reply_token と返信テキストを受け取り、LINE Messaging API へ POST する。
テスト時は send_reply を monkeypatch で差し替える。
"""

from __future__ import annotations

from kotolog.line._line_client import post_line_message

LINE_REPLY_URL = "https://api.line.me/v2/bot/message/reply"


def send_reply(reply_token: str, text: str, access_token: str) -> None:
    """LINE Reply API にテキストメッセージを送信する。"""
    payload = {
        "replyToken": reply_token,
        "messages": [{"type": "text", "text": text}],
    }
    post_line_message(LINE_REPLY_URL, payload, access_token, event="reply")
