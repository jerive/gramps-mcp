from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import httpx2
import pytest
from fastmcp import Client, FastMCP

from gramps_mcp.trees import (
    CORE_READ_ROUTES,
    OBJECT_TYPES,
    is_core_read_route,
    is_exposed,
    map_route,
    read_tools_mode,
)

FIXTURE = json.loads(
    (Path(__file__).parent / "data" / "gramps_routes.json").read_text()
)
ROUTES: list[tuple[str, str]] = [(m, p) for m, p in FIXTURE["routes"]]

# The tools of a tree, written out: this is the contract.
EXPECTED_CORE = sorted(
    [("GET", f"/api/{t}/") for t in OBJECT_TYPES]
    + [("GET", f"/api/{t}/{{handle}}") for t in OBJECT_TYPES]
    + [("POST", f"/api/{t}/query/") for t in OBJECT_TYPES]
    + [
        ("GET", "/api/search/"),
        ("GET", "/api/relations/{handle1}/{handle2}"),
        ("GET", "/api/relations/{handle1}/{handle2}/all"),
        ("GET", "/api/people/{handle}/timeline"),
        ("GET", "/api/families/{handle}/timeline"),
    ]
)


def exposed(mode: str) -> list[tuple[str, str]]:
    return sorted((m, p) for m, p in ROUTES if is_exposed(m, p, mode))  # type: ignore[arg-type]


def test_the_fixture_is_a_real_gramps_route_list():
    assert len(ROUTES) > 150 and FIXTURE["gramps_web_api"].startswith("3.")
    assert ("GET", "/api/people/") in ROUTES and (
        "PUT",
        "/api/people/{handle}",
    ) in ROUTES


def test_core_is_exactly_the_business_objects_and_a_few_ways_to_find_them():
    assert exposed("core") == EXPECTED_CORE
    assert len(EXPECTED_CORE) == 35


def test_core_leaves_out_what_an_agent_does_not_need():
    left_out = [
        "/api/media/{handle}/file",
        "/api/media/{handle}/thumbnail/{size}",
        "/api/media/{handle}/face_detection",
        "/api/media/archive/{filename}",
        "/api/people/{handle}/dna/matches",
        "/api/people/{handle}/ydna",
        "/api/reports/",
        "/api/exporters/",
        "/api/importers/",
        "/api/oidc/login/",
        "/api/tasks/{task_id}",
        "/api/trees/",
        "/api/types/",
        "/api/transactions/history/",
        "/api/holidays/",
        "/api/living/{handle}",
        "/api/places/coordinates/",
        "/api/metadata/",
    ]
    for path in left_out:
        assert not is_core_read_route("GET", path), path
    assert not any(is_exposed("GET", p, "core") for p in left_out)


def test_a_route_gramps_adds_later_is_not_exposed_by_default():
    assert not is_exposed("GET", "/api/newthing/", "core")
    assert not is_exposed("GET", "/api/people/{handle}/newthing", "core")
    assert not is_exposed("POST", "/api/people/query/extra", "core")


def test_all_keeps_every_read_route_but_the_excluded_prefixes():
    core, everything = set(exposed("core")), set(exposed("all"))
    assert core < everything and len(everything) == 93
    assert not any(p.startswith(("/api/users/", "/api/token/")) for _, p in everything)


@pytest.mark.parametrize("mode", ["core", "all"])
def test_no_write_route_is_ever_exposed(mode):
    writes = [
        (m, p) for m, p in ROUTES if m != "GET" and not p.rstrip("/").endswith("/query")
    ]
    assert writes and not [r for r in writes if is_exposed(*r, mode)]  # type: ignore[arg-type]


def test_every_core_pattern_matches_something_in_gramps():
    for method, pattern in CORE_READ_ROUTES:
        assert any(m == method and pattern.match(p) for m, p in ROUTES), pattern.pattern


def test_the_mode_comes_from_the_environment(monkeypatch):
    monkeypatch.delenv("GRAMPS_MCP_READ_TOOLS", raising=False)
    assert read_tools_mode() == "core"
    for value, mode in [("core", "core"), (" ALL ", "all")]:
        monkeypatch.setenv("GRAMPS_MCP_READ_TOOLS", value)
        assert read_tools_mode() == mode
    monkeypatch.setenv("GRAMPS_MCP_READ_TOOLS", "some")
    with pytest.raises(ValueError, match="must be core or all"):
        read_tools_mode()


def spec_from_routes() -> dict[str, Any]:
    paths: dict[str, dict[str, Any]] = {}
    for method, path in ROUTES:
        operation: dict[str, Any] = {
            "operationId": re.sub(r"\W+", "_", f"{method.lower()}{path}").strip("_"),
            "responses": {"200": {"description": "ok"}},
            "parameters": [
                {
                    "name": name,
                    "in": "path",
                    "required": True,
                    "schema": {"type": "string"},
                }
                for name in re.findall(r"\{(\w+)\}", path)
            ],
        }
        paths.setdefault(path, {})[method.lower()] = operation
    return {
        "openapi": "3.0.0",
        "info": {"title": "gramps", "version": "1"},
        "paths": paths,
    }


@pytest.mark.parametrize(("mode", "count"), [("core", 35), ("all", 93)])
async def test_the_server_built_from_the_real_routes_has_that_many_tools(
    monkeypatch, mode, count
):
    monkeypatch.setenv("GRAMPS_MCP_READ_TOOLS", mode)
    server = FastMCP.from_openapi(
        openapi_spec=spec_from_routes(),
        client=httpx2.AsyncClient(base_url="http://x"),
        route_map_fn=map_route,
    )
    async with Client(server) as client:
        tools = await client.list_tools()
    assert len(tools) == count
    assert not [t.name for t in tools if t.name.startswith(("put_", "delete_"))]
