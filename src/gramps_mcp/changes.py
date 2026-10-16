"""The only write tool: a list of changes, shown to the user for confirmation,
then applied.

Every generated write endpoint is excluded (see `trees.map_route`), so nothing
can change the tree without going through `apply_changes` and a confirmation.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Annotated, Any, Literal

import httpx2
from fastmcp import Context, FastMCP
from fastmcp.exceptions import ToolError
from fastmcp.server.elicitation import AcceptedElicitation
from mcp import MCPError
from mcp.types import ToolAnnotations
from pydantic import BaseModel, Field

MAX_OPERATIONS = 20
MAX_VALUE_CHARS = 60

ObjectType = Literal[
    "people",
    "families",
    "events",
    "places",
    "citations",
    "sources",
    "repositories",
    "media",
    "notes",
    "tags",
]


class CreateOperation(BaseModel):
    op: Literal["create"]
    type: ObjectType
    object: dict[str, Any] = Field(
        description="The full Gramps object to add, including its `_class`."
    )


class UpdateOperation(BaseModel):
    op: Literal["update"]
    type: ObjectType
    handle: str
    changes: dict[str, Any] = Field(
        description=(
            "Only the fields to change, as a nested object. They are merged into "
            "the current object, so everything not mentioned is kept."
        )
    )


class DeleteOperation(BaseModel):
    op: Literal["delete"]
    type: ObjectType
    handle: str


Operation = Annotated[
    CreateOperation | UpdateOperation | DeleteOperation, Field(discriminator="op")
]


class ChangeError(Exception):
    """A change cannot be planned or applied; the message is safe to show."""


@dataclass
class Planned:
    op: str
    type: str
    handle: str | None
    method: str
    path: str
    body: dict[str, Any] | None
    lines: list[str] = field(default_factory=list)


def merge_changes(current: dict[str, Any], changes: dict[str, Any]) -> dict[str, Any]:
    """Deep-merges `changes` into a copy of `current`; lists and scalars replace."""
    merged = dict(current)
    for key, value in changes.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = merge_changes(merged[key], value)
        else:
            merged[key] = value
    return merged


def leaf_diff(old: Any, new: Any, prefix: str = "") -> list[tuple[str, Any, Any]]:
    if isinstance(old, dict) and isinstance(new, dict):
        out: list[tuple[str, Any, Any]] = []
        for key in sorted(set(old) | set(new)):
            path = f"{prefix}.{key}" if prefix else key
            out.extend(leaf_diff(old.get(key), new.get(key), path))
        return out
    return [] if old == new else [(prefix, old, new)]


def short(value: Any) -> str:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    return text if len(text) <= MAX_VALUE_CHARS else text[: MAX_VALUE_CHARS - 1] + "…"


def label_of(obj: dict[str, Any]) -> str:
    name = obj.get("primary_name")
    if isinstance(name, dict):
        surnames = name.get("surname_list") or [{}]
        parts = [name.get("first_name"), surnames[0].get("surname")]
        text = " ".join(p for p in parts if p)
    else:
        text = obj.get("title") or obj.get("name") or obj.get("description") or ""
        if isinstance(text, dict):
            text = text.get("string", "")
    ident = obj.get("gramps_id") or obj.get("handle") or "?"
    return f"{text} ({ident})" if text else str(ident)


def describe(planned: Planned, current: dict[str, Any] | None) -> list[str]:
    if planned.op == "create":
        return [f"Create {planned.type}: {label_of(planned.body or {})}"]
    target = label_of(current or {})
    if planned.op == "delete":
        return [f"Delete {planned.type}: {target}"]
    lines = [f"Update {planned.type}: {target}"]
    for path, old, new in leaf_diff(current or {}, planned.body or {}):
        lines.append(f"  {path}: {short(old)} → {short(new)}")
    return lines


def format_confirmation(planned: list[Planned]) -> str:
    lines = [f"Apply these {len(planned)} change(s) to the tree?"]
    for index, item in enumerate(planned, start=1):
        first, *rest = item.lines
        lines.append(f"{index}. {first}")
        lines.extend(rest)
    return "\n".join(lines)


def _error_message(response: httpx2.Response) -> str:
    try:
        return str(response.json()["error"]["message"])
    except Exception:
        return response.text[:200] or f"HTTP {response.status_code}"


async def _current(client: httpx2.AsyncClient, type_: str, handle: str) -> dict:
    response = await client.get(f"/api/{type_}/{handle}")
    if response.status_code == 404:
        raise ChangeError(f"{type_} {handle} does not exist.")
    if response.status_code >= 400:
        raise ChangeError(
            f"Could not read {type_} {handle}: {_error_message(response)}"
        )
    return response.json()


async def plan(client: httpx2.AsyncClient, operations: list[Any]) -> list[Planned]:
    if not operations:
        raise ChangeError("No changes given.")
    if len(operations) > MAX_OPERATIONS:
        raise ChangeError(
            f"At most {MAX_OPERATIONS} changes per call, got {len(operations)}."
        )
    planned: list[Planned] = []
    for op in operations:
        if op.op == "create":
            item = Planned(
                "create", op.type, None, "POST", f"/api/{op.type}/", op.object
            )
            item.lines = describe(item, None)
        else:
            current = await _current(client, op.type, op.handle)
            path = f"/api/{op.type}/{op.handle}"
            if op.op == "update":
                body = merge_changes(current, op.changes)
                item = Planned("update", op.type, op.handle, "PUT", path, body)
            else:
                item = Planned("delete", op.type, op.handle, "DELETE", path, None)
            item.lines = describe(item, current)
        planned.append(item)
    return planned


async def execute(client: httpx2.AsyncClient, planned: list[Planned]) -> dict[str, Any]:
    """Applies the changes in order and stops at the first failure.

    Gramps has no REST call that applies a mix of creates, updates and deletes
    atomically, so a failure part-way leaves the earlier changes applied; the
    result says exactly which.
    """
    applied: list[str] = []
    for index, item in enumerate(planned):
        response = await client.request(item.method, item.path, json=item.body)
        if response.status_code >= 400:
            return {
                "status": "partly_applied" if applied else "failed",
                "applied": applied,
                "failed": {
                    "change": item.lines[0],
                    "error": _error_message(response),
                },
                "not_applied": [p.lines[0] for p in planned[index + 1 :]],
            }
        applied.append(item.lines[0])
    return {"status": "applied", "applied": applied}


def register_apply_changes(
    server: FastMCP, client: httpx2.AsyncClient, *, auth: Any = None
) -> None:
    @server.tool(
        name="apply_changes",
        description=(
            "Change this tree: add, edit or delete objects. This is the only way "
            "to write. Give the whole set of changes as a list of operations: "
            "`create` (full object), `update` (handle plus only the changed "
            "fields) or `delete` (handle). Look up handles with the read tools "
            "first. The user is shown every change and must confirm before "
            f"anything is written. At most {MAX_OPERATIONS} operations per call."
        ),
        annotations=ToolAnnotations(
            readOnlyHint=False, destructiveHint=True, openWorldHint=False
        ),
        auth=auth,
    )
    async def apply_changes(
        operations: list[Operation], ctx: Context
    ) -> dict[str, Any]:
        try:
            planned = await plan(client, operations)
        except ChangeError as exc:
            return {"status": "invalid", "error": str(exc)}

        try:
            answer = await ctx.elicit(format_confirmation(planned), response_type=bool)
        except (MCPError, ToolError):
            return {
                "status": "not_applied",
                "reason": (
                    "This client cannot ask the user for confirmation, so nothing "
                    "was written."
                ),
            }
        if not isinstance(answer, AcceptedElicitation) or answer.data is not True:
            return {"status": "declined", "reason": "The user did not confirm."}

        return await execute(client, planned)
