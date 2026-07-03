"""CSRF トークン生成・検証（Issue #32）。

各リクエストで CSRF トークンを生成して session に保存し、POST リクエストで検証する。
トークンは Form Data の `csrf_token` フィールドで送信される。
"""

from __future__ import annotations

import secrets

from fastapi import HTTPException, Request

_CSRF_TOKEN_FIELD = "csrf_token"
_SESSION_CSRF_KEY = "csrf_token"


def generate_csrf_token() -> str:
    """セキュアなランダム CSRF トークンを生成する。"""
    return secrets.token_urlsafe(32)


def get_or_create_csrf_token(request: Request) -> str:
    """リクエストの session から CSRF トークンを取得し、なければ生成する。

    リクエストごとに一度だけ生成され、その後は同じトークンが再利用される。
    テンプレート/フォーム側で使用する。
    """
    if _SESSION_CSRF_KEY not in request.session:
        request.session[_SESSION_CSRF_KEY] = generate_csrf_token()
    return request.session[_SESSION_CSRF_KEY]


def check_csrf_token(request: Request, form_data: dict) -> None:
    """POST リクエストの CSRF トークンを検証する。

    session に保存されたトークンと form_data の csrf_token を比較する。
    呼び出し元で form_data = await request.form() を実行して渡す必要がある。

    Raises:
        HTTPException: トークンが無効または欠落している場合は 403 を送出。
    """
    expected_token = request.session.get(_SESSION_CSRF_KEY)

    if not expected_token:
        raise HTTPException(status_code=403, detail="CSRF token missing from session")

    provided_token = form_data.get(_CSRF_TOKEN_FIELD)
    if not provided_token:
        raise HTTPException(status_code=403, detail="CSRF token missing from request")

    if not secrets.compare_digest(provided_token, expected_token):
        raise HTTPException(status_code=403, detail="Invalid CSRF token")
