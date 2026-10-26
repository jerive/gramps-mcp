"""The write surface as an outside caller sees it.

Everything here imports from `gramps_mcp`, never from `gramps_mcp.changes`, and
uses no test helper from the rest of the suite: moving a name out of `changes`
or changing what these functions promise has to break this file. See
docs/reference/python-api.md.
"""

from __future__ import annotations

import json
from copy import deepcopy
from typing import Any

import httpx2
import pytest
from pydantic import TypeAdapter, ValidationError

from gramps_mcp import (
    MAX_OPERATIONS,
    ApplyChangesResult,
    ChangeError,
    ChangeSummary,
    CreateOperation,
    DeleteOperation,
    Failure,
    Operations,
    Planned,
    UpdateOperation,
    execute,
    format_confirmation,
    plan,
    rollback,
)

PUBLIC = {
    "MAX_OPERATIONS",
    "ApplyChangesResult",
    "ChangeError",
    "ChangeSummary",
    "CreateOperation",
    "DeleteOperation",
    "Failure",
    "Operations",
    "Planned",
    "UpdateOperation",
    "__version__",
    "execute",
    "format_confirmation",
    "plan",
    "rollback",
}

OPERATIONS = TypeAdapter(Operations)


class Gramps:
    """A Gramps Web API that answers from a dict and keeps the transactions it
    returned, which is what a caller needs to undo a set that succeeded."""

    def __init__(self, objects: dict[str, dict[str, Any]]) -> None:
        self.objects = objects
        self.transactions: list[list[dict[str, Any]]] = []
        self.refuse_writes: set[str] = set()

    def handle(self, request: httpx2.Request) -> httpx2.Response:
        method, path = request.method, request.url.path
        if path == "/api/transactions/":
            return self._undo(json.loads(request.content))
        if method != "GET" and path in self.refuse_writes:
            return httpx2.Response(400, json={"error": {"message": "refused"}})
        _, type_, *rest = path.strip("/").split("/")
        handle = rest[0] if rest else None
        if method == "GET":
            if handle in self.objects:
                return httpx2.Response(200, json=self.objects[handle])
            return httpx2.Response(404, json={"error": {"message": "no such object"}})
        if method == "PUT":
            assert handle is not None
            old, new = self.objects[handle], json.loads(request.content)
            self.objects[handle] = new
            return self._transaction("update", handle, old, new)
        if method == "DELETE":
            assert handle is not None
            return self._transaction("delete", handle, self.objects.pop(handle), None)
        body = {**json.loads(request.content), "handle": f"new-{type_}"}
        self.objects[body["handle"]] = body
        return self._transaction("add", body["handle"], None, body)

    def _transaction(
        self, kind: str, handle: str, old: Any, new: Any
    ) -> httpx2.Response:
        cls = (new or old)["_class"]
        payload = [
            {"type": kind, "_class": cls, "handle": handle, "old": old, "new": new}
        ]
        self.transactions.append(payload)
        return httpx2.Response(200, json=payload)

    def _undo(self, payload: list[dict[str, Any]]) -> httpx2.Response:
        state = deepcopy(self.objects)
        for item in reversed(payload):
            if state.get(item["handle"]) != item["new"]:
                return httpx2.Response(
                    400, json={"error": {"message": "Object has changed"}}
                )
            if item["old"] is None:
                state.pop(item["handle"])
            else:
                state[item["handle"]] = item["old"]
        self.objects = state
        return httpx2.Response(200, json=[])

    def client(self) -> httpx2.AsyncClient:
        return httpx2.AsyncClient(
            base_url="http://gramps", transport=httpx2.MockTransport(self.handle)
        )


def person(handle: str, first: str) -> dict[str, Any]:
    return {
        "_class": "Person",
        "handle": handle,
        "gramps_id": f"I{handle}",
        "gender": 1,
        "primary_name": {
            "_class": "Name",
            "first_name": first,
            "surname_list": [{"_class": "Surname", "surname": "Dupont"}],
        },
    }


def test_the_package_promises_exactly_this_surface():
    import gramps_mcp

    assert set(gramps_mcp.__all__) == PUBLIC
    for name in gramps_mcp.__all__:
        assert getattr(gramps_mcp, name) is not None, name


def test_the_package_ships_py_typed():
    from importlib.resources import files

    assert files("gramps_mcp").joinpath("py.typed").is_file()


def test_the_limits_a_caller_has_to_respect():
    assert MAX_OPERATIONS == 20
    with pytest.raises(ValidationError):
        OPERATIONS.validate_python([])
    with pytest.raises(ValidationError, match="appears twice"):
        OPERATIONS.validate_python(
            [
                {"op": "update", "type": "people", "handle": "1", "changes": {"a": 1}},
                {"op": "update", "type": "people", "handle": "1", "changes": {"b": 2}},
            ]
        )
    # the invariant the reference page rests on: no operation writes bytes
    with pytest.raises(ValidationError, match="exists only with its file"):
        OPERATIONS.validate_python([{"op": "create", "type": "media", "object": {}}])


def test_validation_builds_the_three_operation_types():
    create, update, delete = OPERATIONS.validate_python(
        [
            {"op": "create", "type": "notes", "object": {}},
            {"op": "update", "type": "people", "handle": "1", "changes": {"gender": 0}},
            {"op": "delete", "type": "events", "handle": "e"},
        ]
    )
    assert isinstance(create, CreateOperation)
    assert isinstance(update, UpdateOperation)
    assert isinstance(delete, DeleteOperation)


async def test_a_caller_can_plan_show_apply_and_then_undo():
    gramps = Gramps({"1": person("1", "Marie")})
    client = gramps.client()
    operations = OPERATIONS.validate_python(
        [{"op": "update", "type": "people", "handle": "1", "changes": {"gender": 0}}]
    )

    planned = await plan(client, operations)
    assert isinstance(planned[0], Planned)
    assert planned[0].lines  # what a caller renders
    shown = format_confirmation(planned)
    assert "Marie Dupont" in shown

    result = await execute(client, planned)
    assert isinstance(result, ApplyChangesResult)
    assert result.status == "applied"
    assert isinstance(result.applied[0], ChangeSummary)
    assert gramps.objects["1"]["gender"] == 0

    # execute does not hand the transactions back, so the caller kept them
    assert await rollback(client, gramps.transactions) is None
    assert gramps.objects["1"]["gender"] == 1


async def test_a_created_object_reports_the_handle_it_was_given():
    gramps = Gramps({})
    result = await execute(
        gramps.client(),
        await plan(
            gramps.client(),
            OPERATIONS.validate_python(
                [{"op": "create", "type": "notes", "object": {}}]
            ),
        ),
    )
    assert result.status == "applied"
    assert result.applied[0].handle == "new-notes"


async def test_a_failed_change_undoes_the_earlier_ones():
    gramps = Gramps({"1": person("1", "Marie"), "2": person("2", "Jean")})
    gramps.refuse_writes = {"/api/people/2"}
    client = gramps.client()
    operations = OPERATIONS.validate_python(
        [
            {"op": "update", "type": "people", "handle": "1", "changes": {"gender": 0}},
            {"op": "update", "type": "people", "handle": "2", "changes": {"gender": 0}},
        ]
    )
    planned = await plan(client, operations)

    result = await execute(client, planned)
    assert result.status == "rolled_back"
    assert isinstance(result.failed, Failure)
    assert result.failed.error == "refused"
    assert gramps.objects["1"]["gender"] == 1


async def test_planning_an_unknown_handle_raises_change_error():
    gramps = Gramps({})
    with pytest.raises(ChangeError, match="does not exist"):
        await plan(
            gramps.client(),
            OPERATIONS.validate_python(
                [{"op": "delete", "type": "people", "handle": "nope"}]
            ),
        )
