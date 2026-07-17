"""Security authentication tests for admin screens (Issue #27).

Implements "default-deny" authentication: admin screens ALWAYS require a valid token,
regardless of environment variable configuration.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

TOKEN = "test-admin-token"


def _login(client, token=TOKEN):
    """Helper to log in via POST /admin/login."""
    r = client.post("/admin/login", data={"token": token})
    assert r.status_code in (303, 302)
    return client


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


# --- Issue #100: Session-based authentication (no query token auth) ----------


def test_admin_no_session_redirects_to_login(client):
    """Unauthenticated GET /admin redirects to /admin/login (303)."""
    resp = client.get("/admin")
    assert resp.status_code == 303
    assert resp.headers["location"] == "/admin/login"


def test_admin_records_no_session_redirects_to_login(client):
    """Unauthenticated GET /admin/records redirects to /admin/login (303)."""
    resp = client.get("/admin/records")
    assert resp.status_code == 303
    assert resp.headers["location"] == "/admin/login"


def test_admin_query_token_alone_does_not_auth(client):
    """Query token in URL alone no longer grants access (must use session)."""
    resp = client.get(f"/admin?token={TOKEN}")
    assert resp.status_code == 303
    assert resp.headers["location"] == "/admin/login"


def test_admin_with_session_returns_200(client):
    """Admin page allows access after login (session-based)."""
    with patch("kotolog.db.crud.get_setting", return_value=None):
        _login(client, TOKEN)
        resp = client.get("/admin")
    assert resp.status_code == 200


def test_admin_records_no_session_redirects_to_login_no_env(client_no_env_token):
    """ISSUE #27: Even when KOTOLOG_DASHBOARD_TOKEN is NOT set, admin requires session.

    This is the "default-deny" behavior: reject by default unless explicitly authorized.
    """
    resp = client_no_env_token.get("/admin/records")
    assert resp.status_code == 303
    assert resp.headers["location"] == "/admin/login"


def test_admin_no_session_redirects_to_login_no_env(client_no_env_token):
    """ISSUE #27: Admin page requires session even when no env token is configured."""
    resp = client_no_env_token.get("/admin")
    assert resp.status_code == 303
    assert resp.headers["location"] == "/admin/login"


def test_admin_users_no_session_redirects_to_login(client):
    """Unauthenticated GET /admin/users redirects to /admin/login (303)."""
    resp = client.get("/admin/users")
    assert resp.status_code == 303
    assert resp.headers["location"] == "/admin/login"


def test_admin_users_no_session_redirects_to_login_no_env(client_no_env_token):
    """ISSUE #27: Admin users page requires session even when no env token is configured."""
    resp = client_no_env_token.get("/admin/users")
    assert resp.status_code == 303
    assert resp.headers["location"] == "/admin/login"


def test_dashboard_no_session_redirects_to_login(monkeypatch):
    """Unauthenticated GET /dashboard redirects to /admin/login (303)."""
    monkeypatch.setenv("LINE_CHANNEL_SECRET", "secret")
    monkeypatch.setenv("LINE_CHANNEL_ACCESS_TOKEN", "token")
    monkeypatch.setenv("KOTOLOG_DASHBOARD_TOKEN", TOKEN)

    fake_conn = MagicMock()
    with patch("kotolog.line.dashboard._get_conn_and_child", return_value=(fake_conn, 1)):
        from kotolog.line.webhook import app

        client = TestClient(app, raise_server_exceptions=True, follow_redirects=False)
        resp = client.get("/dashboard")
    assert resp.status_code == 303
    assert resp.headers["location"] == "/admin/login"


def test_dashboard_no_session_redirects_to_login_no_env(monkeypatch):
    """ISSUE #27: Dashboard requires session even when no env token is configured."""
    monkeypatch.setenv("LINE_CHANNEL_SECRET", "secret")
    monkeypatch.setenv("LINE_CHANNEL_ACCESS_TOKEN", "token")
    monkeypatch.delenv("KOTOLOG_DASHBOARD_TOKEN", raising=False)

    fake_conn = MagicMock()
    with patch("kotolog.line.dashboard._get_conn_and_child", return_value=(fake_conn, 1)):
        from kotolog.line.webhook import app

        client = TestClient(app, raise_server_exceptions=True, follow_redirects=False)
        resp = client.get("/dashboard")
    assert resp.status_code == 303
    assert resp.headers["location"] == "/admin/login"


def test_login_wrong_token_returns_403(client):
    """POST /admin/login with wrong token returns 403."""
    resp = client.post("/admin/login", data={"token": "wrong-token"})
    assert resp.status_code == 403


def test_login_correct_token_sets_session(client):
    """POST /admin/login with correct token sets session and redirects."""
    with patch("kotolog.db.crud.get_setting", return_value=None):
        resp = client.post("/admin/login", data={"token": TOKEN})
        assert resp.status_code == 303
        # Session should be set; verify by making another request
        resp2 = client.get("/admin")
        assert resp2.status_code == 200
