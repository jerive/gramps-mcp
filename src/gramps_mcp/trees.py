"""Mounts one MCP sub-server per configured Gramps tree, generated from its openapi.json."""

from __future__ import annotations

import os
from typing import Any

import httpx2
from fastmcp import FastMCP
from fastmcp.server.providers.openapi.routing import MCPType
from fastmcp.utilities.openapi import HTTPRoute

from .core import (
    GRAMPS_BACKEND_URL,
    build_body_handle_injection_hook,
    build_http_debug_hooks,
    login_gramps_user,
    require_tree_member,
    start_token_refresh_loop,
)

# Route prefixes excluded from every tree's generated MCP tool set: either
# not useful to an agent (server config, OIDC config), redundant with the
# auth this server already performs (token refresh, user listing), or
# a surface better kept out of an LLM's hands (saved/custom filters).
EXCLUDED_ROUTE_PREFIXES = (
    "/api/filters/",
    "/api/token/",
    "/api/users/",
    "/api/facts/",
    "/api/facts",
    "/api/config",
    "/api/oidc/config",
)


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

    def route_mapper(route: HTTPRoute, route_type: MCPType) -> MCPType:
        if route.path.startswith(EXCLUDED_ROUTE_PREFIXES) or route.path in (
            "/api/facts",
        ):
            return MCPType.EXCLUDE
        return route_type

    def get_bearer_token() -> str:
        return client.headers.get("Authorization", "").removeprefix("Bearer ").strip()

    tree_check = require_tree_member(project, get_bearer_token)

    def customize_component(_route: HTTPRoute, component: Any) -> None:
        component.auth = tree_check

    child = FastMCP.from_openapi(
        openapi_spec=openapi_spec,
        client=client,
        name=f"Tree {project}",
        route_map_fn=route_mapper,
        mcp_component_fn=customize_component,
    )
    mcp.mount(child, namespace=f"tree-{project.lower()}")
