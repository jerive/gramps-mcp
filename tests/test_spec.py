from __future__ import annotations

import copy
from typing import Any

import httpx2
import pytest
from fastmcp import Client, FastMCP

from gramps_mcp.spec import (
    UnsupportedGrampsVersion,
    check_api_version,
    prepare_spec,
)
from gramps_mcp.trees import OBJECT_TYPES, map_route


def _list(t: str) -> dict[str, Any]:
    return {
        "operationId": f"get_{t}",
        "summary": f"List {t}",
        "parameters": [
            {"name": "page", "in": "query", "schema": {"type": "integer"}},
            {"name": "profile", "in": "query", "schema": {"type": "string"}},
        ],
        "responses": {"200": {"description": "ok"}},
    }


def _get(t: str) -> dict[str, Any]:
    return {
        "operationId": f"get_{t}_one",
        "summary": f"Get {t}",
        "parameters": [{"name": "keys", "in": "query", "schema": {"type": "string"}}],
        "responses": {"200": {"description": "ok"}},
    }


def _query(t: str) -> dict[str, Any]:
    return {
        "operationId": f"post_{t}_query",
        "requestBody": {
            "required": True,
            "content": {
                "application/json": {
                    "schema": {
                        "type": "object",
                        "properties": {"where_expr": {"type": "string"}},
                    }
                }
            },
        },
        "responses": {"200": {"description": "ok"}},
    }


def make_spec(version: str = "3.23.1") -> dict[str, Any]:
    paths: dict[str, Any] = {
        "/api/search/": {
            "get": {
                "operationId": "get_search",
                "responses": {"200": {"description": "ok"}},
            }
        },
        "/api/people/{handle}/timeline": {
            "get": {
                "operationId": "get_person_timeline",
                "responses": {"200": {"description": "ok"}},
            }
        },
        "/api/reports/": {
            "get": {
                "operationId": "get_reports",
                "responses": {"200": {"description": "ok"}},
            }
        },
    }
    for t in OBJECT_TYPES:
        paths[f"/api/{t}/"] = {
            "get": _list(t),
            "post": {
                "operationId": f"post_{t}",
                "responses": {"200": {"description": "ok"}},
            },
        }
        paths[f"/api/{t}/{{handle}}"] = {
            "parameters": [
                {
                    "name": "handle",
                    "in": "path",
                    "required": True,
                    "schema": {"type": "string"},
                }
            ],
            "get": _get(t),
            "put": {
                "operationId": f"put_{t}",
                "responses": {"200": {"description": "ok"}},
            },
        }
        paths[f"/api/{t}/query/"] = {"post": _query(t)}
    return {
        "openapi": "3.0.0",
        "info": {"title": "Gramps Web API", "version": version},
        "paths": paths,
    }


async def tools_of(spec: dict[str, Any], mode: str, monkeypatch) -> dict[str, Any]:
    monkeypatch.setenv("GRAMPS_MCP_READ_TOOLS", mode)
    server = FastMCP.from_openapi(
        openapi_spec=prepare_spec(spec, mode, OBJECT_TYPES),  # type: ignore[arg-type]
        client=httpx2.AsyncClient(base_url="http://x"),
        route_map_fn=map_route,
    )
    async with Client(server) as client:
        return {t.name: t for t in await client.list_tools()}


async def test_core_has_three_generic_tools_and_the_other_reads(monkeypatch):
    tools = await tools_of(make_spec(), "core", monkeypatch)
    assert sorted(tools) == [
        "get_object",
        "get_person_timeline",
        "get_search",
        "list_objects",
        "query_objects",
    ]
    schema = tools["list_objects"].input_schema
    assert schema["properties"]["object_type"]["enum"] == list(OBJECT_TYPES)
    assert {"object_type", "page", "profile"} <= set(schema["properties"])
    assert "where_expr" in tools["query_objects"].input_schema["properties"]
    assert {"object_type", "handle", "keys"} <= set(
        tools["get_object"].input_schema["properties"]
    )


async def test_all_mode_leaves_the_spec_alone(monkeypatch):
    tools = await tools_of(make_spec(), "all", monkeypatch)
    assert "get_people" in tools and "list_objects" not in tools
    assert "get_reports" in tools
    assert not {"post_people", "put_people"} & set(tools)


async def test_generic_tools_call_the_right_url(monkeypatch):
    monkeypatch.setenv("GRAMPS_MCP_READ_TOOLS", "core")
    seen: list[tuple[str, str]] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append((request.method, str(request.url.path)))
        return httpx2.Response(200, json=[])

    server = FastMCP.from_openapi(
        openapi_spec=prepare_spec(make_spec(), "core", OBJECT_TYPES),
        client=httpx2.AsyncClient(
            base_url="http://x", transport=httpx2.MockTransport(handler)
        ),
        route_map_fn=map_route,
    )
    async with Client(server) as client:
        await client.call_tool("list_objects", {"object_type": "events"})
        await client.call_tool("get_object", {"object_type": "places", "handle": "h1"})
        await client.call_tool(
            "query_objects", {"object_type": "people", "where_expr": "x"}
        )
    assert seen == [
        ("GET", "/api/events/"),
        ("GET", "/api/places/h1"),
        ("POST", "/api/people/query/"),
    ]


async def test_a_type_that_differs_keeps_its_own_routes(monkeypatch):
    spec = make_spec()
    spec["paths"]["/api/notes/"]["get"]["parameters"].append(
        {"name": "format", "in": "query", "schema": {"type": "string"}}
    )
    tools = await tools_of(spec, "core", monkeypatch)
    # all three of its operations stay, rather than a half-generic mix
    assert {"get_notes", "get_notes_one", "post_notes_query"} <= set(tools)
    for generic in ("list_objects", "get_object", "query_objects"):
        enum = tools[generic].input_schema["properties"]["object_type"]["enum"]
        assert "notes" not in enum and "people" in enum


def test_prepare_spec_does_not_modify_its_input():
    spec = make_spec()
    before = copy.deepcopy(spec)
    prepare_spec(spec, "core", OBJECT_TYPES)
    assert spec == before


def test_versions(caplog):
    assert check_api_version(make_spec("3.23.0")) == "3.23.0"
    with pytest.raises(UnsupportedGrampsVersion, match="3.21.4 is too old"):
        check_api_version(make_spec("3.21.4"))
    with caplog.at_level("WARNING", logger="gramps_mcp"):
        check_api_version(make_spec("3.30.0"))
    assert "newer than" in caplog.text


async def test_the_goql_guide_can_be_added_to_any_server():
    from gramps_mcp.goql import register_goql_resource

    server = FastMCP()
    register_goql_resource(server)
    async with Client(server) as client:
        uris = {str(r.uri) for r in await client.list_resources()}
        text = (await client.read_resource("gramps://goql-spec"))[0].text
    assert uris == {"gramps://goql-spec"} and "where_expr" in text
    assert "query_objects" in text


def test_the_spec_is_read_from_the_backend(monkeypatch):
    from gramps_mcp import spec

    seen = []

    def fake_get(url, timeout):
        seen.append(url)
        return httpx2.Response(
            200,
            json={"info": {"version": "3.23.1"}},
            request=httpx2.Request("GET", url),
        )

    monkeypatch.setattr(spec.httpx2, "get", fake_get)
    assert spec.fetch_openapi_spec()["info"]["version"] == "3.23.1"
    assert seen == [f"{spec.GRAMPS_BACKEND_URL}/api/openapi.json"]


COMPONENTS = {
    "components": {
        "schemas": {
            "Place": {
                "type": "object",
                "properties": {
                    "lat": {"type": "string", "description": "Latitude."},
                    "name": {
                        "description": "Primary name.",
                        "allOf": [{"$ref": "#/components/schemas/PlaceName"}],
                    },
                    "profile": {"type": "object"},
                    "backlinks": {"type": "array"},
                },
            },
            "PlaceName": {
                "type": "object",
                "description": "dropped",
                "properties": {"value": {"type": "string", "description": "dropped"}},
            },
        }
    }
}


def test_the_schema_of_each_object_comes_from_the_spec():
    from gramps_mcp.spec import object_schemas

    schemas = object_schemas(COMPONENTS, {"places": "Place", "people": "Person"})
    assert list(schemas) == ["places"]  # Person is not in this spec
    place = schemas["places"]["properties"]
    assert "profile" not in place and "backlinks" not in place  # read-only
    assert place["lat"] == {"type": "string", "description": "Latitude."}
    # a reference is inlined; the field keeps its description, the target loses its own
    assert place["name"]["description"] == "Primary name."
    assert place["name"]["allOf"][0] == {
        "type": "object",
        "properties": {"value": {"type": "string"}},
    }
