"""CSRF トークン生成・検証（Issue #32）。

各リクエストで CSRF トークンを生成して session に保存し、POST リクエストで検証する。
トークンは Form Data の `_csrf_token` フィールドで送信される。
"""

from __future__ import annotations

import secrets

from fastapi import HTTPException, Request

_CSRF_TOKEN_FIELD = "_csrf_token"
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


def check_csrf_token(request: Request, form_data: dict | None = None) -> None:
    """POST リクエストの CSRF トークンを検証する。

    session に保存されたトークンと form_data の _csrf_token を比較する。
    form_data が None の場合は form_data = await request.form() で取得する。

    Raises:
        HTTPException: トークンが無効または欠落している場合は 403 を送出。
    """
    expected_token = request.session.get(_SESSION_CSRF_KEY)

    if not expected_token:
        # session に CSRF トークンがない（session が初期化されていない）
        raise HTTPException(status_code=403, detail="CSRF token missing from session")

    # form_data から CSRF トークンを取得
    provided_token = None
    if form_data is not None:
        # 既に form_data が渡されている場合
        provided_token = form_data.get(_CSRF_TOKEN_FIELD)
    else:
        # form_data を request から取得する場合（async）
        # 注: この関数は同期なので、呼び出し元で form を取得して渡す必要がある
        provided_token = None

    if not provided_token:
        raise HTTPException(status_code=403, detail="CSRF token missing from request")

    # タイミング攻撃を防ぐため secrets.compare_digest を使用
    if not secrets.compare_digest(provided_token, expected_token):
        raise HTTPException(status_code=403, detail="Invalid CSRF token")
