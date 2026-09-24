"""CSRF defence by Origin/Referer check on state-changing requests (CLAUDE.md §2 row 16)."""

from urllib.parse import urlsplit

from starlette.responses import PlainTextResponse
from starlette.types import ASGIApp, Receive, Scope, Send

SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


class OriginCheckMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and scope["method"] not in SAFE_METHODS:
            headers = {k.decode().lower(): v.decode() for k, v in scope["headers"]}
            host = headers.get("host", "")
            source = headers.get("origin") or headers.get("referer")
            if not source or source == "null" or urlsplit(source).netloc != host:
                await PlainTextResponse("Cross-origin request blocked", status_code=403)(scope, receive, send)
                return
        await self.app(scope, receive, send)
