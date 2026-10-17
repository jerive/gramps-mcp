"""Cards that show planned changes in colour, for hosts that render MCP Apps.

Additions are green, edits orange and deletions red; each change carries the
icon of the kind of object it touches. With `apply_changes` the card is
interactive: the user can untick changes (the ones
that depend on them follow), fix a name, and apply; the button calls
`confirm_changes`, a tool the host keeps away from the model.
"""

from __future__ import annotations

import copy
import json
import os
import secrets
import time
from dataclasses import dataclass, field
from typing import Any, Literal

import httpx2
from fastmcp import Context, FastMCP, FastMCPApp
from fastmcp.apps import UI_EXTENSION_ID
from fastmcp.tools import ToolResult
from prefab_ui.actions import SetState, ShowToast
from prefab_ui.actions.mcp import CallTool, SendMessage
from prefab_ui.app import PrefabApp
from prefab_ui.components import (
    Alert,
    AlertTitle,
    Badge,
    Button,
    Card,
    CardContent,
    CardDescription,
    CardFooter,
    CardHeader,
    CardTitle,
    Checkbox,
    Column,
    Input,
    Muted,
    Row,
    Text,
)
from prefab_ui.components.control_flow import If
from prefab_ui.rx import STATE

from .changes import (
    APPLY_ANNOTATIONS,
    ApplyChangesResult,
    ChangeError,
    Operations,
    Planned,
    apply_description,
    client_label,
    confirmation_mode,
    describe_all,
    digest,
    execute,
    format_confirmation,
    make_apply,
    plan,
)
from .core import logger
from .selection import (
    EditError,
    EditField,
    check_edit,
    closure,
    direct_requirements,
    edit_fields,
    missing_requirements,
    set_path,
)
from .summary import ChangeView, Detail

TYPE_ICONS = {
    "people": "user",
    "families": "users",
    "events": "calendar",
    "places": "map-pin",
    "citations": "quote",
    "sources": "book-open",
    "repositories": "landmark",
    "media": "image",
    "notes": "sticky-note",
    "tags": "tag",
}

Variant = Literal["success", "warning", "destructive"]
OP_VARIANTS: dict[str, Variant] = {
    "create": "success",
    "update": "warning",
    "delete": "destructive",
}
OP_LABELS = {"create": "Added", "update": "Edited", "delete": "Deleted"}
OP_VERBS = {"create": "Add", "update": "Edit", "delete": "Delete"}

PENDING_TTL_SECONDS = 1800
PENDING_LIMIT = 50
APP_NAME = "Gramps changes"
CONFIRM_TOOL = "confirm_changes"

CARD_NOTE = (
    "The changes are shown to the user as a card where they can untick some, fix "
    "a name and apply. Nothing is written yet. Do not call apply_changes again and "
    "do not say they were applied: the user's decision arrives as a message."
)


UiMode = Literal["off", "auto", "always"]


def ui_mode() -> UiMode:
    """`prefab`: the card when the client says it renders MCP Apps. `prefab-always`:
    the card for every client; one that cannot render it writes nothing."""
    value = os.environ.get("GRAMPS_MCP_UI", "").strip().lower()
    if value == "prefab":
        return "auto"
    if value == "prefab-always":
        return "always"
    return "off"


def ui_enabled() -> bool:
    return ui_mode() != "off"


def _detail(detail: Detail) -> None:
    if detail.kind == "add":
        Text(f"+ {detail.label}: {detail.text}", css_class="text-sm")
    elif detail.kind == "remove":
        Text(f"− {detail.label}: {detail.text}", css_class="text-sm")
    elif detail.kind == "change":
        with Row(gap=2, align="center", css_class="flex-wrap text-sm"):
            Muted(f"{detail.label}:")
            Text(detail.before, strikethrough=True, css_class="opacity-70")
            Text("→")
            Text(detail.after, bold=True)
    else:
        Muted(f"{detail.label}: {detail.text}", css_class="text-sm")


def _change(
    view: ChangeView,
    *,
    index: int | None = None,
    fields: list[EditField] | None = None,
) -> None:
    with Alert(variant=OP_VARIANTS[view.op], icon=TYPE_ICONS.get(view.type, "file")):
        with Row(gap=2, align="center"):
            Badge(OP_VERBS[view.op], variant=OP_VARIANTS[view.op])
            AlertTitle(f"{view.noun.capitalize()}: {view.title}")
        if view.details or fields:
            with Column(gap=1, css_class="mt-1"):
                for detail in view.details:
                    _detail(detail)
                if fields and index is not None:
                    with If(getattr(STATE, f"s{index}")):
                        for edit in fields:
                            with Row(gap=2, align="center", css_class="text-sm"):
                                Muted(f"{edit.label}:")
                                Input(name=edit.key, css_class="max-w-xs")


def counts(views: list[ChangeView]) -> dict[str, int]:
    return {op: sum(1 for v in views if v.op == op) for op in OP_LABELS}


def _header(views: list[ChangeView], title: str, note: str) -> None:
    with CardHeader():
        CardTitle(title)
        CardDescription(note)
        with Row(gap=2):
            for op, number in counts(views).items():
                if number:
                    Badge(f"{number} {OP_LABELS[op].lower()}", variant=OP_VARIANTS[op])


def build_preview(views: list[ChangeView]) -> PrefabApp:
    total = len(views)
    note = f"{total} change{'s' if total != 1 else ''}, in this order"
    with Card(css_class="max-w-2xl mx-auto") as view:
        _header(views, "Changes to review", note)
        with CardContent(), Column(gap=3):
            for change in views:
                _change(change)
    return PrefabApp(view=view)


def build_error(message: str) -> PrefabApp:
    with Alert(variant="destructive", icon="circle-alert") as view:
        AlertTitle("These changes cannot be previewed")
        Text(message)
    return PrefabApp(view=view)


def _ref(name: str) -> str:
    return "{{ " + name + " }}"


def _cascade(
    i: int, requires: list[set[int]], required_by: list[set[int]]
) -> list[Any]:
    """What clicking box `i` does. `$event` is its new state.

    First the box's own value is written to the state: when a Checkbox has an
    `on_change`, Prefab no longer does it, and the box and the state drift
    apart. Then unticking it unticks every change that needs it and ticking it
    ticks every change it needs. Setting a box from an action does not fire that
    box's own handler, so the sets are the whole transitive closure."""
    actions: list[Any] = [SetState(f"s{i}", _ref("$event"))]
    actions += [
        SetState(f"s{d}", _ref(f"s{d} && $event")) for d in sorted(required_by[i])
    ]
    actions += [SetState(f"s{p}", _ref(f"s{p} || $event")) for p in sorted(requires[i])]
    return actions


def build_interactive(planned: list[Planned], token: str) -> PrefabApp:
    """The card of `apply_changes`: tick, edit, apply."""
    views = [p.view for p in planned if p.view is not None]
    fields = edit_fields([(p.op, p.type, p.body, p.current) for p in planned])
    requires, required_by = closure(
        direct_requirements([(p.op, p.body) for p in planned])
    )
    total = len(views)

    state: dict[str, Any] = {f"s{i}": True for i in range(total)}
    state.update({f.key: f.value for f in fields})
    state.update({"done": False, "outcome": ""})

    apply = CallTool(
        CONFIRM_TOOL,
        arguments={
            "token": token,
            "selection": {str(i): _ref(f"s{i}") for i in range(total)},
            "edits": {f.key: _ref(f.key) for f in fields},
        },
        on_success=[
            SetState("done", True),
            SetState("outcome", "{{ $result.message }}"),
            SendMessage("{{ $result.message }}"),
        ],
        on_error=ShowToast("The changes could not be applied.", variant="error"),
    )
    cancel = [
        SetState("done", True),
        SetState("outcome", "Cancelled. Nothing was written."),
        SendMessage("The user cancelled these changes. Nothing was written."),
    ]

    plural = "s" if total != 1 else ""
    with Card(css_class="max-w-2xl mx-auto") as view:
        _header(
            views,
            "Review the changes",
            f"{total} change{plural}. Untick what you do not want, and fix a name "
            "if it is misspelled. Changes that depend on each other are ticked and "
            "unticked together.",
        )
        with CardContent(), Column(gap=3):
            for i, change in enumerate(views):
                with Row(gap=3, align="start"):
                    Checkbox(
                        name=f"s{i}",
                        value=True,
                        on_change=_cascade(i, requires, required_by),  # type: ignore[call-arg]
                    )
                    with Column(css_class="flex-1"):
                        _change(
                            change,
                            index=i,
                            fields=[f for f in fields if f.op == i],
                        )
        with CardFooter():
            with If(STATE.done):
                Text("{{ outcome }}")
            with If(~STATE.done):  # noqa: SIM117
                with Row(gap=2, css_class="w-full justify-end"):
                    Button("Cancel", variant="outline", on_click=cancel)
                    Button("Apply", variant="success", on_click=apply)
    return PrefabApp(view=view, state=state)


def _payload(card: PrefabApp, app_name: str = APP_NAME) -> dict[str, Any]:
    """The card as the host receives it, with the button's tool call addressed
    to the tool this server lists under its own name."""
    try:
        from fastmcp.tools.base import _prefab_to_json

        payload: dict[str, Any] = _prefab_to_json(card, fastmcp_app_name=app_name)
        return payload
    except ImportError:  # pragma: no cover
        return card.to_json()


@dataclass
class Pending:
    operations: list[Any]
    digest: str
    created: float = field(default_factory=time.monotonic)


class PendingStore:
    """Changes shown in a card, waiting for the button. Kept in memory: a
    restart means the card has expired and the changes must be asked again."""

    def __init__(
        self, ttl: float = PENDING_TTL_SECONDS, limit: int = PENDING_LIMIT
    ) -> None:
        self._items: dict[str, Pending] = {}
        self._ttl = ttl
        self._limit = limit

    def _purge(self) -> None:
        now = time.monotonic()
        for token in [t for t, p in self._items.items() if now - p.created > self._ttl]:
            del self._items[token]
        while len(self._items) >= self._limit:
            del self._items[next(iter(self._items))]

    def add(self, operations: list[Any], digest_: str) -> str:
        self._purge()
        token = secrets.token_urlsafe(16)
        self._items[token] = Pending(operations, digest_)
        return token

    def get(self, token: str) -> Pending | None:
        self._purge()
        return self._items.get(token)

    def discard(self, token: str) -> None:
        self._items.pop(token, None)


def _outcome(result: ApplyChangesResult) -> ToolResult:
    return ToolResult(
        content=result.message, structured_content=result.model_dump(mode="json")
    )


def _refused(status: Literal["invalid", "not_applied"], message: str) -> ToolResult:
    return _outcome(ApplyChangesResult(status=status, message=message))


def register_ui(
    server: FastMCP,
    client: httpx2.AsyncClient,
    *,
    auth: Any = None,
    confirmation: Literal["required", "when-supported"] | None = None,
    namespace: str | None = None,
) -> None:
    """Registers `apply_changes` with its interactive card and the
    `confirm_changes` tool behind its button.

    Give each tree a distinct `namespace` (the one it is mounted under). FastMCP
    addresses the button's tool by a hash of the app's name and the tool's name,
    so apps with the same name collide once mounted together: the button's call
    is then refused as ambiguous, and nothing is applied."""
    mode = confirmation or confirmation_mode()
    ui = ui_mode()
    core = make_apply(client, mode)
    store = PendingStore()
    app_name = f"{APP_NAME}-{namespace}" if namespace else APP_NAME
    app = FastMCPApp(app_name)

    @app.ui(
        name="apply_changes",
        description=apply_description(mode, card=True),
        annotations=APPLY_ANNOTATIONS,
        auth=auth,
    )
    async def apply_changes(operations: Operations, ctx: Context) -> Any:
        advertised = ctx.client_supports_extension(UI_EXTENSION_ID)
        logger.info(
            "apply_changes from %s: advertises MCP Apps=%s, card=%s",
            client_label(ctx),
            advertised,
            advertised or ui == "always",
        )
        if not (advertised or ui == "always"):
            outcome = await core(operations, ctx)
            if isinstance(outcome, ApplyChangesResult):
                data = outcome.model_dump(mode="json")
                return ToolResult(
                    content=json.dumps(data, ensure_ascii=False),
                    structured_content=data,
                )
            return outcome
        try:
            planned = await plan(client, operations)
        except ChangeError as exc:
            return _refused("invalid", str(exc))
        token = store.add(operations, digest(format_confirmation(planned)))
        card = build_interactive(planned, token)
        header = f"{len(planned)} change(s) waiting for the user's decision."
        return ToolResult(
            content=f"{CARD_NOTE}\n\n{format_confirmation(planned, header)}",
            structured_content=_payload(card, app_name),
        )

    @app.tool(name=CONFIRM_TOOL, auth=auth)
    async def confirm_changes(
        token: str, selection: dict[str, bool], edits: dict[str, str]
    ) -> ToolResult:
        pending = store.get(token)
        if pending is None:
            return _refused(
                "not_applied",
                "This confirmation has expired, so nothing was written. Ask for the "
                "changes again.",
            )
        try:
            planned = await plan(client, pending.operations)
        except ChangeError as exc:
            store.discard(token)
            return _refused("invalid", str(exc))
        if digest(format_confirmation(planned)) != pending.digest:
            store.discard(token)
            return _refused(
                "not_applied",
                "The objects changed since the changes were shown, so nothing was "
                "written. Ask for the changes again.",
            )

        count = len(planned)
        logger.info(
            "confirm_changes received selection=%s edits=%s", selection, sorted(edits)
        )
        keys = {k for k in selection if k.isdigit() and int(k) < count}
        if keys != set(selection):
            return _refused("invalid", "The selection does not match the changes.")
        chosen = {int(k) for k in keys if selection[k] is True}
        if not chosen:
            return _refused(
                "not_applied", "No change was selected, so nothing was written."
            )

        requires, _ = closure(direct_requirements([(p.op, p.body) for p in planned]))
        gaps = missing_requirements(chosen, requires)
        if gaps:
            first, needed = gaps[0]
            return _refused(
                "invalid",
                f"Change {first + 1} needs change {needed + 1}, which is not selected.",
            )

        fields = {
            f.key: f
            for f in edit_fields([(p.op, p.type, p.body, p.current) for p in planned])
        }
        applied_edits = 0
        for key, raw in edits.items():
            edit = fields.get(key)
            if edit is None:
                return _refused("invalid", "Unknown field in the edits.")
            if edit.op not in chosen:
                continue
            try:
                value = check_edit(raw)
                if not value:
                    raise EditError(f"The {edit.label} cannot be empty.")
                if value == edit.value:
                    continue
                target = planned[edit.op]
                if target.body is None:
                    raise EditError("There is nothing to edit.")
                target.body = copy.deepcopy(target.body)
                set_path(target.body, edit.path, value)
                applied_edits += 1
            except EditError as exc:
                return _refused("invalid", str(exc))
        if applied_edits:
            await describe_all(client, planned)

        logger.info(
            "confirm_changes: %d of %d selected, %d edited",
            len(chosen),
            count,
            applied_edits,
        )
        store.discard(token)
        result = await execute(client, [planned[i] for i in sorted(chosen)])
        return _outcome(result)

    server.add_provider(app)
