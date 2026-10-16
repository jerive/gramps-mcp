from __future__ import annotations

import json
from collections.abc import Callable
from copy import deepcopy
from typing import Any, Literal

import httpx2
import pytest
from fastmcp import Client, FastMCP
from fastmcp.client.elicitation import ElicitResult
from mcp.types import Implementation
from pydantic import TypeAdapter, ValidationError

from gramps_mcp.changes import (
    MAX_OPERATIONS,
    OBJECT_CLASSES,
    ApplyChangesResult,
    ChangeError,
    CreateOperation,
    DeleteOperation,
    Operations,
    Planned,
    UpdateOperation,
    confirmation_mode,
    describe,
    execute,
    format_confirmation,
    label_of,
    leaf_diff,
    list_diff_lines,
    merge_changes,
    plan,
    register_apply_changes,
)
from gramps_mcp.trees import is_read_route, map_route

OPERATIONS = TypeAdapter(Operations)


def person(handle: str, first: str, surname: str, **extra: Any) -> dict[str, Any]:
    return {
        "_class": "Person",
        "handle": handle,
        "gramps_id": f"I{handle}",
        "gender": 0,
        "primary_name": {"first_name": first, "surname_list": [{"surname": surname}]},
        **extra,
    }


class FakeGramps:
    """Just enough of Gramps Web API: object endpoints that return their
    transaction, and `POST /api/transactions/?undo=true` with the real
    semantics (all or nothing, refused if an object changed since)."""

    def __init__(self, objects: dict[str, dict[str, Any]]):
        self.objects = objects
        self.calls: list[tuple[str, str]] = []
        self.fail: Callable[[str, str], bool] = lambda method, path: False
        self.on_failure: Callable[[], None] = lambda: None
        self._next = 0

    def handler(self, request: httpx2.Request) -> httpx2.Response:
        method, path = request.method, request.url.path
        self.calls.append((method, path))
        if path == "/api/transactions/":
            return self._undo(json.loads(request.content))
        if self.fail(method, path):
            self.on_failure()
            return httpx2.Response(400, json={"error": {"message": "boom"}})
        parts = path.strip("/").split("/")
        type_, handle = parts[1], parts[2] if len(parts) > 2 else None
        cls = OBJECT_CLASSES[type_]  # type: ignore[index]
        if method == "GET":
            if handle in self.objects:
                return httpx2.Response(200, json=self.objects[handle])
            return httpx2.Response(404, json={"error": {"message": "nope"}})
        if method == "PUT":
            old, new = self.objects[handle], json.loads(request.content)  # type: ignore[index]
            self.objects[handle] = new  # type: ignore[index]
            return self._txn("update", cls, handle, old, new)  # type: ignore[arg-type]
        if method == "DELETE":
            old = self.objects.pop(handle)  # type: ignore[arg-type]
            return self._txn("delete", cls, handle, old, None)  # type: ignore[arg-type]
        body = json.loads(request.content)
        self._next += 1
        new_handle = body.get("handle") or f"new{self._next}"
        body = {**body, "handle": new_handle}
        self.objects[new_handle] = body
        return self._txn("add", cls, new_handle, None, body)

    @staticmethod
    def _txn(kind: str, cls: str, handle: str, old: Any, new: Any) -> httpx2.Response:
        item = {"type": kind, "_class": cls, "handle": handle, "old": old, "new": new}
        return httpx2.Response(200, json=[item])

    def _undo(self, payload: list[dict[str, Any]]) -> httpx2.Response:
        state = deepcopy(self.objects)
        for item in reversed(payload):
            handle, old, new = item["handle"], item["old"], item["new"]
            if state.get(handle) != new:
                return httpx2.Response(
                    400, json={"error": {"message": "Object has changed"}}
                )
            if old is None:
                state.pop(handle)
            else:
                state[handle] = old
        self.objects = state
        return httpx2.Response(200, json=[])

    def client(self) -> httpx2.AsyncClient:
        return httpx2.AsyncClient(
            base_url="http://gramps", transport=httpx2.MockTransport(self.handler)
        )


def make_server(
    gramps: FakeGramps, confirmation: Literal["required", "when-supported"] = "required"
) -> FastMCP:
    server = FastMCP()
    register_apply_changes(server, gramps.client(), confirmation=confirmation)
    return server


def writes(gramps: FakeGramps) -> list[tuple[str, str]]:
    return [c for c in gramps.calls if c[0] != "GET"]


def update(handle: str, **changes: Any) -> dict[str, Any]:
    return {"op": "update", "type": "people", "handle": handle, "changes": changes}


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


def test_read_routes_only():
    assert is_read_route("GET", "/api/people/")
    assert is_read_route("POST", "/api/people/query/")
    for method, path in [
        ("POST", "/api/people/"),
        ("PUT", "/api/people/{handle}"),
        ("DELETE", "/api/people/{handle}"),
        ("POST", "/api/people/{a}/merge/{b}"),
        ("POST", "/api/objects/"),
        ("POST", "/api/transactions/"),
        ("POST", "/api/chat/"),
    ]:
        assert not is_read_route(method, path), (method, path)


async def test_generated_tools_are_read_only():
    def op(method: str, op_id: str) -> dict[str, Any]:
        return {
            method: {"operationId": op_id, "responses": {"200": {"description": "ok"}}}
        }

    handle_param = [
        {"name": "handle", "in": "path", "required": True, "schema": {"type": "string"}}
    ]
    by_handle = {
        **op("get", "get_person"),
        **op("put", "put_person"),
        **op("delete", "delete_person"),
    }
    for entry in by_handle.values():
        entry["parameters"] = handle_param
    spec = {
        "openapi": "3.0.0",
        "info": {"title": "t", "version": "1"},
        "paths": {
            "/api/people/": {**op("get", "get_people"), **op("post", "post_people")},
            "/api/people/{handle}": by_handle,
            "/api/people/query/": op("post", "post_people_query"),
            "/api/objects/": op("post", "post_objects"),
            "/api/transactions/": op("post", "post_transactions"),
            "/api/users/": op("get", "get_users"),
        },
    }
    server = FastMCP.from_openapi(
        openapi_spec=spec,
        client=httpx2.AsyncClient(base_url="http://x"),
        route_map_fn=map_route,
    )
    async with Client(server) as client:
        names = {t.name for t in await client.list_tools()}
    assert names == {"get_people", "get_person", "post_people_query"}


class TestOperationTyping:
    def test_discriminated_union_builds_the_right_models(self):
        ops = OPERATIONS.validate_python(
            [
                {"op": "create", "type": "notes", "object": {"text": {}}},
                update("h", gender=1),
                {"op": "delete", "type": "events", "handle": "e"},
            ]
        )
        assert [type(o) for o in ops] == [
            CreateOperation,
            UpdateOperation,
            DeleteOperation,
        ]

    def test_create_fills_in_and_checks_the_class(self):
        (op,) = OPERATIONS.validate_python(
            [{"op": "create", "type": "people", "object": {"gender": 1}}]
        )
        assert op.object["_class"] == "Person"
        with pytest.raises(ValidationError, match="needs `_class` 'Person'"):
            OPERATIONS.validate_python(
                [{"op": "create", "type": "people", "object": {"_class": "Family"}}]
            )

    @pytest.mark.parametrize(
        "operation",
        [
            {"op": "create", "type": "people", "object": {}, "extra": 1},
            {"op": "delete", "type": "people", "handle": "h", "changes": {}},
            {"op": "update", "type": "users", "handle": "h", "changes": {"a": 1}},
            {"op": "update", "type": "people", "handle": "", "changes": {"a": 1}},
            {"op": "update", "type": "people", "handle": "x" * 51, "changes": {"a": 1}},
            {"op": "update", "type": "people", "handle": "h", "changes": {}},
            {
                "op": "update",
                "type": "people",
                "handle": "h",
                "changes": {"handle": "z"},
            },
            {
                "op": "update",
                "type": "people",
                "handle": "h",
                "changes": {"backlinks": {}},
            },
            {"op": "create", "type": "people", "object": {"profile": {}}},
            {"op": "rename", "type": "people", "handle": "h"},
            {"type": "people", "handle": "h"},
        ],
    )
    def test_invalid_operations_are_rejected(self, operation):
        with pytest.raises(ValidationError):
            OPERATIONS.validate_python([operation])

    def test_list_bounds_and_duplicates(self):
        with pytest.raises(ValidationError):
            OPERATIONS.validate_python([])
        with pytest.raises(ValidationError):
            OPERATIONS.validate_python([update("h", gender=1)] * (MAX_OPERATIONS + 1))
        with pytest.raises(ValidationError, match="appears twice"):
            OPERATIONS.validate_python([update("h", gender=1), update("h", gender=2)])
        OPERATIONS.validate_python(
            [{"op": "create", "type": "notes", "object": {}}] * 2
        )


async def test_advertised_schemas_are_precise():
    server = make_server(FakeGramps({}))
    async with Client(server) as client:
        (tool,) = await client.list_tools()
    operations = tool.input_schema["properties"]["operations"]
    assert operations["minItems"] == 1 and operations["maxItems"] == MAX_OPERATIONS
    variants = operations["items"]["oneOf"]
    assert [v["properties"]["op"]["const"] for v in variants] == [
        "create",
        "update",
        "delete",
    ]
    assert all(v.get("additionalProperties") is False for v in variants)
    assert tool.annotations.destructive_hint is True
    status = tool.output_schema["properties"]["status"]["enum"]
    assert set(status) == {
        "applied",
        "declined",
        "not_applied",
        "invalid",
        "rolled_back",
        "partly_applied",
    }


async def test_plan_describes_the_diff_and_rejects_unknown_handles():
    gramps = FakeGramps({"1": person("1", "Marie", "Dupont", birth=1824)})
    ops = OPERATIONS.validate_python([update("1", birth=1842)])
    planned = await plan(gramps.client(), ops)
    assert planned[0].body is not None and planned[0].body["birth"] == 1842
    assert planned[0].body["primary_name"]["first_name"] == "Marie"
    text = format_confirmation(planned)
    assert "Update people: Marie Dupont (I1)" in text and "birth: 1824 → 1842" in text
    with pytest.raises(ChangeError, match="does not exist"):
        await plan(
            gramps.client(), OPERATIONS.validate_python([update("zzz", gender=1)])
        )


async def test_execute_returns_the_handle_of_created_objects():
    gramps = FakeGramps({})
    ops = OPERATIONS.validate_python([{"op": "create", "type": "notes", "object": {}}])
    result = await execute(gramps.client(), await plan(gramps.client(), ops))
    assert result.status == "applied"
    assert result.applied[0].handle == "new1"


async def test_failure_rolls_back_creates_updates_and_deletes_in_one_call():
    start = {"1": person("1", "A", "B"), "2": person("2", "C", "D")}
    gramps = FakeGramps(deepcopy(start))
    ops = OPERATIONS.validate_python(
        [
            update("1", gender=1),
            {"op": "delete", "type": "people", "handle": "2"},
            {"op": "create", "type": "notes", "object": {}},
            update("1", gender=2)
            if False
            else {"op": "create", "type": "tags", "object": {}},
        ]
    )
    planned = await plan(gramps.client(), ops)
    gramps.fail = lambda method, path: (method, path) == ("POST", "/api/tags/")
    result = await execute(gramps.client(), planned)
    assert result.status == "rolled_back"
    assert [c.op for c in result.rolled_back] == ["update", "delete", "create"]
    assert result.failed is not None and result.failed.error == "boom"
    assert result.applied == []
    assert gramps.objects == start
    assert gramps.calls.count(("POST", "/api/transactions/")) == 1


async def test_rollback_refused_when_an_object_changed_meanwhile():
    gramps = FakeGramps({"1": person("1", "A", "B")})
    ops = OPERATIONS.validate_python(
        [
            update("1", gender=1),
            update("1", gender=1)
            if False
            else {"op": "create", "type": "tags", "object": {}},
        ]
    )
    planned = await plan(gramps.client(), ops)
    gramps.fail = lambda method, path: (method, path) == ("POST", "/api/tags/")

    def someone_else_edits() -> None:
        gramps.objects["1"] = {**gramps.objects["1"], "gender": 3}

    gramps.on_failure = someone_else_edits
    result = await execute(gramps.client(), planned)
    assert result.status == "partly_applied"
    assert "Object has changed" in result.message
    assert [c.op for c in result.applied] == ["update"]
    assert gramps.objects["1"]["gender"] == 3


async def test_first_failure_writes_nothing_and_does_not_try_to_undo():
    gramps = FakeGramps({"1": person("1", "A", "B")})
    ops = OPERATIONS.validate_python(
        [update("1", gender=1), {"op": "create", "type": "tags", "object": {}}]
    )
    planned = await plan(gramps.client(), ops)
    gramps.fail = lambda method, path: method == "PUT"
    result = await execute(gramps.client(), planned)
    assert result.status == "not_applied"
    assert len(result.not_applied) == 1
    assert ("POST", "/api/transactions/") not in gramps.calls


def answer(value: bool, before: Callable[[], None] | None = None):
    async def handler(message, response_type, params, context):
        handler.messages.append(message)
        if before is not None and len(handler.messages) == 1:
            before()
        return ElicitResult(action="accept", content={"confirm": value})

    handler.messages = []
    return handler


def cancel():
    async def handler(message, response_type, params, context):
        handler.messages.append(message)
        return ElicitResult(action="cancel")

    handler.messages = []
    return handler


MODES = ["legacy", "auto"]  # auto negotiates the 2026-07-28 protocol


async def call(
    server: FastMCP, operations: list[dict[str, Any]], mode: str = "auto", **kwargs: Any
):
    async with Client(server, mode=mode, **kwargs) as client:
        return await client.call_tool(
            "apply_changes", {"operations": operations}, raise_on_error=False
        )


def parsed(result: Any) -> ApplyChangesResult:
    return ApplyChangesResult.model_validate(result.structured_content)


@pytest.mark.parametrize("mode", MODES)
async def test_accepted_changes_are_written_after_one_confirmation(mode):
    gramps = FakeGramps({"1": person("1", "Marie", "Dupont")})
    handler = answer(True)
    result = parsed(
        await call(
            make_server(gramps),
            [update("1", gender=1)],
            mode,
            elicitation_handler=handler,
        )
    )
    assert result.status == "applied"
    assert writes(gramps) == [("PUT", "/api/people/1")]
    assert gramps.objects["1"]["gender"] == 1
    assert len(handler.messages) == 1 and "gender: 0 → 1" in handler.messages[0]


@pytest.mark.parametrize("mode", MODES)
async def test_declined_changes_write_nothing(mode):
    gramps = FakeGramps({"1": person("1", "Marie", "Dupont")})
    result = parsed(
        await call(
            make_server(gramps),
            [update("1", gender=1)],
            mode,
            elicitation_handler=answer(False),
        )
    )
    assert result.status == "declined"
    assert writes(gramps) == []


@pytest.mark.parametrize("mode", MODES)
async def test_cancelled_confirmation_writes_nothing(mode):
    gramps = FakeGramps({"1": person("1", "Marie", "Dupont")})
    result = parsed(
        await call(
            make_server(gramps),
            [update("1", gender=1)],
            mode,
            elicitation_handler=cancel(),
        )
    )
    assert result.status == "declined"
    assert writes(gramps) == []


@pytest.mark.parametrize("mode", MODES)
async def test_client_without_confirmation_support_writes_nothing(mode):
    gramps = FakeGramps({"1": person("1", "Marie", "Dupont")})
    result = parsed(await call(make_server(gramps), [update("1", gender=1)], mode))
    assert result.status == "not_applied"
    assert "cannot ask" in result.message
    assert writes(gramps) == []


@pytest.mark.parametrize("mode", MODES)
async def test_unknown_handle_is_reported_before_asking_anything(mode):
    handler = answer(True)
    result = parsed(
        await call(
            make_server(FakeGramps({})),
            [update("missing", gender=1)],
            mode,
            elicitation_handler=handler,
        )
    )
    assert result.status == "invalid"
    assert handler.messages == []


@pytest.mark.parametrize("mode", MODES)
async def test_object_changed_while_asking_means_asking_again(mode):
    gramps = FakeGramps({"1": person("1", "Marie", "Dupont", birth=1824)})

    def someone_else_edits() -> None:
        gramps.objects["1"] = {**gramps.objects["1"], "birth": 1830}

    handler = answer(True, before=someone_else_edits)
    result = parsed(
        await call(
            make_server(gramps),
            [update("1", birth=1842)],
            mode,
            elicitation_handler=handler,
        )
    )
    assert result.status == "applied"
    assert len(handler.messages) == 2
    assert "birth: 1824 → 1842" in handler.messages[0]
    assert "birth: 1830 → 1842" in handler.messages[1]
    assert gramps.objects["1"]["birth"] == 1842


async def test_confirmation_that_keeps_going_stale_writes_nothing():
    gramps = FakeGramps({"1": person("1", "Marie", "Dupont", birth=1824)})
    version = [1824]

    async def handler(message, response_type, params, context):
        version[0] += 1
        gramps.objects["1"] = {**gramps.objects["1"], "birth": version[0]}
        return ElicitResult(action="accept", content={"confirm": True})

    result = parsed(
        await call(
            make_server(gramps),
            [update("1", birth=1900)],
            "legacy",
            elicitation_handler=handler,
        )
    )
    assert result.status == "not_applied"
    assert "kept changing" in result.message
    assert writes(gramps) == []


@pytest.mark.parametrize("mode", MODES)
async def test_malformed_operations_are_refused_by_the_tool_schema(mode):
    gramps = FakeGramps({"1": person("1", "Marie", "Dupont")})
    bad = {"op": "update", "type": "people", "handle": "1", "changes": {"handle": "x"}}
    result = await call(
        make_server(gramps), [bad], mode, elicitation_handler=answer(True)
    )
    assert result.is_error
    assert writes(gramps) == []


async def test_tool_asks_claude_code_for_a_confirmation_every_time():
    async with Client(make_server(FakeGramps({}))) as client:
        (tool,) = await client.list_tools()
    assert tool.meta["anthropic/requiresUserInteraction"] is True


def test_list_changes_are_shown_as_added_and_removed_items():
    old = [{"ref": "kid1", "frel": "Birth"}, {"ref": "kid2"}]
    new = [{"ref": "kid1", "frel": "Birth"}, {"ref": "kid3", "frel": "Birth"}]
    assert list_diff_lines("child_ref_list", old, new) == [
        "  child_ref_list: + kid3",
        "  child_ref_list: - kid2",
    ]
    assert list_diff_lines("tag_list", ["a", "b"], ["b", "a"]) == [
        "  tag_list: reordered"
    ]
    planned = Planned(
        "update",
        "families",
        "f",
        "PUT",
        "/api/families/f",
        {"handle": "f", "child_ref_list": new},
    )
    lines = describe(planned, {"handle": "f", "child_ref_list": old})
    assert lines[1:] == ["  child_ref_list: + kid3", "  child_ref_list: - kid2"]


def test_confirmation_mode_from_the_environment(monkeypatch):
    monkeypatch.delenv("GRAMPS_MCP_CONFIRMATION", raising=False)
    assert confirmation_mode() == "required"
    monkeypatch.setenv("GRAMPS_MCP_CONFIRMATION", " When-Supported ")
    assert confirmation_mode() == "when-supported"
    monkeypatch.setenv("GRAMPS_MCP_CONFIRMATION", "never")
    with pytest.raises(ValueError, match="must be one of required, when-supported"):
        confirmation_mode()


def raw(action: str, content: dict[str, Any] | None = None):
    async def handler(message, response_type, params, context):
        return ElicitResult(action=action, content=content)  # type: ignore[arg-type]

    return handler


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize(
    ("action", "content", "expected"),
    [
        ("decline", None, "The user declined."),
        ("cancel", None, "dismissed the confirmation (cancel)"),
        ("accept", {}, "accept without confirming"),
        ("accept", {"value": True}, "accept without confirming"),
        (
            "accept",
            {"confirm": False},
            'accept without confirming (content: {"confirm": false})',
        ),
    ],
)
async def test_every_non_confirmation_says_what_the_client_sent(
    mode, action, content, expected
):
    gramps = FakeGramps({"1": person("1", "Marie", "Dupont")})
    result = parsed(
        await call(
            make_server(gramps, "when-supported"),
            [update("1", gender=1)],
            mode,
            elicitation_handler=raw(action, content),
        )
    )
    assert result.status == "declined"
    assert expected in result.message
    assert writes(gramps) == []


@pytest.mark.parametrize("mode", MODES)
async def test_when_supported_applies_directly_for_clients_that_cannot_ask(mode):
    gramps = FakeGramps({"1": person("1", "Marie", "Dupont")})
    result = parsed(
        await call(make_server(gramps, "when-supported"), [update("1", gender=1)], mode)
    )
    assert result.status == "applied"
    assert "no confirmation" in result.message
    assert gramps.objects["1"]["gender"] == 1


@pytest.mark.parametrize("mode", MODES)
async def test_when_supported_still_asks_clients_that_can(mode):
    gramps = FakeGramps({"1": person("1", "Marie", "Dupont")})
    handler = answer(True)
    result = parsed(
        await call(
            make_server(gramps, "when-supported"),
            [update("1", gender=1)],
            mode,
            elicitation_handler=handler,
        )
    )
    assert result.status == "applied"
    assert len(handler.messages) == 1
    assert "no confirmation" not in result.message


@pytest.mark.parametrize("mode", MODES)
async def test_the_confirmation_outcome_is_logged_with_the_client_and_protocol(
    mode, caplog
):
    from gramps_mcp.core import logger

    gramps = FakeGramps({"1": person("1", "Marie", "Dupont")})
    logger.addHandler(caplog.handler)
    try:
        with caplog.at_level("INFO", logger="gramps_mcp"):
            await call(
                make_server(gramps),
                [update("1", gender=1)],
                mode,
                elicitation_handler=raw("cancel"),
                client_info=Implementation(name="antigravity-test", version="1.2"),
            )
    finally:
        logger.removeHandler(caplog.handler)
    line = next(
        r.getMessage() for r in caplog.records if "confirmation from" in r.getMessage()
    )
    assert "antigravity-test 1.2" in line and "cancel" in line
    assert ("2026-07-28" if mode == "auto" else "2025-11-25") in line
