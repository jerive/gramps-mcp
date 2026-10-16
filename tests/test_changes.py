from __future__ import annotations

import json
from typing import Any

import httpx2
import pytest
from fastmcp import Client, FastMCP
from fastmcp.client.elicitation import ElicitResult
from pydantic import TypeAdapter

from gramps_mcp.changes import (
    ChangeError,
    CreateOperation,
    DeleteOperation,
    Operation,
    UpdateOperation,
    execute,
    format_confirmation,
    label_of,
    leaf_diff,
    merge_changes,
    plan,
    register_apply_changes,
)
from gramps_mcp.trees import is_read_route, map_route


def person(handle: str, first: str, surname: str, **extra: Any) -> dict[str, Any]:
    return {
        "handle": handle,
        "gramps_id": f"I{handle}",
        "gender": 0,
        "primary_name": {
            "first_name": first,
            "surname_list": [{"surname": surname}],
        },
        **extra,
    }


class FakeGramps:
    """Just enough of Gramps Web API: stores objects, records every request."""

    def __init__(self, objects: dict[str, dict[str, Any]], fail_on: str | None = None):
        self.objects = objects
        self.fail_on = fail_on
        self.calls: list[tuple[str, str]] = []

    def handler(self, request: httpx2.Request) -> httpx2.Response:
        self.calls.append((request.method, request.url.path))
        handle = request.url.path.rstrip("/").split("/")[-1]
        if self.fail_on == request.method:
            return httpx2.Response(400, json={"error": {"message": "boom"}})
        if request.method == "GET":
            if handle in self.objects:
                return httpx2.Response(200, json=self.objects[handle])
            return httpx2.Response(404, json={"error": {"message": "nope"}})
        if request.method == "PUT":
            self.objects[handle] = json.loads(request.content)
        elif request.method == "DELETE":
            self.objects.pop(handle, None)
        elif request.method == "POST":
            body = json.loads(request.content)
            self.objects[body["handle"]] = body
        return httpx2.Response(200, json=[])

    def client(self) -> httpx2.AsyncClient:
        return httpx2.AsyncClient(
            base_url="http://gramps", transport=httpx2.MockTransport(self.handler)
        )


def make_server(gramps: FakeGramps) -> FastMCP:
    server = FastMCP()
    register_apply_changes(server, gramps.client())
    return server


def writes(gramps: FakeGramps) -> list[tuple[str, str]]:
    return [c for c in gramps.calls if c[0] != "GET"]


def test_merge_changes_is_deep_and_keeps_the_rest():
    current = {"a": {"b": 1, "c": 2}, "list": [1, 2], "keep": True}
    merged = merge_changes(current, {"a": {"b": 9}, "list": [3]})
    assert merged == {"a": {"b": 9, "c": 2}, "list": [3], "keep": True}
    assert current["a"]["b"] == 1


def test_leaf_diff_lists_only_changed_paths():
    old = {"a": {"b": 1, "c": 2}, "x": "same"}
    new = {"a": {"b": 9, "c": 2}, "x": "same", "y": "added"}
    assert leaf_diff(old, new) == [("a.b", 1, 9), ("y", None, "added")]


def test_label_of_person_and_fallbacks():
    assert label_of(person("1", "Marie", "Dupont")) == "Marie Dupont (I1)"
    assert label_of({"handle": "h", "title": "Lyon"}) == "Lyon (h)"
    assert label_of({"handle": "h"}) == "h"


def test_operation_union_picks_the_right_model():
    adapter = TypeAdapter(list[Operation])
    ops = adapter.validate_python(
        [
            {"op": "create", "type": "notes", "object": {"_class": "Note"}},
            {"op": "update", "type": "people", "handle": "h", "changes": {"a": 1}},
            {"op": "delete", "type": "events", "handle": "e"},
        ]
    )
    assert [type(o) for o in ops] == [CreateOperation, UpdateOperation, DeleteOperation]
    with pytest.raises(ValueError):
        adapter.validate_python([{"op": "update", "type": "users", "handle": "h"}])


def test_read_routes_only():
    assert is_read_route("GET", "/api/people/")
    assert is_read_route("POST", "/api/people/query/")
    for method, path in [
        ("POST", "/api/people/"),
        ("PUT", "/api/people/{handle}"),
        ("DELETE", "/api/people/{handle}"),
        ("POST", "/api/people/{a}/merge/{b}"),
        ("POST", "/api/objects/"),
        ("POST", "/api/chat/"),
    ]:
        assert not is_read_route(method, path), (method, path)


async def test_generated_tools_are_read_only():
    def op(method: str, op_id: str) -> dict:
        return {
            method: {
                "operationId": op_id,
                "responses": {"200": {"description": "ok"}},
            }
        }

    spec = {
        "openapi": "3.0.0",
        "info": {"title": "t", "version": "1"},
        "paths": {
            "/api/people/": {**op("get", "get_people"), **op("post", "post_people")},
            "/api/people/{handle}": {
                **op("get", "get_person"),
                **op("put", "put_person"),
                **op("delete", "delete_person"),
            },
            "/api/people/query/": op("post", "post_people_query"),
            "/api/objects/": op("post", "post_objects"),
            "/api/users/": op("get", "get_users"),
        },
    }
    for path in ("/api/people/{handle}",):
        for entry in spec["paths"][path].values():
            entry["parameters"] = [
                {
                    "name": "handle",
                    "in": "path",
                    "required": True,
                    "schema": {"type": "string"},
                }
            ]
    server = FastMCP.from_openapi(
        openapi_spec=spec,
        client=httpx2.AsyncClient(base_url="http://x"),
        route_map_fn=map_route,
    )
    async with Client(server) as client:
        names = {t.name for t in await client.list_tools()}
    assert names == {"get_people", "get_person", "post_people_query"}


async def test_plan_reads_current_objects_and_describes_the_diff():
    gramps = FakeGramps({"1": person("1", "Marie", "Dupont", birth=1824)})
    ops = [
        UpdateOperation(op="update", type="people", handle="1", changes={"birth": 1842})
    ]
    planned = await plan(gramps.client(), ops)
    assert planned[0].body["birth"] == 1842
    assert planned[0].body["primary_name"]["first_name"] == "Marie"
    text = format_confirmation(planned)
    assert "Update people: Marie Dupont (I1)" in text
    assert "birth: 1824 → 1842" in text


async def test_plan_rejects_unknown_handle_and_too_many_operations():
    gramps = FakeGramps({})
    with pytest.raises(ChangeError, match="does not exist"):
        await plan(
            gramps.client(), [DeleteOperation(op="delete", type="people", handle="x")]
        )
    with pytest.raises(ChangeError, match="At most"):
        await plan(
            gramps.client(),
            [CreateOperation(op="create", type="notes", object={})] * 21,
        )
    with pytest.raises(ChangeError, match="No changes"):
        await plan(gramps.client(), [])


async def test_execute_stops_at_first_failure_and_reports_what_was_applied():
    gramps = FakeGramps({"1": person("1", "A", "B"), "2": person("2", "C", "D")})
    ops = [
        UpdateOperation(op="update", type="people", handle="1", changes={"gender": 1}),
        DeleteOperation(op="delete", type="people", handle="2"),
        CreateOperation(op="create", type="notes", object={"handle": "n"}),
    ]
    planned = await plan(gramps.client(), ops)
    gramps.fail_on = "DELETE"
    result = await execute(gramps.client(), planned)
    assert result["status"] == "partly_applied"
    assert len(result["applied"]) == 1
    assert result["failed"]["error"] == "boom"
    assert len(result["not_applied"]) == 1
    assert ("POST", "/api/notes/") not in gramps.calls


def accept(value: bool):
    async def handler(message, response_type, params, context):
        handler.seen = message
        return ElicitResult(action="accept", content={"value": value})

    return handler


async def call(server: FastMCP, operations: list[dict], **client_kwargs) -> dict:
    async with Client(server, mode="legacy", **client_kwargs) as client:
        result = await client.call_tool(
            "apply_changes", {"operations": operations}, raise_on_error=False
        )
    return result.structured_content or json.loads(result.content[0].text)


UPDATE_MARIE = [
    {"op": "update", "type": "people", "handle": "1", "changes": {"gender": 1}}
]


async def test_accepted_changes_are_written_after_one_confirmation():
    gramps = FakeGramps({"1": person("1", "Marie", "Dupont")})
    handler = accept(True)
    result = await call(make_server(gramps), UPDATE_MARIE, elicitation_handler=handler)
    assert result["status"] == "applied"
    assert writes(gramps) == [("PUT", "/api/people/1")]
    assert gramps.objects["1"]["gender"] == 1
    assert "gender: 0 → 1" in handler.seen


async def test_declined_changes_write_nothing():
    gramps = FakeGramps({"1": person("1", "Marie", "Dupont")})
    result = await call(
        make_server(gramps), UPDATE_MARIE, elicitation_handler=accept(False)
    )
    assert result["status"] == "declined"
    assert writes(gramps) == []


async def test_client_without_confirmation_support_writes_nothing():
    gramps = FakeGramps({"1": person("1", "Marie", "Dupont")})
    result = await call(make_server(gramps), UPDATE_MARIE)
    assert result["status"] == "not_applied"
    assert writes(gramps) == []


async def test_invalid_changes_are_reported_before_asking_anything():
    gramps = FakeGramps({})
    handler = accept(True)
    result = await call(
        make_server(gramps),
        [{"op": "delete", "type": "people", "handle": "missing"}],
        elicitation_handler=handler,
    )
    assert result["status"] == "invalid"
    assert not hasattr(handler, "seen")
