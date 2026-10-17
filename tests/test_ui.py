from __future__ import annotations

import json
from typing import Any

import httpx2
import pytest
from fastmcp import Client, FastMCP

from gramps_mcp.summary import ChangeView
from gramps_mcp.ui import build_preview, register_ui, ui_enabled

PERSON = {
    "handle": "p1",
    "gramps_id": "I1",
    "gender": 0,
    "primary_name": {"first_name": "Marie", "surname_list": [{"surname": "Dupont"}]},
}


class Gramps:
    def __init__(self) -> None:
        self.methods: list[str] = []

    def client(self) -> httpx2.AsyncClient:
        def handler(request: httpx2.Request) -> httpx2.Response:
            self.methods.append(request.method)
            handle = request.url.path.rstrip("/").split("/")[-1]
            if handle in ("p1", "p2"):
                return httpx2.Response(200, json={**PERSON, "handle": handle})
            return httpx2.Response(404, json={"error": {"message": "nope"}})

        return httpx2.AsyncClient(
            base_url="http://gramps", transport=httpx2.MockTransport(handler)
        )


OPERATIONS: list[dict[str, Any]] = [
    {
        "op": "create",
        "type": "events",
        "object": {"type": "Birth", "description": "Naissance de Marie"},
    },
    {"op": "update", "type": "people", "handle": "p1", "changes": {"gender": 1}},
    {"op": "delete", "type": "people", "handle": "p2"},
]


def test_ui_is_off_unless_asked(monkeypatch):
    monkeypatch.delenv("GRAMPS_MCP_UI", raising=False)
    assert not ui_enabled()
    monkeypatch.setenv("GRAMPS_MCP_UI", " Prefab ")
    assert ui_enabled()
    monkeypatch.setenv("GRAMPS_MCP_UI", "text")
    assert not ui_enabled()


async def call(gramps: Gramps, operations: list[dict[str, Any]]):
    server = FastMCP()
    register_ui(server, gramps.client(), confirmation="required")
    async with Client(server) as client:
        tool = next(t for t in await client.list_tools() if t.name == "preview_changes")
        result = await client.call_tool(
            "preview_changes", {"operations": operations}, raise_on_error=False
        )
    return tool, result


async def test_preview_is_a_colour_coded_card_with_a_readable_text_fallback():
    tool, result = await call(Gramps(), OPERATIONS)

    assert tool.meta["ui"]["resourceUri"].startswith("ui://")
    text = result.content[0].text
    assert text.startswith("Preview of 3 change(s). Nothing was written.")
    assert "Edit person: Marie Dupont (I1)" in text and "sex: female → male" in text

    card = json.dumps(result.structured_content)
    assert "$prefab" in card
    for variant in ("success", "warning", "destructive"):
        assert variant in card
    for icon in ("calendar", "user"):
        assert icon in card
    assert "Marie Dupont" in card


async def test_preview_never_writes():
    gramps = Gramps()
    await call(gramps, OPERATIONS)
    assert set(gramps.methods) == {"GET"}


async def test_preview_of_unknown_object_shows_an_error_card():
    _, result = await call(
        Gramps(), [{"op": "delete", "type": "people", "handle": "gone"}]
    )
    assert not result.is_error
    assert "does not exist" in result.content[0].text
    assert "cannot be previewed" in json.dumps(result.structured_content)


@pytest.mark.parametrize("count", [1, 2])
def test_card_counts_changes(count):
    views = [ChangeView("create", "people", f"Person {i}") for i in range(count)]
    card = json.dumps(build_preview(views).to_json())
    assert f"{count} change" in card and f"{count} added" in card
