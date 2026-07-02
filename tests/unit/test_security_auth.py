"""Security authentication tests for admin screens (Issue #27).

Implements "default-deny" authentication: admin screens ALWAYS require a valid token,
regardless of environment variable configuration.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

TOKEN = "test-admin-token"


@pytest.fixture()
def client(monkeypatch):
    """FastAPI TestClient with token configured."""
    monkeypatch.setenv("LINE_CHANNEL_SECRET", "secret")
    monkeypatch.setenv("LINE_CHANNEL_ACCESS_TOKEN", "token")
    monkeypatch.setenv("KOTOLOG_DASHBOARD_TOKEN", TOKEN)

    fake_conn = MagicMock()
    with patch("kotolog.line.admin._get_conn_and_child", return_value=(fake_conn, 1)):
        from kotolog.line.webhook import app

        yield TestClient(app, raise_server_exceptions=True, follow_redirects=False)


@pytest.fixture()
def client_no_env_token(monkeypatch):
    """FastAPI TestClient with NO token configured in environment.

    For default-deny, even without a configured token, requests should require
    and validate against a token. This tests that behavior.
    """
    monkeypatch.setenv("LINE_CHANNEL_SECRET", "secret")
    monkeypatch.setenv("LINE_CHANNEL_ACCESS_TOKEN", "token")
    # IMPORTANT: Do NOT set KOTOLOG_DASHBOARD_TOKEN
    monkeypatch.delenv("KOTOLOG_DASHBOARD_TOKEN", raising=False)

    fake_conn = MagicMock()
    with patch("kotolog.line.admin._get_conn_and_child", return_value=(fake_conn, 1)):
        from kotolog.line.webhook import app

        yield TestClient(app, raise_server_exceptions=True, follow_redirects=False)


# --- Issue #27: Default-deny authentication (always require token) ----------


def test_admin_no_token_returns_403_when_env_token_set(client):
    """Admin page requires token when KOTOLOG_DASHBOARD_TOKEN is set."""
    resp = client.get("/admin")
    assert resp.status_code == 403


def test_admin_records_no_token_returns_403_when_env_token_set(client):
    """Admin records page requires token when KOTOLOG_DASHBOARD_TOKEN is set."""
    resp = client.get("/admin/records")
    assert resp.status_code == 403


def test_admin_with_wrong_token_returns_403(client):
    """Admin page rejects wrong token."""
    resp = client.get("/admin?token=wrong-token")
    assert resp.status_code == 403


def test_admin_with_correct_token_returns_200(client):
    """Admin page allows access with correct token."""
    resp = client.get(f"/admin?token={TOKEN}")
    assert resp.status_code == 200


def test_admin_records_no_token_returns_403_when_env_token_not_set(client_no_env_token):
    """ISSUE #27: Even when KOTOLOG_DASHBOARD_TOKEN is NOT set, admin requires token.

    This is the "default-deny" behavior: reject by default unless explicitly authorized.
    """
    resp = client_no_env_token.get("/admin/records")
    assert resp.status_code == 403


def test_admin_no_token_returns_403_when_env_token_not_set(client_no_env_token):
    """ISSUE #27: Admin page requires token even when no env token is configured."""
    resp = client_no_env_token.get("/admin")
    assert resp.status_code == 403


def test_admin_users_no_token_returns_403_when_env_token_set(client):
    """Admin users page requires token when KOTOLOG_DASHBOARD_TOKEN is set."""
    resp = client.get("/admin/users")
    assert resp.status_code == 403


def test_admin_users_no_token_returns_403_when_env_token_not_set(client_no_env_token):
    """ISSUE #27: Admin users page requires token even when no env token is configured."""
    resp = client_no_env_token.get("/admin/users")
    assert resp.status_code == 403


def test_dashboard_no_token_returns_403_when_env_token_set(monkeypatch):
    """Dashboard requires token when KOTOLOG_DASHBOARD_TOKEN is set."""
    monkeypatch.setenv("LINE_CHANNEL_SECRET", "secret")
    monkeypatch.setenv("LINE_CHANNEL_ACCESS_TOKEN", "token")
    monkeypatch.setenv("KOTOLOG_DASHBOARD_TOKEN", TOKEN)

    fake_conn = MagicMock()
    with patch("kotolog.line.dashboard._get_conn_and_child", return_value=(fake_conn, 1)):
        from kotolog.line.webhook import app

        client = TestClient(app, raise_server_exceptions=True)
        resp = client.get("/dashboard")
    assert resp.status_code == 403


def test_dashboard_no_token_returns_403_when_env_token_not_set(monkeypatch):
    """ISSUE #27: Dashboard requires token even when no env token is configured."""
    monkeypatch.setenv("LINE_CHANNEL_SECRET", "secret")
    monkeypatch.setenv("LINE_CHANNEL_ACCESS_TOKEN", "token")
    monkeypatch.delenv("KOTOLOG_DASHBOARD_TOKEN", raising=False)

    fake_conn = MagicMock()
    with patch("kotolog.line.dashboard._get_conn_and_child", return_value=(fake_conn, 1)):
        from kotolog.line.webhook import app

        client = TestClient(app, raise_server_exceptions=True)
        resp = client.get("/dashboard")
    assert resp.status_code == 403
