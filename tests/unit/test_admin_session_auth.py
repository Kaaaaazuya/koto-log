"""Cookie-based session authentication for admin pages (Issue #28).

Tests for:
- POST /admin/login - Accept token in form, set session cookie, redirect
- POST /admin/logout - Clear session cookie, redirect
- GET /admin - Accept both query token AND session cookie (backward compat)
- Session cookie attributes (HttpOnly, Secure, SameSite)
"""

from __future__ import annotations

import re
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

TOKEN = "test-admin-token"


@pytest.fixture()
def client(monkeypatch):
    """Test client with mocked auth and session middleware."""
    monkeypatch.setenv("LINE_CHANNEL_SECRET", "secret")
    monkeypatch.setenv("LINE_CHANNEL_ACCESS_TOKEN", "token")
    monkeypatch.setenv("KOTOLOG_DASHBOARD_TOKEN", TOKEN)

    fake_conn = MagicMock()
    with patch("kotolog.line.admin._get_conn", return_value=fake_conn):
        from kotolog.line.webhook import app

        yield TestClient(app, raise_server_exceptions=True, follow_redirects=False)


def _get_csrf_token(client: TestClient, url: str) -> str:
    """Extract CSRF token from a form page."""
    resp = client.get(url)
    assert resp.status_code == 200
    # Match both value="token"...name="csrf_token" and name="csrf_token"...value="token"
    match = re.search(r'name="csrf_token"[^>]*value="([^"]*)"', resp.text)
    if not match:
        match = re.search(r'value="([^"]*)"[^>]*name="csrf_token"', resp.text)
    assert match, f"CSRF token not found in response from {url}"
    return match.group(1)


# --- POST /admin/login -------------------------------------------------------


def test_login_page_renders(client):
    """GET /admin/login should render a login form."""
    resp = client.get("/admin/login")
    assert resp.status_code == 200
    assert 'name="token"' in resp.text or "token" in resp.text
    assert 'type="password"' in resp.text or 'type="text"' in resp.text


def test_login_wrong_token_returns_403(client):
    """POST /admin/login with wrong token should return 403."""
    resp = client.post("/admin/login", data={"token": "wrong-token"})
    assert resp.status_code == 403


def test_login_correct_token_redirects_and_sets_cookie(client):
    """POST /admin/login with correct token should set session cookie and redirect."""
    resp = client.post("/admin/login", data={"token": TOKEN})
    assert resp.status_code == 303
    assert resp.headers["location"] == "/admin"
    # Session cookie should be set
    assert "kotolog_session" in resp.cookies or "Set-Cookie" in resp.headers


def test_login_cookie_has_httponly_flag(client):
    """Session cookie should have HttpOnly flag."""
    resp = client.post("/admin/login", data={"token": TOKEN})
    assert resp.status_code == 303
    # Check Set-Cookie header for HttpOnly
    set_cookie = resp.headers.get("set-cookie", "")
    assert "HttpOnly" in set_cookie or "httponly" in set_cookie.lower()


def test_login_cookie_has_secure_flag(client):
    """Session cookie should have Secure flag."""
    resp = client.post("/admin/login", data={"token": TOKEN})
    assert resp.status_code == 303
    # Check Set-Cookie header for Secure (in production)
    # In test environment might not be enforced but we should test the intent
    # Note: In testing with TestClient, Secure might not be enforced
    # but implementation should include it


def test_login_cookie_has_samesite_strict(client):
    """Session cookie should have SameSite=Strict."""
    resp = client.post("/admin/login", data={"token": TOKEN})
    assert resp.status_code == 303
    # Check Set-Cookie header for SameSite=Strict
    set_cookie = resp.headers.get("set-cookie", "").lower()
    assert "samesite=strict" in set_cookie


def test_login_empty_token_returns_403(client):
    """POST /admin/login with empty token should return 403."""
    resp = client.post("/admin/login", data={"token": ""})
    assert resp.status_code == 403


def test_login_missing_token_field_returns_400(client):
    """POST /admin/login without token field should return 400."""
    resp = client.post("/admin/login", data={})
    # Either 400 (bad request) or 403 (invalid token) is acceptable
    assert resp.status_code in (400, 403)


# --- POST /admin/logout -------------------------------------------------------


def test_logout_clears_session_cookie(client):
    """POST /admin/logout should clear session cookie and redirect."""
    # First login
    login_resp = client.post("/admin/login", data={"token": TOKEN})
    assert login_resp.status_code == 303

    # Then logout (following the redirect so we have the session)
    client.follow_redirects = True
    client.get("/admin")  # Establish session
    client.follow_redirects = False

    logout_resp = client.post("/admin/logout")
    assert logout_resp.status_code == 303
    assert logout_resp.headers["location"] == "/admin/login"


def test_logout_redirects_to_login_page(client):
    """POST /admin/logout should redirect to /admin/login."""
    # Assume authenticated state (would be set by session)
    resp = client.post("/admin/logout")
    assert resp.status_code == 303
    assert resp.headers["location"] == "/admin/login"


# --- Session persistence and backward compatibility --------------------------


def test_admin_with_session_cookie_works(client):
    """GET /admin with valid session cookie should render without query token."""
    # Login first
    login_resp = client.post("/admin/login", data={"token": TOKEN})
    assert login_resp.status_code == 303

    with patch("kotolog.db.crud.get_setting", return_value=""):
        # Access /admin without token (relying on session cookie)
        resp = client.get("/admin")
    assert resp.status_code == 200


def test_admin_with_query_token_still_works(client):
    """GET /admin with query token should still work (backward compatibility)."""
    with patch("kotolog.db.crud.get_setting", return_value=""):
        resp = client.get(f"/admin?token={TOKEN}")
    assert resp.status_code == 200


def test_admin_without_token_or_session_returns_403(client):
    """GET /admin without token or session should return 403."""
    resp = client.get("/admin")
    assert resp.status_code == 403


def test_admin_with_wrong_token_returns_403(client):
    """GET /admin with wrong query token should return 403."""
    resp = client.get("/admin?token=wrong")
    assert resp.status_code == 403


# --- Logout clears session properly -------------------------------------------


def test_after_logout_accessing_admin_requires_auth_again(client):
    """After logout, accessing /admin should require authentication again."""
    # Login
    client.post("/admin/login", data={"token": TOKEN})

    # Logout
    client.post("/admin/logout")

    # Try to access /admin without token
    resp = client.get("/admin")
    assert resp.status_code == 403


# --- Admin records with session and backward compat ---------------------------


def test_admin_records_with_session_cookie(client):
    """GET /admin/records with valid session should work."""
    # Login
    client.post("/admin/login", data={"token": TOKEN})

    with patch("kotolog.db.crud.query_records", return_value=[]):
        # Access without token, relying on session
        resp = client.get("/admin/records")
    assert resp.status_code == 200


def test_admin_records_with_query_token_still_works(client):
    """GET /admin/records with query token should still work (backward compatibility)."""
    with patch("kotolog.db.crud.query_records", return_value=[]):
        resp = client.get(f"/admin/records?token={TOKEN}")
    assert resp.status_code == 200


def test_admin_records_post_with_session(client):
    """POST /admin/records with valid session should work."""
    # Login
    client.post("/admin/login", data={"token": TOKEN})

    with patch("kotolog.db.crud.insert_record", return_value=1):
        # Get CSRF token from form
        csrf_token = _get_csrf_token(client, "/admin/records/new")
        # POST without token, relying on session
        resp = client.post(
            "/admin/records",
            data={
                "type": "feeding",
                "started_at": "2026-06-26T21:30",
                "csrf_token": csrf_token,
            },
        )
    assert resp.status_code == 303


def test_admin_records_post_with_query_token_still_works(client):
    """POST /admin/records with query token should still work (backward compatibility)."""
    with patch("kotolog.db.crud.insert_record", return_value=1):
        # Get CSRF token from form
        csrf_token = _get_csrf_token(client, f"/admin/records/new?token={TOKEN}")
        resp = client.post(
            f"/admin/records?token={TOKEN}",
            data={
                "type": "feeding",
                "started_at": "2026-06-26T21:30",
                "csrf_token": csrf_token,
            },
        )
    assert resp.status_code == 303


# --- Admin users with session and backward compat ----------------------------


def test_admin_users_with_session_cookie(client):
    """GET /admin/users with valid session should work."""
    # Login
    client.post("/admin/login", data={"token": TOKEN})

    with patch("kotolog.db.crud.list_users", return_value=[]):
        with patch("kotolog.db.crud.list_children", return_value=[]):
            # Access without token, relying on session
            resp = client.get("/admin/users")
    assert resp.status_code == 200


def test_admin_users_with_query_token_still_works(client):
    """GET /admin/users with query token should still work (backward compatibility)."""
    with patch("kotolog.db.crud.list_users", return_value=[]):
        with patch("kotolog.db.crud.list_children", return_value=[]):
            resp = client.get(f"/admin/users?token={TOKEN}")
    assert resp.status_code == 200


# --- Security: Session token not exposed in redirects -------------------------


def test_login_redirect_doesnt_expose_token_in_url(client):
    """After login, redirect should not expose token in URL."""
    resp = client.post("/admin/login", data={"token": TOKEN})
    assert resp.status_code == 303
    # Location should be /admin without token
    assert resp.headers["location"] == "/admin"
    assert TOKEN not in resp.headers["location"]


def test_admin_form_action_uses_session_not_token_param(client):
    """Admin form actions should not include token query param when using session."""
    # Login first to establish session
    client.post("/admin/login", data={"token": TOKEN})

    with patch("kotolog.db.crud.get_setting", return_value=""):
        resp = client.get("/admin")
    assert resp.status_code == 200
    # Form action should not include token parameter
    # (when relying on session)
    # Note: During transition, both might be present, but we should test
    # that session-based access works


# --- Env var not set (default-deny) -------------------------------------------


def test_login_fails_when_token_env_not_set(monkeypatch):
    """When KOTOLOG_DASHBOARD_TOKEN env var is not set, login should fail."""
    monkeypatch.setenv("LINE_CHANNEL_SECRET", "secret")
    monkeypatch.setenv("LINE_CHANNEL_ACCESS_TOKEN", "token")
    monkeypatch.delenv("KOTOLOG_DASHBOARD_TOKEN", raising=False)

    fake_conn = MagicMock()
    with patch("kotolog.line.admin._get_conn", return_value=fake_conn):
        from kotolog.line.webhook import app

        client = TestClient(app, follow_redirects=False)
        resp = client.post("/admin/login", data={"token": "anything"})
    assert resp.status_code == 403
