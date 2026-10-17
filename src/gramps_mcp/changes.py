"""The only write tool: a list of changes, shown to the user for confirmation,
then applied.

Every generated write endpoint is excluded (see `trees.map_route`), so nothing
can change the tree without going through `apply_changes` and a confirmation.
"""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Annotated, Any, Literal

import httpx2
from fastmcp import Context, FastMCP
from fastmcp.exceptions import ToolError
from fastmcp.server.elicitation import AcceptedElicitation, DeclinedElicitation
from mcp import MCPError
from mcp.types import (
    ClientCapabilities,
    ElicitationCapability,
    ElicitRequest,
    ElicitRequestFormParams,
    ElicitResult,
    FormElicitationCapability,
    InputRequiredResult,
    ToolAnnotations,
)
from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    model_validator,
)

from .core import logger
from .summary import ChangeView, Labeler, view_operation

MAX_OPERATIONS = 20
MAX_VALUE_CHARS = 60
MAX_CONFIRMATIONS = 3
CONFIRMATION_MODES = ("required", "when-supported")
CONFIRM_KEY = "confirm"
# From this protocol version on, a server cannot send a question in the middle
# of a call: it returns an `InputRequiredResult` and the client calls again.
MODERN_PROTOCOL_FROM = "2026-07-28"

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

OBJECT_CLASSES: dict[ObjectType, str] = {
    "people": "Person",
    "families": "Family",
    "events": "Event",
    "places": "Place",
    "citations": "Citation",
    "sources": "Source",
    "repositories": "Repository",
    "media": "Media",
    "notes": "Note",
    "tags": "Tag",
}

# Computed by the server, never written by a client.
READ_ONLY_FIELDS = frozenset({"backlinks", "extended", "profile"})
# Cannot be changed on an existing object.
IMMUTABLE_FIELDS = READ_ONLY_FIELDS | {"handle", "_class", "change"}

# Gramps handles are at most 50 characters.
Handle = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=50)
]
JsonObject = dict[str, Any]
Op = Literal["create", "update", "delete"]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


# What the model is shown for the `object` of a `create`, by type: the schema of
# the backend's spec (see `spec.object_schemas`). Set once at startup, before the
# tools are registered; empty, the object is just an object.
OBJECT_SCHEMAS: dict[str, JsonObject] = {}
PLACE_HINT = (
    "For a place, give `lat` and `long` (decimal degrees as text, e.g. "
    '"-23.5505") along with its `name`.'
)


def set_object_schemas(schemas: dict[str, JsonObject]) -> None:
    OBJECT_SCHEMAS.clear()
    OBJECT_SCHEMAS.update(schemas)


def _show_object_schemas(schema: JsonObject) -> None:
    if not OBJECT_SCHEMAS:
        return
    schema["properties"]["object"] = {
        **schema["properties"]["object"],
        "anyOf": list(OBJECT_SCHEMAS.values()),
    }


class CreateOperation(_Strict):
    model_config = ConfigDict(extra="forbid", json_schema_extra=_show_object_schemas)

    op: Literal["create"]
    type: ObjectType
    object: JsonObject = Field(
        description=(
            "The full Gramps object to add; its fields are those of the schema "
            "named like `type`. `_class` may be left out; if given it must match "
            "`type`. `handle` may be left out: one is assigned. " + PLACE_HINT
        )
    )

    @model_validator(mode="after")
    def _check_object(self) -> CreateOperation:
        expected = OBJECT_CLASSES[self.type]
        given = self.object.get("_class", expected)
        if given != expected:
            raise ValueError(
                f"`type` {self.type!r} needs `_class` {expected!r}, got {given!r}"
            )
        forbidden = sorted(READ_ONLY_FIELDS & self.object.keys())
        if forbidden:
            raise ValueError(f"read-only fields cannot be set: {forbidden}")
        self.object = {**self.object, "_class": expected}
        return self


class UpdateOperation(_Strict):
    op: Literal["update"]
    type: ObjectType
    handle: Handle
    changes: JsonObject = Field(
        min_length=1,
        description=(
            "Only the fields to change, as a nested object. They are merged into "
            "the current object, so everything not mentioned is kept. A list "
            "replaces the whole list. `handle`, `_class` and read-only fields "
            "cannot be changed."
        ),
    )

    @model_validator(mode="after")
    def _check_changes(self) -> UpdateOperation:
        forbidden = sorted(IMMUTABLE_FIELDS & self.changes.keys())
        if forbidden:
            raise ValueError(f"these fields cannot be changed: {forbidden}")
        return self


class DeleteOperation(_Strict):
    op: Literal["delete"]
    type: ObjectType
    handle: Handle


Operation = Annotated[
    CreateOperation | UpdateOperation | DeleteOperation, Field(discriminator="op")
]


def _reject_duplicates(operations: list[Operation]) -> list[Operation]:
    seen: set[tuple[str, str]] = set()
    for operation in operations:
        if isinstance(operation, CreateOperation):
            continue
        key = (operation.type, operation.handle)
        if key in seen:
            raise ValueError(
                f"{operation.type} {operation.handle} appears twice: merge the "
                "changes into one operation"
            )
        seen.add(key)
    return operations


Operations = Annotated[
    list[Operation],
    Field(
        min_length=1,
        max_length=MAX_OPERATIONS,
        description="The changes to apply, in order, as one set.",
    ),
    AfterValidator(_reject_duplicates),
]


class ChangeSummary(BaseModel):
    op: Op
    type: ObjectType
    handle: Handle | None = Field(
        description="The object's handle; for a created object, the one assigned."
    )
    description: str


class Failure(BaseModel):
    change: ChangeSummary
    error: str


class ApplyChangesResult(BaseModel):
    status: Literal[
        "applied",
        "declined",
        "not_applied",
        "invalid",
        "rolled_back",
        "partly_applied",
    ] = Field(
        description=(
            "applied: everything was written. declined: the user said no. "
            "not_applied: nothing was written (see `message`). invalid: the "
            "changes could not be planned. rolled_back: a change failed and the "
            "earlier ones were undone. partly_applied: a change failed and the "
            "earlier ones could not be undone."
        )
    )
    message: str
    applied: list[ChangeSummary] = Field(
        default_factory=list, description="Changes currently in the tree."
    )
    rolled_back: list[ChangeSummary] = Field(
        default_factory=list, description="Changes that were written, then undone."
    )
    failed: Failure | None = None
    not_applied: list[ChangeSummary] = Field(
        default_factory=list, description="Changes after the failure, never tried."
    )


class ChangeError(Exception):
    """A change cannot be planned or applied; the message is safe to show."""


@dataclass
class Planned:
    op: Op
    type: ObjectType
    handle: str | None
    method: Literal["POST", "PUT", "DELETE"]
    path: str
    body: JsonObject | None
    current: JsonObject | None = None
    view: ChangeView | None = None

    @property
    def lines(self) -> list[str]:
        return self.view.lines() if self.view is not None else []

    def summary(self, handle: str | None = None) -> ChangeSummary:
        return ChangeSummary(
            op=self.op,
            type=self.type,
            handle=handle or self.handle,
            description=self.lines[0],
        )


def merge_changes(current: JsonObject, changes: JsonObject) -> JsonObject:
    """Deep-merges `changes` into a copy of `current`; lists and scalars replace."""
    merged = dict(current)
    for key, value in changes.items():
        existing = merged.get(key)
        if isinstance(value, dict) and isinstance(existing, dict):
            merged[key] = merge_changes(existing, value)
        else:
            merged[key] = value
    return merged


def format_confirmation(planned: list[Planned], header: str | None = None) -> str:
    """The summary as text that is also valid Markdown: clients that render
    Markdown show a list with the details nested under each change."""
    lines = [header or f"Apply these {len(planned)} change(s) to the tree?", ""]
    for index, item in enumerate(planned, start=1):
        first, *rest = item.lines
        lines.append(f"{index}. {first}")
        lines.extend(f"    - {line}" for line in rest)
    return "\n".join(lines)


def _error_message(response: httpx2.Response) -> str:
    try:
        return str(response.json()["error"]["message"])
    except Exception:
        return response.text[:200] or f"HTTP {response.status_code}"


async def _current(
    client: httpx2.AsyncClient, type_: ObjectType, handle: str
) -> JsonObject:
    response = await client.get(f"/api/{type_}/{handle}")
    if response.status_code == 404:
        raise ChangeError(f"{type_} {handle} does not exist.")
    if response.status_code >= 400:
        raise ChangeError(
            f"Could not read {type_} {handle}: {_error_message(response)}"
        )
    current: JsonObject = response.json()
    return current


async def plan(
    client: httpx2.AsyncClient, operations: list[Operation]
) -> list[Planned]:
    planned: list[Planned] = []
    for op in operations:
        if isinstance(op, CreateOperation):
            planned.append(
                Planned("create", op.type, None, "POST", f"/api/{op.type}/", op.object)
            )
            continue
        current = await _current(client, op.type, op.handle)
        path = f"/api/{op.type}/{op.handle}"
        if isinstance(op, UpdateOperation):
            body = merge_changes(current, op.changes)
            planned.append(
                Planned("update", op.type, op.handle, "PUT", path, body, current)
            )
        else:
            planned.append(
                Planned("delete", op.type, op.handle, "DELETE", path, None, current)
            )

    await describe_all(client, planned)
    return planned


async def describe_all(client: httpx2.AsyncClient, planned: list[Planned]) -> None:
    """(Re)builds the readable view of every planned change from its body."""
    created = {
        str(item.body["handle"]): (item.type, item.body)
        for item in planned
        if item.op == "create" and item.body and item.body.get("handle")
    }
    labeler = Labeler(client, created)
    for item in planned:
        item.view = await view_operation(
            labeler, item.op, item.type, item.body, item.current
        )


def _created_handle(transaction: list[JsonObject], class_name: str) -> str | None:
    for item in transaction:
        if item.get("type") == "add" and item.get("_class") == class_name:
            handle = item.get("handle")
            return handle if isinstance(handle, str) else None
    return None


async def rollback(
    client: httpx2.AsyncClient, transactions: list[list[JsonObject]]
) -> str | None:
    """Undoes applied changes in one call; returns an error message on failure.

    Gramps replays the inverse of the transactions the write endpoints
    returned, all or nothing, and refuses if an object changed since.
    """
    payload = [item for transaction in transactions for item in transaction]
    response = await client.post(
        "/api/transactions/",
        params={"undo": "true", "message": "Undo failed changes"},
        json=payload,
    )
    return None if response.status_code < 400 else _error_message(response)


async def execute(
    client: httpx2.AsyncClient, planned: list[Planned]
) -> ApplyChangesResult:
    """Applies the changes in order; if one fails, undoes the earlier ones."""
    applied: list[ChangeSummary] = []
    transactions: list[list[JsonObject]] = []
    for index, item in enumerate(planned):
        response = await client.request(item.method, item.path, json=item.body)
        if response.status_code < 400:
            transaction: list[JsonObject] = response.json()
            transactions.append(transaction)
            handle = (
                _created_handle(transaction, OBJECT_CLASSES[item.type])
                if item.op == "create"
                else None
            )
            applied.append(item.summary(handle))
            continue

        failure = Failure(change=item.summary(), error=_error_message(response))
        untried = [p.summary() for p in planned[index + 1 :]]
        if not applied:
            return ApplyChangesResult(
                status="not_applied",
                message="The first change failed; nothing was written.",
                failed=failure,
                not_applied=untried,
            )
        undo_error = await rollback(client, transactions)
        if undo_error is None:
            return ApplyChangesResult(
                status="rolled_back",
                message="A change failed, so the earlier ones were undone.",
                rolled_back=applied,
                failed=failure,
                not_applied=untried,
            )
        return ApplyChangesResult(
            status="partly_applied",
            message=(
                "A change failed and the earlier ones could not be undone: "
                f"{undo_error}"
            ),
            applied=applied,
            failed=failure,
            not_applied=untried,
        )
    return ApplyChangesResult(
        status="applied", message=f"Applied {len(applied)} change(s).", applied=applied
    )


class Confirmation(BaseModel):
    confirm: bool = Field(default=True, title="Apply these changes")


Decision = Literal["accepted", "declined", "unsupported"]


@dataclass(frozen=True)
class Answer:
    decision: Decision
    detail: str


UNSUPPORTED = Answer("unsupported", "the client cannot ask for confirmation")


def confirmation_mode() -> Literal["required", "when-supported"]:
    value = os.environ.get("GRAMPS_MCP_CONFIRMATION", "required").strip().lower()
    if value == "required":
        return "required"
    if value == "when-supported":
        return "when-supported"
    raise ValueError(
        f"GRAMPS_MCP_CONFIRMATION must be one of {', '.join(CONFIRMATION_MODES)}, "
        f"got {value!r}"
    )


def digest(message: str) -> str:
    return hashlib.sha256(message.encode()).hexdigest()[:32]


def _is_modern(ctx: Context) -> bool:
    request = ctx.request_context
    return request is not None and request.protocol_version >= MODERN_PROTOCOL_FROM


def client_label(ctx: Context) -> str:
    params = getattr(ctx.session, "client_params", None)
    info = getattr(params, "client_info", None)
    name = getattr(info, "name", None) or "unknown client"
    version = getattr(info, "version", None)
    request = ctx.request_context
    protocol = request.protocol_version if request is not None else "?"
    return f"{name} {version or ''} (protocol {protocol})".replace("  ", " ")


def _interpret(action: str, content: dict[str, Any] | None) -> Answer:
    if action == "accept":
        if (content or {}).get("confirm") is True:
            return Answer("accepted", "accept")
        return Answer(
            "declined", f"accept without confirming (content: {json.dumps(content)})"
        )
    return Answer("declined", action)


async def _ask_legacy(ctx: Context, message: str) -> Answer:
    try:
        answer = await ctx.elicit(message, response_type=Confirmation)
    except (MCPError, ToolError):
        return UNSUPPORTED
    if isinstance(answer, AcceptedElicitation):
        # The model's default would turn an empty answer into a confirmation.
        return _interpret("accept", answer.data.model_dump(exclude_unset=True))
    return _interpret(
        "decline" if isinstance(answer, DeclinedElicitation) else "cancel", None
    )


def _ask_modern(ctx: Context, message: str) -> Answer | InputRequiredResult:
    """Asks through the multi-round-trip flow.

    The state sent with the question is a digest of what the user is shown, and
    it only counts as a confirmation if the summary rebuilt on the retry has the
    same digest. If an object changed or the arguments differ, the user is asked
    again about what would now be written.
    """
    can_ask = ctx.session.check_client_capability(
        ClientCapabilities(
            elicitation=ElicitationCapability(form=FormElicitationCapability())
        )
    )
    if not can_ask:
        return UNSUPPORTED
    state_digest = digest(message)
    responses = ctx.input_responses
    if responses is not None and ctx.request_state == state_digest:
        answer = responses.get(CONFIRM_KEY)
        if isinstance(answer, ElicitResult):
            return _interpret(answer.action, answer.content)
    return InputRequiredResult(
        input_requests={
            CONFIRM_KEY: ElicitRequest(
                params=ElicitRequestFormParams(
                    message=message,
                    requested_schema=Confirmation.model_json_schema(),
                )
            )
        },
        request_state=state_digest,
    )


def _declined_message(detail: str) -> str:
    if detail == "decline":
        return "The user declined."
    if detail == "cancel":
        return "The user dismissed the confirmation (cancel)."
    return f"The client did not confirm: {detail}."


def apply_description(mode: Literal["required", "when-supported"], card: bool) -> str:
    if card:
        asks = (
            "The user is shown the changes as a card where they can untick "
            "changes, fix a name and apply; nothing is written until they do. "
            "If the client cannot show the card, it asks for a confirmation "
            "instead."
        )
    elif mode == "required":
        asks = (
            "The user is shown every change and must confirm before anything is "
            "written."
        )
    else:
        asks = (
            "If the client can ask the user, they are shown every change and "
            "must confirm; a client that cannot ask has the changes applied "
            "directly."
        )
    return (
        "Change this tree: add, edit or delete objects. This is the only way "
        "to write. Give the whole set of changes as a list of operations: "
        "`create` (full object), `update` (handle plus only the changed "
        "fields) or `delete` (handle). Look up handles with the read tools "
        f"first. {asks} If a change fails, the earlier ones are undone. At most "
        f"{MAX_OPERATIONS} operations per call."
    )


APPLY_ANNOTATIONS = ToolAnnotations(
    read_only_hint=False,
    destructive_hint=True,
    idempotent_hint=False,
    open_world_hint=False,
)
APPLY_META = {"anthropic/requiresUserInteraction": True}

ApplyOutcome = ApplyChangesResult | InputRequiredResult


def make_apply(
    client: httpx2.AsyncClient, mode: Literal["required", "when-supported"]
) -> Callable[[Operations, Context], Awaitable[ApplyOutcome]]:
    """The confirmation flow of `apply_changes`: ask the user, then write."""

    async def apply(operations: Operations, ctx: Context) -> ApplyOutcome:
        for _ in range(MAX_CONFIRMATIONS):
            try:
                planned = await plan(client, operations)
            except ChangeError as exc:
                return ApplyChangesResult(status="invalid", message=str(exc))
            message = format_confirmation(planned)

            if _is_modern(ctx):
                answer = _ask_modern(ctx, message)
                if isinstance(answer, InputRequiredResult):
                    return answer
            else:
                answer = await _ask_legacy(ctx, message)
                if answer.decision == "accepted":
                    try:
                        changed = (
                            format_confirmation(await plan(client, operations))
                            != message
                        )
                    except ChangeError as exc:
                        return ApplyChangesResult(status="invalid", message=str(exc))
                    if changed:
                        continue

            logger.info(
                "apply_changes confirmation from %s: %s",
                client_label(ctx),
                answer.detail,
            )
            if answer.decision == "declined":
                return ApplyChangesResult(
                    status="declined", message=_declined_message(answer.detail)
                )
            if answer.decision == "unsupported":
                if mode == "required":
                    return ApplyChangesResult(
                        status="not_applied",
                        message=(
                            "This client cannot ask the user for confirmation, so "
                            "nothing was written."
                        ),
                    )
                logger.warning(
                    "applying without confirmation: %s cannot ask for one",
                    client_label(ctx),
                )
                result = await execute(client, planned)
                result.message += " (no confirmation: the client cannot ask for one)"
                return result
            return await execute(client, planned)

        return ApplyChangesResult(
            status="not_applied",
            message=(
                "The objects kept changing while waiting for confirmation, so "
                "nothing was written. Try again."
            ),
        )

    return apply


def register_apply_changes(
    server: FastMCP,
    client: httpx2.AsyncClient,
    *,
    auth: Any = None,
    confirmation: Literal["required", "when-supported"] | None = None,
) -> None:
    mode = confirmation or confirmation_mode()
    apply = make_apply(client, mode)

    @server.tool(
        name="apply_changes",
        description=apply_description(mode, card=False),
        annotations=APPLY_ANNOTATIONS,
        meta=APPLY_META,
        auth=auth,
    )
    async def apply_changes(
        operations: Operations, ctx: Context
    ) -> ApplyChangesResult | InputRequiredResult:
        return await apply(operations, ctx)
