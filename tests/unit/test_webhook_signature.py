"""T2.1: Webhook 署名検証の単体テスト。

_verify_signature の純粋関数テストと、
/webhook エンドポイントの 200/401 を確認する。
"""

from __future__ import annotations

import base64
import hashlib
import hmac

import pytest
from fastapi.testclient import TestClient

CHANNEL_SECRET = "test_secret_xyz"


def _sign(body: bytes, secret: str = CHANNEL_SECRET) -> str:
    h = hmac.new(secret.encode(), body, hashlib.sha256).digest()
    return base64.b64encode(h).decode()


# --- _verify_signature の純粋関数テスト ------------------------------------


def test_verify_signature_valid():
    from kotolog.line.webhook import _verify_signature

    body = b'{"events":[]}'
    assert _verify_signature(body, _sign(body), CHANNEL_SECRET) is True


def test_verify_signature_invalid_sig():
    from kotolog.line.webhook import _verify_signature

    body = b'{"events":[]}'
    assert _verify_signature(body, "bad_sig", CHANNEL_SECRET) is False


def test_verify_signature_empty_sig():
    from kotolog.line.webhook import _verify_signature

    body = b'{"events":[]}'
    assert _verify_signature(body, "", CHANNEL_SECRET) is False


def test_verify_signature_wrong_secret():
    from kotolog.line.webhook import _verify_signature

    body = b'{"events":[]}'
    sig = _sign(body, "other_secret")
    assert _verify_signature(body, sig, CHANNEL_SECRET) is False


# --- /webhook エンドポイントテスト ------------------------------------------


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setenv("LINE_CHANNEL_SECRET", CHANNEL_SECRET)
    monkeypatch.setenv("LINE_CHANNEL_ACCESS_TOKEN", "dummy_token")
    from kotolog.line.webhook import app

    return TestClient(app, raise_server_exceptions=True)


def test_valid_signature_returns_200(client):
    body = b'{"events":[],"destination":"Uxxx"}'
    resp = client.post("/webhook", content=body, headers={"X-Line-Signature": _sign(body)})
    assert resp.status_code == 200


def test_invalid_signature_returns_401(client):
    body = b'{"events":[],"destination":"Uxxx"}'
    resp = client.post("/webhook", content=body, headers={"X-Line-Signature": "invalid=="})
    assert resp.status_code == 401


def test_missing_signature_returns_401(client):
    body = b'{"events":[],"destination":"Uxxx"}'
    resp = client.post("/webhook", content=body)
    assert resp.status_code == 401
