"""Shared auth, login, and token-refresh helpers for the Gramps MCP server."""

from __future__ import annotations

import base64
import json
import logging
import os
import re
import sys
import threading
import time
from collections.abc import Callable
from typing import Any

import httpx2
from fastmcp.server.auth import AuthCheck, AuthContext
from fastmcp.server.auth.providers.google import GoogleProvider
from key_value.aio.stores.redis import RedisStore
from tenacity import (
    Retrying,
    before_sleep_log,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential_jitter,
)

GRAMPS_BACKEND_URL = os.environ.get("GRAMPS_BACKEND_URL", "http://localhost:5000").rstrip(
    "/"
)
GRAMPS_LOGIN_LOCK = threading.Lock()
GRAMPS_LOGIN_LAST_ATTEMPT_TS = 0.0

# Emails that bypass per-tree membership checks and can access every mounted tree.
ADMIN_EMAILS = frozenset(
    email.strip().lower()
    for email in os.environ.get("GRAMPS_MCP_ADMIN_EMAILS", "").split(",")
    if email.strip()
)

logger = logging.getLogger("gramps_mcp")
logger.setLevel(os.getenv("GRAMPS_MCP_LOG_LEVEL", "INFO").upper())
if not logger.handlers:
    # fastmcp only attaches handlers to its own loggers, so ours needs one to reach stdout.
    _handler = logging.StreamHandler(sys.stdout)
    _handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
    )
    logger.addHandler(_handler)

HTTP_LOG_BODY_LIMIT = int(os.getenv("GRAMPS_MCP_HTTP_LOG_LIMIT", "4000"))

_COLLECTION_HANDLE_URL_RE = re.compile(r"^/api/[a-zA-Z_]+/([^/]+)/?$")


def build_body_handle_injection_hook() -> dict[str, list[Any]]:
    """Ensures PUT/PATCH bodies carry the resource `handle` from the URL.

    The MCP tool schema deliberately omits `handle` from the request body (it
    collides with the path parameter of the same name), but the Gramps REST
    API still requires it in the JSON payload and rejects the update with a
    bare 400 otherwise.
    """

    async def inject_handle(request: httpx2.Request) -> None:
        if request.method not in {"PUT", "PATCH", "GET", "DELETE"}:
            return

        try:
            raw = request.read()
        except httpx2.RequestNotRead:
            raw = b""

        body = None
        if raw:
            try:
                body = json.loads(raw)
            except json.JSONDecodeError:
                body = None

        actual_handle = None
        if isinstance(body, dict) and body.get("handle"):
            actual_handle = str(body["handle"]).strip()
        elif request.url.params.get("handle"):
            actual_handle = str(request.url.params.get("handle")).strip()

        if actual_handle and (
            "%7Bhandle%7D" in request.url.path or "{handle}" in request.url.path
        ):
            new_path = request.url.path.replace("%7Bhandle%7D", actual_handle).replace(
                "{handle}", actual_handle
            )
            request.url = request.url.copy_with(path=new_path)

        match = _COLLECTION_HANDLE_URL_RE.match(request.url.path)
        if not match:
            return

        if isinstance(body, dict):
            body["handle"] = match.group(1)
            new_bytes = json.dumps(body).encode("utf-8")
            request._content = new_bytes
            request.stream = httpx2.ByteStream(new_bytes)
            request.headers["content-length"] = str(len(new_bytes))
            request.headers["content-type"] = "application/json"

    return {"request": [inject_handle]}


def build_http_debug_hooks() -> dict[str, list[Any]]:
    """httpx event hooks logging raw upstream Gramps requests/responses at DEBUG."""

    def _truncate(raw: bytes) -> str:
        text = raw.decode("utf-8", errors="replace")
        if len(text) > HTTP_LOG_BODY_LIMIT:
            return f"{text[:HTTP_LOG_BODY_LIMIT]}... [{len(text)} bytes total]"
        return text

    async def log_request(request: httpx2.Request) -> None:
        if not logger.isEnabledFor(logging.DEBUG):
            return
        try:
            body = _truncate(request.content) if request.content else ""
        except Exception:
            body = "<streaming body>"
        logger.debug("--> %s %s %s", request.method, request.url, body)

    async def log_response(response: httpx2.Response) -> None:
        if not logger.isEnabledFor(logging.DEBUG):
            return
        try:
            await response.aread()
            body = _truncate(response.content)
        except Exception as exc:
            body = f"<unreadable body: {exc}>"
        logger.debug("<-- %s %s %s", response.status_code, response.request.url, body)

    return {"request": [log_request], "response": [log_response]}


def _jwt_expiry_seconds(token: str | None) -> float | None:
    if not isinstance(token, str) or not token.strip():
        return None
    try:
        payload_b64 = token.split(".", 2)[1]
        padded = payload_b64 + "=" * (-len(payload_b64) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded).decode("utf-8"))
        exp = payload.get("exp")
        if isinstance(exp, (int, float)):
            return float(exp)
    except Exception:
        return None
    return None


def gramps_token_looks_valid(token: str | None) -> bool:
    if not isinstance(token, str) or not token.strip():
        return False
    try:
        response = httpx2.get(
            f"{GRAMPS_BACKEND_URL}/api/events/",
            params={"page": 1, "pagesize": 1, "keys": ["handle"]},
            headers={"Authorization": f"Bearer {token.strip()}"},
            timeout=15,
        )
        return response.status_code < 400
    except Exception:
        return False


def refresh_client_headers(client: httpx2.AsyncClient, refresh_token: str) -> str:
    response = httpx2.post(
        f"{GRAMPS_BACKEND_URL}/api/token/refresh/",
        json={"token": refresh_token},
        timeout=15,
    )
    if response.status_code >= 400:
        raise RuntimeError(
            f"Refresh failed with status {response.status_code}: {response.text}"
        )

    payload = response.json()
    access_token = payload.get("access_token")
    new_refresh_token = payload.get("refresh_token")
    if not isinstance(access_token, str) or not access_token.strip():
        raise RuntimeError("Refresh response did not include access_token")

    client.headers.update({"Authorization": f"Bearer {access_token.strip()}"})
    if isinstance(new_refresh_token, str) and new_refresh_token.strip():
        return new_refresh_token.strip()
    return refresh_token


class GrampsCredentialsError(RuntimeError):
    """Raised when Gramps rejects the credentials; never worth retrying."""


class _RetryableLoginError(RuntimeError):
    pass


def _post_login(username: str, password: str) -> tuple[str, str]:
    global GRAMPS_LOGIN_LAST_ATTEMPT_TS

    with GRAMPS_LOGIN_LOCK:
        elapsed = time.monotonic() - GRAMPS_LOGIN_LAST_ATTEMPT_TS
        if elapsed < 2.0:
            time.sleep(2.0 - elapsed)

        payload = httpx2.post(
            f"{GRAMPS_BACKEND_URL}/api/token/",
            json={"username": username, "password": password},
            timeout=30,
        )
        GRAMPS_LOGIN_LAST_ATTEMPT_TS = time.monotonic()

    if payload.status_code in {401, 403}:
        raise GrampsCredentialsError(
            f"Gramps rejected credentials for {username} (status {payload.status_code}): {payload.text}"
        )

    if payload.status_code >= 400:
        raise _RetryableLoginError(f"status {payload.status_code}: {payload.text}")

    try:
        token_payload = payload.json()
    except Exception as exc:
        raise _RetryableLoginError(f"non-JSON login response: {exc}") from exc

    access_token = token_payload.get("access_token")
    refresh_token = token_payload.get("refresh_token")
    if not isinstance(access_token, str) or not access_token.strip():
        raise _RetryableLoginError(
            f"login payload without access_token: {json.dumps(token_payload)}"
        )
    if not isinstance(refresh_token, str) or not refresh_token.strip():
        raise _RetryableLoginError(
            f"login payload without refresh_token: {json.dumps(token_payload)}"
        )

    return access_token.strip(), refresh_token.strip()


def login_gramps_user(username: str, password: str) -> tuple[str, str]:
    retrying = Retrying(
        retry=retry_if_exception_type((_RetryableLoginError, httpx2.HTTPError)),
        wait=wait_exponential_jitter(initial=2, max=30, jitter=2),
        stop=stop_after_attempt(int(os.getenv("GRAMPS_MCP_LOGIN_ATTEMPTS", "8"))),
        before_sleep=before_sleep_log(logger, logging.WARNING),
        reraise=True,
    )
    return retrying(_post_login, username, password)


_TREE_EMAILS_CACHE: dict[str, tuple[float, set[str]]] = {}
_TREE_EMAILS_CACHE_LOCK = threading.Lock()
_TREE_EMAILS_TTL_SECONDS = 300


def fetch_gramps_user_emails(access_token: str) -> set[str]:
    response = httpx2.get(
        f"{GRAMPS_BACKEND_URL}/api/users/",
        headers={"Authorization": f"Bearer {access_token.strip()}"},
        timeout=30,
    )
    if response.status_code >= 400:
        raise RuntimeError(
            f"GET /api/users/ failed with status {response.status_code}: {response.content!r}"
        )

    emails: set[str] = set()
    for user in response.json():
        email = user.get("email") if isinstance(user, dict) else None
        if isinstance(email, str) and email.strip():
            emails.add(email.strip().lower())
    return emails


def get_tree_member_emails(
    project: str, get_bearer_token: Callable[[], str]
) -> set[str]:
    """Email addresses of Gramps users registered on this specific tree, cached briefly."""
    with _TREE_EMAILS_CACHE_LOCK:
        cached = _TREE_EMAILS_CACHE.get(project)
        if cached and time.time() - cached[0] < _TREE_EMAILS_TTL_SECONDS:
            return cached[1]

    bearer = get_bearer_token()
    emails: set[str] = set()
    if bearer:
        try:
            emails = fetch_gramps_user_emails(bearer)
        except Exception:
            logger.warning(
                "Failed to fetch Gramps users for tree %s", project, exc_info=True
            )

    with _TREE_EMAILS_CACHE_LOCK:
        if emails:
            _TREE_EMAILS_CACHE[project] = (time.time(), emails)
            return emails
        return _TREE_EMAILS_CACHE.get(project, (0.0, set()))[1]


def require_tree_member(project: str, get_bearer_token: Callable[[], str]) -> AuthCheck:
    """AuthCheck granting access to admins and Gramps users registered on this tree."""

    def _check(ctx: AuthContext) -> bool:
        if ctx.token is None:
            return False
        email = ctx.token.claims.get("email")
        if not isinstance(email, str) or not email.strip():
            return True
        email = email.strip().lower()
        return email in ADMIN_EMAILS or email in get_tree_member_emails(
            project, get_bearer_token
        )

    return _check


def build_google_auth_provider() -> GoogleProvider | None:
    """Build a Google OAuth provider from env vars, or None to disable auth.

    Disabling auth is only appropriate for a locally-run, single-user server;
    anything reachable over a network should set GOOGLE_CLIENT_ID/SECRET.
    """
    client_id = os.environ.get("GOOGLE_CLIENT_ID", "").strip()
    client_secret = os.environ.get("GOOGLE_CLIENT_SECRET", "").strip()
    if not client_id or not client_secret:
        return None

    base_url = os.environ.get("MCP_BASE_URL", "http://localhost:8000").strip()
    redirect_path = os.environ.get("MCP_GOOGLE_REDIRECT_PATH", "/auth/callback").strip()
    required_scopes = [
        "openid",
        "https://www.googleapis.com/auth/userinfo.email",
    ]
    client_storage_url = os.environ.get(
        "MCP_CLIENT_STORAGE_URL",
        "redis://localhost:6379/0",
    ).strip()

    try:
        client_storage = RedisStore(url=client_storage_url)
    except Exception as exc:  # pragma: no cover - surfaced through clear startup error
        raise RuntimeError(
            f"Failed to initialize Redis-backed OAuth client storage at {client_storage_url}: {exc}"
        ) from exc

    return GoogleProvider(
        client_id=client_id,
        client_secret=client_secret,
        base_url=base_url,
        redirect_path=redirect_path,
        required_scopes=required_scopes,
        client_storage=client_storage,
    )


def start_token_refresh_loop(
    client: httpx2.AsyncClient,
    refresh_token: str,
    *,
    username: str | None = None,
    password: str | None = None,
    interval_seconds: int = 300,
) -> threading.Thread:
    def _refresh_loop() -> None:
        nonlocal refresh_token
        while True:
            try:
                auth_header = client.headers.get("Authorization", "")
                bearer = (
                    auth_header.replace("Bearer ", "", 1).strip()
                    if auth_header.startswith("Bearer ")
                    else auth_header.strip()
                )

                if gramps_token_looks_valid(bearer):
                    expiry_epoch = _jwt_expiry_seconds(bearer)
                    if expiry_epoch is not None:
                        remaining = max(0.0, expiry_epoch - time.time())
                        sleep_for = min(
                            float(interval_seconds), max(30.0, remaining - 90.0)
                        )
                    else:
                        sleep_for = float(interval_seconds)
                    time.sleep(sleep_for)
                    continue

                try:
                    refresh_token = refresh_client_headers(client, refresh_token)
                except Exception:
                    if username and password:
                        new_access, new_refresh = login_gramps_user(username, password)
                        client.headers.update({"Authorization": f"Bearer {new_access}"})
                        refresh_token = new_refresh
                    else:
                        raise
            except Exception as exc:
                logger.error("Token refresh failed: %s", exc)
                time.sleep(30)

    thread = threading.Thread(target=_refresh_loop, daemon=True)
    thread.start()
    return thread
