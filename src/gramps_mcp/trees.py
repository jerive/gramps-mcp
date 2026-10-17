"""Mounts one MCP sub-server per configured Gramps tree, generated from its
openapi.json."""

from __future__ import annotations

import os
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal

import httpx2
from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from fastmcp.server.auth import AccessToken
from fastmcp.server.dependencies import get_access_token
from fastmcp.server.providers.openapi.routing import MCPType
from fastmcp.utilities.openapi import HTTPRoute
from mcp.types import ToolAnnotations

from .changes import OBJECT_CLASSES, register_apply_changes, set_object_schemas
from .core import (
    GRAMPS_BACKEND_URL,
    build_body_handle_injection_hook,
    build_http_debug_hooks,
    is_tree_member,
    login_gramps_user,
    start_token_refresh_loop,
)
from .spec import TREE_PARAM, object_schemas, prepare_spec
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
# `{object_type}` is the generic path spec.py makes out of the per-type ones.
_TYPES = "|".join((*OBJECT_TYPES, r"\{object_type\}"))
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


@dataclass(frozen=True)
class Tree:
    id: str
    name: str
    client: httpx2.AsyncClient
    # None: no sign-in is required of the user, so nothing to check.
    is_member: Callable[[AccessToken | None], bool] | None


class Trees:
    """The trees of this server, each with the Gramps user and client it writes
    and reads as. Gramps cannot give one user several trees, so there is one
    client per tree; a call says which one with its `tree`."""

    def __init__(self, trees: list[Tree]) -> None:
        self._trees = {tree.id: tree for tree in trees}

    @property
    def ids(self) -> list[str]:
        return list(self._trees)

    def _allowed(self, tree: Tree) -> bool:
        return tree.is_member is None or tree.is_member(get_access_token())

    def visible(self) -> list[Tree]:
        """The trees the user of the current call may use."""
        return [tree for tree in self._trees.values() if self._allowed(tree)]

    def client(self, tree_id: str | None) -> httpx2.AsyncClient:
        """The client of a tree, or a `ToolError` saying why there is none. A
        tree that exists but is not the user's reads like one that does not."""
        if tree_id is None and len(self._trees) == 1:
            tree: Tree | None = next(iter(self._trees.values()))
        elif tree_id is None:
            raise ToolError(f"Say which tree: one of {', '.join(self._trees)}.")
        else:
            tree = self._trees.get(tree_id)
        if tree is None or not self._allowed(tree):
            raise ToolError(f"No tree {tree_id!r} is available to you. Use list_trees.")
        return tree.client


class RoutedClient(httpx2.AsyncClient):
    """The client the generated read tools use: it sends each request through
    the client of the tree named by its `tree` parameter, and Gramps never sees
    that parameter."""

    def __init__(self, trees: Trees) -> None:
        super().__init__(base_url=GRAMPS_BACKEND_URL)
        self._trees = trees

    async def send(self, request: httpx2.Request, **kwargs: Any) -> httpx2.Response:
        params = request.url.params
        client = self._trees.client(params.get(TREE_PARAM))
        content = await request.aread()
        headers = [
            (name, value)
            for name, value in request.headers.multi_items()
            if name.lower() not in {"host", "content-length"}
        ]
        rest = params.remove(TREE_PARAM)
        routed = client.build_request(
            request.method,
            f"{request.url.path}?{rest}" if rest else request.url.path,
            headers=headers,
            content=content,
        )
        return await client.send(routed, **kwargs)


def connect_tree(project: str, *, check_members: bool) -> Tree:
    """Logs in to one Gramps tree as its service user."""
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

    # Only check membership when the server itself requires Google sign-in;
    # otherwise there is no token, nobody would be a member, and no tool would
    # ever be usable in the documented unauthenticated/local/single-user mode.
    return Tree(
        id=project.lower(),
        name=project,
        client=client,
        is_member=is_tree_member(project, get_bearer_token) if check_members else None,
    )


def customize_component(_route: HTTPRoute, component: Any) -> None:
    # Gramps' declared response schemas are stricter than what the API really
    # returns (e.g. extra Person fields), and MCP clients validate structured
    # results against outputSchema, so calls would fail. Without a schema the
    # data is still returned as structured content.
    if hasattr(component, "output_schema"):
        component.output_schema = None


def mount_trees(
    projects: list[str], openapi_spec: dict[str, Any], mcp: FastMCP
) -> None:
    """Logs in to each Gramps tree and gives `mcp` one set of tools for all of
    them: every tool takes the `tree` it is for."""
    trees = Trees(
        [
            connect_tree(project, check_members=mcp.auth is not None)
            for project in projects
        ]
    )
    add_tree_tools(trees, openapi_spec, mcp)


def add_tree_tools(trees: Trees, openapi_spec: dict[str, Any], mcp: FastMCP) -> None:
    """The tools of `trees`, generated from the backend's spec."""
    set_object_schemas(
        object_schemas(openapi_spec, {t: c for t, c in OBJECT_CLASSES.items()})
    )
    child = FastMCP.from_openapi(
        openapi_spec=prepare_spec(
            openapi_spec, read_tools_mode(), OBJECT_TYPES, trees.ids
        ),
        client=RoutedClient(trees),
        name="Gramps",
        route_map_fn=map_route,
        mcp_component_fn=customize_component,
    )

    @child.tool(
        name="list_trees",
        description=(
            "The trees you can read and change: the `id` is what the other tools "
            "take as `tree`."
        ),
        annotations=ToolAnnotations(read_only_hint=True, open_world_hint=False),
    )
    def list_trees() -> list[dict[str, str]]:
        return [{"id": tree.id, "name": tree.name} for tree in trees.visible()]

    if ui_enabled():
        register_ui(child, trees)
    else:
        register_apply_changes(child, trees)
    mcp.mount(child)
