"""HTTP Security Headers Middleware (Issue #35).

Add security headers to all responses using pure ASGI middleware:
- Content-Security-Policy (CSP)
- X-Frame-Options
- X-Content-Type-Options
- Referrer-Policy
- X-XSS-Protection
- Strict-Transport-Security (HSTS)
"""

from __future__ import annotations

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send


class SecurityHeadersMiddleware:
    """Pure ASGI middleware for adding HTTP security headers."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return

        async def send_with_security_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)

                # CSP: Restrict to self, allow fonts and styles from trusted sources
                headers["Content-Security-Policy"] = (
                    "default-src 'self'; "
                    "script-src 'self' 'unsafe-inline'; "
                    "style-src 'self' 'unsafe-inline'; "
                    "img-src 'self' data: https:; "
                    "font-src 'self' data:; "
                    "connect-src 'self'; "
                    "frame-ancestors 'none'; "
                    "base-uri 'self'; "
                    "form-action 'self'"
                )

                # Prevent clickjacking
                headers["X-Frame-Options"] = "DENY"

                # Prevent MIME type sniffing
                headers["X-Content-Type-Options"] = "nosniff"

                # Control referrer information
                headers["Referrer-Policy"] = "strict-origin-when-cross-origin"

                # Legacy XSS protection header
                headers["X-XSS-Protection"] = "1; mode=block"

                # HSTS: Enforce HTTPS for 1 year (if HTTPS is enabled)
                headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains; preload"

            await send(message)

        await self.app(scope, receive, send_with_security_headers)
