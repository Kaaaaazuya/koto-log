"""LINE API クライアント共通実装（Issue #102）。

POST リクエストの HTTP 状態確認・リトライ・エラーログを一元管理。
reply.py・push.py はこの関数を使って，失敗時の観測可能性を担保する。
"""

from __future__ import annotations

import json
import logging
import time

import httpx

logger = logging.getLogger("kotolog.line")


class LineApiError(Exception):
    """LINE API 呼び出しの最終的な失敗を表す例外。"""

    pass


def post_line_message(
    url: str,
    payload: dict,
    access_token: str,
    *,
    event: str,
    max_attempts: int = 3,
) -> None:
    """LINE API へ POST し、HTTP 状態をチェック・リトライして最終ステータスをログ。

    Args:
        url: LINE API エンドポイント URL（REPLY_URL or PUSH_URL）。
        payload: POST ボディ JSON ペイロード（`messages` など）。
        access_token: LINE channel access token。
        event: イベント種別（"reply" or "push"）。エラーログに記録。
        max_attempts: 最大試行回数（デフォルト 3）。

    Raises:
        LineApiError: 最大試行回数後も失敗した場合、または非リトライ可能エラーの場合。

    Structured logging:
    - 成功時 (2xx): ログなし。
    - リトライ失敗時: logger.error() で `line_api_send_failed` と JSON フィールド。
    - 非リトライ可能エラー時: logger.error() で即座に記録・例外発生。
    """
    attempt = 0
    last_status = None

    while attempt < max_attempts:
        attempt += 1
        try:
            with httpx.Client() as client:
                response = client.post(
                    url,
                    headers={"Authorization": f"Bearer {access_token}"},
                    json=payload,
                    timeout=10,
                )
            last_status = response.status_code

            # 2xx: 成功、リターン
            if 200 <= response.status_code < 300:
                return

            # 429 (Too Many Requests): リトライ可能
            if response.status_code == 429:
                if attempt < max_attempts:
                    time.sleep(0.5 * attempt)
                    continue
                # max_attempts に達した
                _log_failure(event, last_status, attempt)
                response.raise_for_status()

            # 4xx（400, 401 など）: 非リトライ可能 → 即座に失敗ログ + 例外
            if 400 <= response.status_code < 500:
                _log_failure(event, last_status, attempt)
                response.raise_for_status()

            # 5xx or other: リトライ可能。max_attempts に達しなければ次の試行へ
            if attempt < max_attempts:
                time.sleep(0.5 * attempt)
                continue

            # max_attempts に達した → 最終失敗ログ + 例外
            _log_failure(event, last_status, attempt)
            response.raise_for_status()

        except httpx.TimeoutException as e:
            # ネットワーク例外もリトライ可能
            if attempt < max_attempts:
                time.sleep(0.5 * attempt)
                continue
            _log_failure(event, status=None, attempts=attempt)
            raise LineApiError(f"Line API timeout after {attempt} attempts") from e

        except httpx.TransportError as e:
            # その他のネットワーク例外もリトライ可能
            if attempt < max_attempts:
                time.sleep(0.5 * attempt)
                continue
            _log_failure(event, status=None, attempts=attempt)
            raise LineApiError(f"Line API transport error after {attempt} attempts") from e

        except httpx.HTTPStatusError:
            # HTTPStatusError は既にログ済みで、呼び出し元で処理される
            # そのまま re-raise
            raise


def _log_failure(event: str, status: int | None = None, attempts: int = 1) -> None:
    """LINE API 失敗をstructured log（JSON）で記録。PII は含めない。

    Args:
        event: イベント種別（"reply" or "push"）。
        status: HTTP status code, またはネットワーク例外なら None。
        attempts: 試行回数。
    """
    log_data = {
        "event": event,
        "status": status,
        "attempts": attempts,
    }
    logger.error("line_api_send_failed %s", json.dumps(log_data))
