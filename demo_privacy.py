"""Visitor boundaries for the hosted fictional demo."""
import hashlib
import re
import secrets
from contextvars import ContextVar

from starlette.responses import JSONResponse

_scope = ContextVar("demo_scope", default="")
COOKIE = "__Host-orderproof"


def storage_key(value):
    return _scope.get() + value


async def isolate_visitor(request, call_next):
    cookie = request.cookies.get(COOKIE, "")
    fresh = not re.fullmatch(r"[0-9a-f]{64}", cookie)
    if fresh:
        cookie = secrets.token_hex(32)
    scope = hashlib.sha256(cookie.encode()).hexdigest()
    context = _scope.set(scope + ":")
    try:
        origin = request.headers.get("origin")
        foreign = origin and origin != str(request.base_url).rstrip("/")
        if request.method not in {"GET", "HEAD", "OPTIONS"} and (
                foreign or request.headers.get("sec-fetch-site") == "cross-site"):
            response = JSONResponse({"detail": "Open the demo to change an order."}, status_code=403)
        elif request.method == "POST" and request.url.path.rstrip("/") == "/orders" and (
                not re.fullmatch(r"[0-9]{1,8}", request.headers.get("content-length", ""))
                or int(request.headers["content-length"]) > 5 * 1024 * 1024 + 65536):
            response = JSONResponse({"detail": "Select a bundled sample order."}, status_code=413)
        else:
            response = await call_next(request)
        if fresh:
            response.set_cookie(COOKIE, cookie, secure=True, httponly=True, samesite="strict", path="/")
        response.headers["Cache-Control"] = "private, no-store"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        return response
    finally:
        _scope.reset(context)
