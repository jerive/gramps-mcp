"""Mounts one MCP sub-server per configured Gramps tree, generated from its
openapi.json."""

from __future__ import annotations

import os
import re
from typing import Any, Literal

import httpx2
from fastmcp import FastMCP
from fastmcp.server.providers.openapi.routing import MCPType
from fastmcp.utilities.openapi import HTTPRoute

from .changes import register_apply_changes
from .core import (
    GRAMPS_BACKEND_URL,
    build_body_handle_injection_hook,
    build_http_debug_hooks,
    login_gramps_user,
    require_tree_member,
    start_token_refresh_loop,
)
from .ui import register_ui, ui_enabled

# The read tools of a tree: the business objects, and a few ways to find and
# relate them. Everything else Gramps Web API offers (reports, exports, imports,
# media files and thumbnails, DNA, OIDC, tasks, tree administration...) is left
# out: each tool costs context in every tree, and an agent has no use for them.
OBJECT_TYPES = (
    "people",
    "families",
    "events",
    "places",
    "citations",
    "sources",
    "repositories",
    "notes",
    "tags",
    "media",
)
_TYPES = "|".join(OBJECT_TYPES)
_PARAM = r"\{[^/{}]+\}"
CORE_READ_ROUTES = tuple(
    (method, re.compile(pattern))
    for method, pattern in (
        ("GET", rf"^/api/({_TYPES})/$"),
        ("GET", rf"^/api/({_TYPES})/{_PARAM}$"),
        ("POST", rf"^/api/({_TYPES})/query/$"),
        ("GET", r"^/api/search/$"),
        ("GET", rf"^/api/relations/{_PARAM}/{_PARAM}$"),
        ("GET", rf"^/api/relations/{_PARAM}/{_PARAM}/all$"),
        ("GET", rf"^/api/(people|families)/{_PARAM}/timeline$"),
    )
)

# With GRAMPS_MCP_READ_TOOLS=all, every read route except these prefixes: not
# useful to an agent (server config, OIDC config), redundant with the auth this
# server already performs (token refresh, user listing), or a surface better kept
# out of an LLM's hands (saved/custom filters).
EXCLUDED_ROUTE_PREFIXES = (
    "/api/filters/",
    "/api/token/",
    "/api/users/",
    "/api/facts/",
    "/api/facts",
    "/api/config",
    "/api/oidc/config",
)


def read_tools_mode() -> Literal["core", "all"]:
    value = os.environ.get("GRAMPS_MCP_READ_TOOLS", "core").strip().lower()
    if value == "core":
        return "core"
    if value == "all":
        return "all"
    raise ValueError(f"GRAMPS_MCP_READ_TOOLS must be core or all, got {value!r}")


def is_read_route(method: str, path: str) -> bool:
    """Whether a route only reads data.

    GET is a read, and so are the POST `.../query/` endpoints, which take their
    search criteria in the body. Every other POST, PUT, PATCH and DELETE is a
    write, including ones that look harmless (chat, parsers), so a new write
    endpoint is never exposed by accident.
    """
    method = method.upper()
    if method == "GET":
        return True
    return method == "POST" and path.rstrip("/").endswith("/query")


def is_core_read_route(method: str, path: str) -> bool:
    method = method.upper()
    return any(m == method and pattern.match(path) for m, pattern in CORE_READ_ROUTES)


def is_exposed(method: str, path: str, mode: Literal["core", "all"]) -> bool:
    """Whether a route becomes a tool. Writes never do: they go through
    `apply_changes`."""
    if not is_read_route(method, path):
        return False
    if mode == "core":
        return is_core_read_route(method, path)
    return not path.startswith(EXCLUDED_ROUTE_PREFIXES)


def map_route(route: HTTPRoute, route_type: MCPType) -> MCPType:
    if is_exposed(route.method, route.path, read_tools_mode()):
        return route_type
    return MCPType.EXCLUDE


def _env_key(project: str) -> str:
    return "".join(ch if ch.isalnum() else "_" for ch in project.strip().upper())


def get_tree_credentials(project: str) -> tuple[str, str]:
    """Reads the service-account username/password for one tree from the environment.

    Set GRAMPS_MCP_TREE_<PROJECT>_USERNAME and _PASSWORD, with <PROJECT> being
    the tree name from GRAMPS_MCP_TREES, uppercased with non-alphanumeric
    characters replaced by `_`.
    """
    key = _env_key(project)
    username = os.environ.get(f"GRAMPS_MCP_TREE_{key}_USERNAME", "").strip()
    password = os.environ.get(f"GRAMPS_MCP_TREE_{key}_PASSWORD", "").strip()
    if not username or not password:
        raise RuntimeError(
            f"Missing GRAMPS_MCP_TREE_{key}_USERNAME / GRAMPS_MCP_TREE_{key}_PASSWORD "
            f"for tree {project!r}."
        )
    return username, password


def mount_tree(project: str, openapi_spec: dict[str, Any], mcp: FastMCP) -> None:
    """Logs in to one Gramps tree and mounts its REST API as MCP tools under `mcp`."""
    username, password = get_tree_credentials(project)
    access_token, refresh_token = login_gramps_user(username, password)

    client = httpx2.AsyncClient(
        base_url=GRAMPS_BACKEND_URL,
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=120.0,
        event_hooks={
            "request": [
                *build_body_handle_injection_hook()["request"],
                *build_http_debug_hooks()["request"],
            ],
            "response": build_http_debug_hooks()["response"],
        },
    )

    start_token_refresh_loop(
        client,
        refresh_token.strip(),
        username=username,
        password=password,
    )

    def get_bearer_token() -> str:
        return client.headers.get("Authorization", "").removeprefix("Bearer ").strip()

    # Only gate tools behind tree membership when the server itself requires
    # Google sign-in; otherwise every request has no token (ctx.token is
    # None), require_tree_member would deny it, and *no* tool would ever be
    # usable in the documented unauthenticated/local/single-user mode.
    tree_check = (
        require_tree_member(project, get_bearer_token) if mcp.auth is not None else None
    )

    def customize_component(_route: HTTPRoute, component: Any) -> None:
        if tree_check is not None:
            component.auth = tree_check
        # Gramps' declared response schemas are stricter than what the API
        # really returns (e.g. extra Person fields), and MCP clients validate
        # structured results against outputSchema, so calls would fail.
        # Without a schema the data is still returned as structured content.
        if hasattr(component, "output_schema"):
            component.output_schema = None

    child = FastMCP.from_openapi(
        openapi_spec=openapi_spec,
        client=client,
        name=f"Tree {project}",
        route_map_fn=map_route,
        mcp_component_fn=customize_component,
    )
    if ui_enabled():
        register_ui(child, client, auth=tree_check)
    else:
        register_apply_changes(child, client, auth=tree_check)
    mcp.mount(child, namespace=f"tree-{project.lower()}")
