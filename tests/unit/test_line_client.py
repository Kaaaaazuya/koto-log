"""_line_client.py の単体テスト（Issue #102）。

HTTP 状態確認・リトライ・エラーログをテスト。
PII 安全性、payload 形式、retry ロジックを検証。
"""

from __future__ import annotations

import json
import logging
from unittest.mock import MagicMock, call, patch

import httpx
import pytest

from kotolog.line._line_client import LineApiError, post_line_message

# ---------------------------------------------------------------------------
# ヘルパー: httpx.Client の mock
# ---------------------------------------------------------------------------


def _mock_response(status_code: int):
    """指定ステータスコードの httpx.Response モック。"""
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status_code

    def raise_for_status():
        if not (200 <= status_code < 300):
            raise httpx.HTTPStatusError(f"HTTP {status_code}", request=MagicMock(), response=resp)

    resp.raise_for_status = raise_for_status
    return resp


# ---------------------------------------------------------------------------
# 成功パス
# ---------------------------------------------------------------------------


def test_post_line_message_success_2xx():
    """2xx 応答で正常に終了。1 度の POST、エラーログなし。"""
    with patch("kotolog.line._line_client.httpx.Client") as mock_client_cls:
        mock_client = MagicMock()
        mock_client_cls.return_value.__enter__.return_value = mock_client
        mock_client.post.return_value = _mock_response(200)

        payload = {"messages": [{"type": "text", "text": "test"}]}
        post_line_message("https://api.line.me/v2/bot/message/reply", payload, "token", event="reply")

        assert mock_client.post.call_count == 1


def test_post_line_message_success_201():
    """2xx (201) でも正常に終了。"""
    with patch("kotolog.line._line_client.httpx.Client") as mock_client_cls:
        mock_client = MagicMock()
        mock_client_cls.return_value.__enter__.return_value = mock_client
        mock_client.post.return_value = _mock_response(201)

        payload = {"messages": [{"type": "text", "text": "test"}]}
        post_line_message("https://api.line.me/v2/bot/message/reply", payload, "token", event="reply")

        assert mock_client.post.call_count == 1


def test_post_line_message_success_no_error_log(caplog):
    """2xx 成功時はエラーログが出ない。"""
    with patch("kotolog.line._line_client.httpx.Client") as mock_client_cls:
        mock_client = MagicMock()
        mock_client_cls.return_value.__enter__.return_value = mock_client
        mock_client.post.return_value = _mock_response(200)

        with caplog.at_level(logging.ERROR, logger="kotolog.line"):
            payload = {"messages": []}
            post_line_message("https://api.line.me/v2/bot/message/reply", payload, "token", event="reply")

        error_logs = [record for record in caplog.records if record.levelname == "ERROR"]
        assert len(error_logs) == 0


# ---------------------------------------------------------------------------
# リトライ可能エラー: 5xx
# ---------------------------------------------------------------------------


def test_post_line_message_500_then_200_retries():
    """500 → 200: リトライして成功。2 度の POST。"""
    with patch("kotolog.line._line_client.httpx.Client") as mock_client_cls:
        mock_client = MagicMock()
        mock_client_cls.return_value.__enter__.return_value = mock_client
        mock_client.post.side_effect = [_mock_response(500), _mock_response(200)]

        with patch("kotolog.line._line_client.time.sleep"):
            payload = {"messages": []}
            post_line_message("https://api.line.me/v2/bot/message/reply", payload, "token", event="reply")

        assert mock_client.post.call_count == 2


def test_post_line_message_persistent_500_raises_and_logs(caplog):
    """3 度の 500: 3 度試行後に例外。エラーログに status=500, attempts=3。"""
    with patch("kotolog.line._line_client.httpx.Client") as mock_client_cls:
        mock_client = MagicMock()
        mock_client_cls.return_value.__enter__.return_value = mock_client
        mock_client.post.return_value = _mock_response(500)

        with patch("kotolog.line._line_client.time.sleep"), pytest.raises(httpx.HTTPStatusError):
            with caplog.at_level(logging.ERROR, logger="kotolog.line"):
                payload = {"messages": []}
                post_line_message("https://api.line.me/v2/bot/message/reply", payload, "token", event="reply")

        assert mock_client.post.call_count == 3

        error_logs = [record for record in caplog.records if "line_api_send_failed" in record.message]
        assert len(error_logs) == 1
        log_data = json.loads(error_logs[0].message.split(" ", 1)[1])
        assert log_data["event"] == "reply"
        assert log_data["status"] == 500
        assert log_data["attempts"] == 3


def test_post_line_message_429_retryable():
    """429 (Too Many Requests): リトライ対象。"""
    with patch("kotolog.line._line_client.httpx.Client") as mock_client_cls:
        mock_client = MagicMock()
        mock_client_cls.return_value.__enter__.return_value = mock_client
        mock_client.post.side_effect = [_mock_response(429), _mock_response(200)]

        with patch("kotolog.line._line_client.time.sleep"):
            payload = {"messages": []}
            post_line_message("https://api.line.me/v2/bot/message/reply", payload, "token", event="reply")

        assert mock_client.post.call_count == 2


# ---------------------------------------------------------------------------
# 非リトライ可能エラー: 4xx（400, 401）
# ---------------------------------------------------------------------------


def test_post_line_message_400_no_retry():
    """400: リトライしない。1 度のみ POST、即座に例外。"""
    with patch("kotolog.line._line_client.httpx.Client") as mock_client_cls:
        mock_client = MagicMock()
        mock_client_cls.return_value.__enter__.return_value = mock_client
        mock_client.post.return_value = _mock_response(400)

        with patch("kotolog.line._line_client.time.sleep"), pytest.raises(httpx.HTTPStatusError):
            payload = {"messages": []}
            post_line_message("https://api.line.me/v2/bot/message/reply", payload, "token", event="reply")

        assert mock_client.post.call_count == 1


def test_post_line_message_400_logs_immediately(caplog):
    """400: エラーログが即座に記録される。"""
    with patch("kotolog.line._line_client.httpx.Client") as mock_client_cls:
        mock_client = MagicMock()
        mock_client_cls.return_value.__enter__.return_value = mock_client
        mock_client.post.return_value = _mock_response(400)

        with patch("kotolog.line._line_client.time.sleep"), pytest.raises(httpx.HTTPStatusError):
            with caplog.at_level(logging.ERROR, logger="kotolog.line"):
                payload = {"messages": []}
                post_line_message("https://api.line.me/v2/bot/message/reply", payload, "token", event="reply")

        error_logs = [record for record in caplog.records if "line_api_send_failed" in record.message]
        assert len(error_logs) == 1
        log_data = json.loads(error_logs[0].message.split(" ", 1)[1])
        assert log_data["status"] == 400
        assert log_data["attempts"] == 1


def test_post_line_message_401_no_retry(caplog):
    """401 (Unauthorized): 非リトライ可能、1 度のみ POST。"""
    with patch("kotolog.line._line_client.httpx.Client") as mock_client_cls:
        mock_client = MagicMock()
        mock_client_cls.return_value.__enter__.return_value = mock_client
        mock_client.post.return_value = _mock_response(401)

        with patch("kotolog.line._line_client.time.sleep"), pytest.raises(httpx.HTTPStatusError):
            with caplog.at_level(logging.ERROR, logger="kotolog.line"):
                payload = {"messages": []}
                post_line_message("https://api.line.me/v2/bot/message/reply", payload, "token", event="reply")

        assert mock_client.post.call_count == 1
        error_logs = [record for record in caplog.records if "line_api_send_failed" in record.message]
        assert len(error_logs) == 1


# ---------------------------------------------------------------------------
# ネットワークエラー
# ---------------------------------------------------------------------------


def test_post_line_message_timeout_retryable():
    """TimeoutException: リトライ対象。"""
    with patch("kotolog.line._line_client.httpx.Client") as mock_client_cls:
        mock_client = MagicMock()
        mock_client_cls.return_value.__enter__.return_value = mock_client
        mock_client.post.side_effect = [
            httpx.TimeoutException("Timeout"),
            _mock_response(200),
        ]

        with patch("kotolog.line._line_client.time.sleep"):
            payload = {"messages": []}
            post_line_message("https://api.line.me/v2/bot/message/reply", payload, "token", event="reply")

        assert mock_client.post.call_count == 2


def test_post_line_message_timeout_exhausted_raises_and_logs(caplog):
    """TimeoutException が max_attempts 回続く: 例外。status=None。"""
    with patch("kotolog.line._line_client.httpx.Client") as mock_client_cls:
        mock_client = MagicMock()
        mock_client_cls.return_value.__enter__.return_value = mock_client
        mock_client.post.side_effect = httpx.TimeoutException("Timeout")

        with patch("kotolog.line._line_client.time.sleep"), pytest.raises(LineApiError):
            with caplog.at_level(logging.ERROR, logger="kotolog.line"):
                payload = {"messages": []}
                post_line_message("https://api.line.me/v2/bot/message/reply", payload, "token", event="reply")

        assert mock_client.post.call_count == 3
        error_logs = [record for record in caplog.records if "line_api_send_failed" in record.message]
        assert len(error_logs) == 1
        log_data = json.loads(error_logs[0].message.split(" ", 1)[1])
        assert log_data["status"] is None
        assert log_data["attempts"] == 3


def test_post_line_message_transport_error_retryable():
    """TransportError: リトライ対象。"""
    with patch("kotolog.line._line_client.httpx.Client") as mock_client_cls:
        mock_client = MagicMock()
        mock_client_cls.return_value.__enter__.return_value = mock_client
        mock_client.post.side_effect = [
            httpx.TransportError("Network error"),
            _mock_response(200),
        ]

        with patch("kotolog.line._line_client.time.sleep"):
            payload = {"messages": []}
            post_line_message("https://api.line.me/v2/bot/message/reply", payload, "token", event="reply")

        assert mock_client.post.call_count == 2


# ---------------------------------------------------------------------------
# リトライ間隔（sleep）
# ---------------------------------------------------------------------------


def test_post_line_message_sleep_between_retries():
    """リトライ間に time.sleep(0.5 * attempt) が呼ばれる。"""
    with patch("kotolog.line._line_client.httpx.Client") as mock_client_cls:
        mock_client = MagicMock()
        mock_client_cls.return_value.__enter__.return_value = mock_client
        mock_client.post.side_effect = [_mock_response(500), _mock_response(500), _mock_response(200)]

        with patch("kotolog.line._line_client.time.sleep") as mock_sleep:
            payload = {"messages": []}
            post_line_message("https://api.line.me/v2/bot/message/reply", payload, "token", event="reply")

        # 1 回目失敗後 sleep(0.5 * 1 = 0.5)、2 回目失敗後 sleep(0.5 * 2 = 1.0)
        assert mock_sleep.call_count == 2
        calls = mock_sleep.call_args_list
        assert calls[0] == call(0.5)
        assert calls[1] == call(1.0)


# ---------------------------------------------------------------------------
# PII 安全性：ログに機密情報が含まれない
# ---------------------------------------------------------------------------


def test_post_line_message_pii_not_in_log_message_text(caplog):
    """失敗時のログに message text が含まれない。"""
    with patch("kotolog.line._line_client.httpx.Client") as mock_client_cls:
        mock_client = MagicMock()
        mock_client_cls.return_value.__enter__.return_value = mock_client
        mock_client.post.return_value = _mock_response(500)

        with patch("kotolog.line._line_client.time.sleep"), pytest.raises(httpx.HTTPStatusError):
            with caplog.at_level(logging.ERROR, logger="kotolog.line"):
                message_text = "ユーザーの個人情報が含まれるメッセージ"
                payload = {"messages": [{"type": "text", "text": message_text}]}
                post_line_message("https://api.line.me/v2/bot/message/reply", payload, "token", event="reply")

        error_logs = [record for record in caplog.records if "line_api_send_failed" in record.message]
        assert len(error_logs) == 1
        log_text = error_logs[0].message
        assert message_text not in log_text


def test_post_line_message_pii_not_in_log_reply_token(caplog):
    """失敗時のログに replyToken が含まれない。"""
    with patch("kotolog.line._line_client.httpx.Client") as mock_client_cls:
        mock_client = MagicMock()
        mock_client_cls.return_value.__enter__.return_value = mock_client
        mock_client.post.return_value = _mock_response(500)

        with patch("kotolog.line._line_client.time.sleep"), pytest.raises(httpx.HTTPStatusError):
            with caplog.at_level(logging.ERROR, logger="kotolog.line"):
                reply_token = "nHuyWiB7yP5Ym5N2Eq7GQ7GmGm1234567890"
                payload = {"replyToken": reply_token, "messages": []}
                post_line_message("https://api.line.me/v2/bot/message/reply", payload, "token", event="reply")

        error_logs = [record for record in caplog.records if "line_api_send_failed" in record.message]
        assert len(error_logs) == 1
        log_text = error_logs[0].message
        assert reply_token not in log_text


def test_post_line_message_pii_not_in_log_user_id(caplog):
    """失敗時のログに user_id が含まれない。"""
    with patch("kotolog.line._line_client.httpx.Client") as mock_client_cls:
        mock_client = MagicMock()
        mock_client_cls.return_value.__enter__.return_value = mock_client
        mock_client.post.return_value = _mock_response(500)

        with patch("kotolog.line._line_client.time.sleep"), pytest.raises(httpx.HTTPStatusError):
            with caplog.at_level(logging.ERROR, logger="kotolog.line"):
                user_id = "U1234567890abcdef1234567890abcdef"
                payload = {"to": user_id, "messages": []}
                post_line_message("https://api.line.me/v2/bot/message/push", payload, "token", event="push")

        error_logs = [record for record in caplog.records if "line_api_send_failed" in record.message]
        assert len(error_logs) == 1
        log_text = error_logs[0].message
        assert user_id not in log_text


def test_post_line_message_pii_not_in_log_access_token(caplog):
    """失敗時のログに access_token が含まれない。"""
    with patch("kotolog.line._line_client.httpx.Client") as mock_client_cls:
        mock_client = MagicMock()
        mock_client_cls.return_value.__enter__.return_value = mock_client
        mock_client.post.return_value = _mock_response(500)

        with patch("kotolog.line._line_client.time.sleep"), pytest.raises(httpx.HTTPStatusError):
            with caplog.at_level(logging.ERROR, logger="kotolog.line"):
                access_token = "dY1234567890abcdefghijklmnopqrstu"
                payload = {"messages": []}
                post_line_message(
                    "https://api.line.me/v2/bot/message/reply", payload, access_token, event="reply"
                )

        error_logs = [record for record in caplog.records if "line_api_send_failed" in record.message]
        assert len(error_logs) == 1
        log_text = error_logs[0].message
        assert access_token not in log_text


# ---------------------------------------------------------------------------
# send_reply / send_push の payload 正確性
# ---------------------------------------------------------------------------


def test_send_reply_correct_payload():
    """send_reply: 正しい payload で POST される。"""
    with patch("kotolog.line._line_client.httpx.Client") as mock_client_cls:
        mock_client = MagicMock()
        mock_client_cls.return_value.__enter__.return_value = mock_client
        mock_client.post.return_value = _mock_response(200)

        from kotolog.line.reply import send_reply

        reply_token = "test_reply_token"
        text = "返信テキスト"
        access_token = "test_access_token"

        send_reply(reply_token, text, access_token)

        mock_client.post.assert_called_once()
        call_kwargs = mock_client.post.call_args[1]
        assert call_kwargs["json"]["replyToken"] == reply_token
        assert call_kwargs["json"]["messages"][0]["type"] == "text"
        assert call_kwargs["json"]["messages"][0]["text"] == text
        assert call_kwargs["headers"]["Authorization"] == f"Bearer {access_token}"


def test_send_push_correct_payload():
    """send_push: 正しい payload で POST される。"""
    with patch("kotolog.line._line_client.httpx.Client") as mock_client_cls:
        mock_client = MagicMock()
        mock_client_cls.return_value.__enter__.return_value = mock_client
        mock_client.post.return_value = _mock_response(200)

        from kotolog.line.push import send_push

        user_id = "U1234567890"
        text = "プッシュメッセージ"
        access_token = "test_access_token"

        send_push(user_id, text, access_token)

        mock_client.post.assert_called_once()
        call_kwargs = mock_client.post.call_args[1]
        assert call_kwargs["json"]["to"] == user_id
        assert call_kwargs["json"]["messages"][0]["type"] == "text"
        assert call_kwargs["json"]["messages"][0]["text"] == text
        assert call_kwargs["headers"]["Authorization"] == f"Bearer {access_token}"


# ---------------------------------------------------------------------------
# カスタム max_attempts
# ---------------------------------------------------------------------------


def test_post_line_message_custom_max_attempts():
    """max_attempts=5 で 5 度まで試行。"""
    with patch("kotolog.line._line_client.httpx.Client") as mock_client_cls:
        mock_client = MagicMock()
        mock_client_cls.return_value.__enter__.return_value = mock_client
        mock_client.post.return_value = _mock_response(500)

        with patch("kotolog.line._line_client.time.sleep"), pytest.raises(httpx.HTTPStatusError):
            payload = {"messages": []}
            post_line_message(
                "https://api.line.me/v2/bot/message/reply",
                payload,
                "token",
                event="reply",
                max_attempts=5,
            )

        assert mock_client.post.call_count == 5


def test_post_line_message_custom_max_attempts_success_early(caplog):
    """max_attempts=5 でも 2 回目で成功したら早期リターン。"""
    with patch("kotolog.line._line_client.httpx.Client") as mock_client_cls:
        mock_client = MagicMock()
        mock_client_cls.return_value.__enter__.return_value = mock_client
        mock_client.post.side_effect = [_mock_response(500), _mock_response(200)]

        with patch("kotolog.line._line_client.time.sleep"):
            payload = {"messages": []}
            post_line_message(
                "https://api.line.me/v2/bot/message/reply",
                payload,
                "token",
                event="reply",
                max_attempts=5,
            )

        assert mock_client.post.call_count == 2
