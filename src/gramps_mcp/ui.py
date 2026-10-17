"""A card that shows planned changes in colour, for hosts that render MCP Apps.

Additions are green, edits orange and deletions red; each change carries the
icon of the kind of object it touches.
"""

from __future__ import annotations

import os
from typing import Any, Literal

import httpx2
from fastmcp import FastMCP, FastMCPApp
from fastmcp.tools import ToolResult
from prefab_ui.app import PrefabApp
from prefab_ui.components import (
    Alert,
    AlertDescription,
    AlertTitle,
    Badge,
    Card,
    CardContent,
    CardDescription,
    CardHeader,
    CardTitle,
    Column,
    Muted,
    Row,
    Text,
)

from .changes import (
    MAX_OPERATIONS,
    ChangeError,
    Operations,
    format_confirmation,
    plan,
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


def ui_enabled() -> bool:
    return os.environ.get("GRAMPS_MCP_UI", "").strip().lower() == "prefab"


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


def _change(view: ChangeView) -> None:
    with Alert(variant=OP_VARIANTS[view.op], icon=TYPE_ICONS.get(view.type, "file")):
        with Row(gap=2, align="center"):
            Badge(OP_VERBS[view.op], variant=OP_VARIANTS[view.op])
            AlertTitle(f"{view.noun.capitalize()}: {view.title}")
        if view.details:
            with Column(gap=1, css_class="mt-1"):
                for detail in view.details:
                    _detail(detail)


def counts(views: list[ChangeView]) -> dict[str, int]:
    return {op: sum(1 for v in views if v.op == op) for op in OP_LABELS}


def build_preview(views: list[ChangeView]) -> PrefabApp:
    total = len(views)
    with Card(css_class="max-w-2xl mx-auto") as view:
        with CardHeader():
            CardTitle("Changes to review")
            CardDescription(f"{total} change{'s' if total != 1 else ''}, in this order")
            with Row(gap=2):
                for op, number in counts(views).items():
                    if number:
                        Badge(
                            f"{number} {OP_LABELS[op].lower()}", variant=OP_VARIANTS[op]
                        )
        with CardContent(), Column(gap=3):
            for change in views:
                _change(change)
    return PrefabApp(view=view)


def build_error(message: str) -> PrefabApp:
    with Alert(variant="destructive", icon="circle-alert") as view:
        AlertTitle("These changes cannot be previewed")
        AlertDescription(message)
    return PrefabApp(view=view)


def register_preview(
    server: FastMCP, client: httpx2.AsyncClient, *, auth: Any = None
) -> None:
    """Adds `preview_changes`: the same plan as `apply_changes`, shown as a card,
    that writes nothing."""
    app = FastMCPApp("Gramps changes")

    @app.ui(
        name="preview_changes",
        description=(
            "Show, as a colour-coded card, what a set of changes would do to this "
            "tree, without writing anything. Takes the same operations as "
            f"`apply_changes` (at most {MAX_OPERATIONS})."
        ),
        auth=auth,
    )
    async def preview_changes(operations: Operations) -> ToolResult:
        try:
            planned = await plan(client, operations)
        except ChangeError as exc:
            return ToolResult(
                content=str(exc), structured_content=build_error(str(exc)).to_json()
            )
        card = build_preview([p.view for p in planned if p.view is not None])
        return ToolResult(
            content=format_confirmation(
                planned,
                f"Preview of {len(planned)} change(s). Nothing was written.",
            ),
            structured_content=card.to_json(),
        )

    server.add_provider(app)
