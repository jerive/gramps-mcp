from __future__ import annotations

import json
from typing import Any

import pytest
from fastmcp import Client, FastMCP
from fastmcp.apps import UI_EXTENSION_ID
from fastmcp.client.elicitation import ElicitResult
from mcp.client.extension import advertise
from test_changes import FakeGramps, person

from gramps_mcp.changes import ApplyChangesResult
from gramps_mcp.ui import PendingStore, register_ui, ui_mode

JER = person("jer", "Jérôme Benoît", "Viveret", family_list=[])

SCENARIO: list[dict[str, Any]] = [
    {
        "op": "create",
        "type": "events",
        "object": {
            "handle": "e6",
            "type": "Birth",
            "description": "Naissance d'Ileana",
        },
    },
    {
        "op": "create",
        "type": "people",
        "object": {
            "handle": "p7",
            "gender": 0,
            "primary_name": {
                "first_name": "Ileana",
                "surname_list": [{"surname": "Viveret"}],
            },
            "event_ref_list": [{"ref": "e6"}],
        },
    },
    {
        "op": "create",
        "type": "families",
        "object": {
            "handle": "f8",
            "father_handle": "jer",
            "child_ref_list": [{"ref": "p7"}],
        },
    },
    {
        "op": "update",
        "type": "people",
        "handle": "jer",
        "changes": {"family_list": ["f8"]},
    },
]
ALL = {"0": True, "1": True, "2": True, "3": True}


def make(
    monkeypatch, ui: str = "prefab", confirmation: str = "required"
) -> tuple[FakeGramps, FastMCP]:
    monkeypatch.setenv("GRAMPS_MCP_UI", ui)
    gramps = FakeGramps({"jer": json.loads(json.dumps(JER))})
    server = FastMCP()
    register_ui(server, gramps.client(), confirmation=confirmation)  # type: ignore[arg-type]
    return gramps, server


def with_ui(server: FastMCP, mode: str = "auto", **kwargs: Any) -> Client:
    return Client(server, mode=mode, extensions=[advertise(UI_EXTENSION_ID)], **kwargs)  # type: ignore[arg-type]


def writes(gramps: FakeGramps) -> list[tuple[str, str]]:
    return [c for c in gramps.calls if c[0] != "GET"]


def find(node: Any, key: str, value: Any = None) -> list[Any]:
    """Every dict in the card that has `key` (equal to `value` when given)."""
    found: list[Any] = []
    if isinstance(node, dict):
        if key in node and (value is None or node[key] == value):
            found.append(node)
        for child in node.values():
            found.extend(find(child, key, value))
    elif isinstance(node, list):
        for child in node:
            found.extend(find(child, key, value))
    return found


async def show_card(server: FastMCP, operations: list[dict[str, Any]] = SCENARIO):
    async with with_ui(server) as client:
        return await client.call_tool("apply_changes", {"operations": operations})


def button_call(card: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    (call,) = find(card, "action", "toolCall")
    return call["tool"], call["arguments"]


async def confirm(
    server: FastMCP,
    card: dict[str, Any],
    selection: dict[str, bool] | None = None,
    edits: dict[str, str] | None = None,
    token: str | None = None,
) -> ApplyChangesResult:
    tool, arguments = button_call(card)
    args = {
        "token": token or arguments["token"],
        "selection": selection if selection is not None else ALL,
        "edits": edits or {},
    }
    async with with_ui(server) as client:
        result = await client.call_tool(tool, args, raise_on_error=False)
    return ApplyChangesResult.model_validate(result.structured_content)


def test_ui_mode_from_the_environment(monkeypatch):
    for value, mode in [
        ("", "off"),
        ("text", "off"),
        ("prefab", "auto"),
        (" Prefab ", "auto"),
        ("prefab-always", "always"),
    ]:
        monkeypatch.setenv("GRAMPS_MCP_UI", value)
        assert ui_mode() == mode


async def test_two_tools_and_the_confirmation_is_kept_from_the_model(monkeypatch):
    _, server = make(monkeypatch)
    async with with_ui(server) as client:
        tools = {t.name: t for t in await client.list_tools()}
    assert set(tools) == {"apply_changes", "confirm_changes"}
    assert tools["confirm_changes"].meta["ui"]["visibility"] == ["app"]
    assert tools["apply_changes"].meta["ui"]["visibility"] == ["model"]


async def test_a_client_that_renders_apps_gets_a_card_and_nothing_is_written(
    monkeypatch,
):
    gramps, server = make(monkeypatch)
    result = await show_card(server)
    assert "$prefab" in result.structured_content
    assert "Nothing is written yet" in result.content[0].text
    assert "Add person: Ileana Viveret" in result.content[0].text
    assert writes(gramps) == []


async def test_the_card_has_a_box_per_change_and_edit_fields(monkeypatch):
    _, server = make(monkeypatch)
    card = (await show_card(server)).structured_content
    assert {k: v for k, v in card["state"].items() if k.startswith("s")} == {
        f"s{k}": v for k, v in ALL.items()
    }
    assert card["state"]["ed1"] == "Ileana" and card["state"]["ed2"] == "Viveret"
    assert len(find(card, "type", "Checkbox")) == 4
    assert len(find(card, "type", "Input")) == 3
    _, arguments = button_call(card)
    assert arguments["selection"] == {str(i): f"{{{{ s{i} }}}}" for i in range(4)}
    texts = [
        n["content"] for n in find(card, "content") if isinstance(n["content"], str)
    ]
    assert not any(t.startswith(("Needs", "Skipped")) for t in texts)


async def test_boxes_follow_each_other_through_their_dependencies(monkeypatch):
    _, server = make(monkeypatch)
    card = (await show_card(server)).structured_content
    boxes = find(card, "type", "Checkbox")

    def sets(box: dict[str, Any]) -> dict[str, str]:
        return {a["key"]: a["value"] for a in box["onChange"]}

    # each box first writes its own value (Prefab does not once there is an
    # on_change), then unticking unticks what needs it, ticking ticks what it needs
    assert sets(boxes[0]) == {
        "s0": "{{ $event }}",
        "s1": "{{ s1 && $event }}",
        "s2": "{{ s2 && $event }}",
        "s3": "{{ s3 && $event }}",
    }
    assert sets(boxes[1]) == {
        "s1": "{{ $event }}",
        "s2": "{{ s2 && $event }}",
        "s3": "{{ s3 && $event }}",
        "s0": "{{ s0 || $event }}",
    }
    assert sets(boxes[3]) == {
        "s3": "{{ $event }}",
        "s0": "{{ s0 || $event }}",
        "s1": "{{ s1 || $event }}",
        "s2": "{{ s2 || $event }}",
    }


async def test_without_the_extension_it_asks_like_before(monkeypatch):
    gramps, server = make(monkeypatch)

    async def accept(message, response_type, params, context):
        return ElicitResult(action="accept", content={"confirm": True})

    async with Client(server, mode="legacy", elicitation_handler=accept) as client:
        result = await client.call_tool("apply_changes", {"operations": SCENARIO})
    assert (
        ApplyChangesResult.model_validate(result.structured_content).status == "applied"
    )
    assert len(writes(gramps)) == 4


async def test_prefab_always_shows_the_card_to_every_client(monkeypatch):
    gramps, server = make(monkeypatch, ui="prefab-always")
    async with Client(server) as client:
        result = await client.call_tool("apply_changes", {"operations": SCENARIO})
    assert "$prefab" in result.structured_content
    assert writes(gramps) == []


async def test_apply_writes_what_the_card_sends_in_order(monkeypatch):
    gramps, server = make(monkeypatch)
    card = (await show_card(server)).structured_content
    outcome = await confirm(server, card)
    assert outcome.status == "applied" and len(outcome.applied) == 4
    assert writes(gramps) == [
        ("POST", "/api/events/"),
        ("POST", "/api/people/"),
        ("POST", "/api/families/"),
        ("PUT", "/api/people/jer"),
    ]
    assert gramps.objects["jer"]["family_list"] == ["f8"]


async def test_only_the_selected_changes_are_written(monkeypatch):
    gramps, server = make(monkeypatch)
    card = (await show_card(server)).structured_content
    outcome = await confirm(
        server, card, {"0": True, "1": True, "2": False, "3": False}
    )
    assert outcome.status == "applied" and len(outcome.applied) == 2
    assert [c[1] for c in writes(gramps)] == ["/api/events/", "/api/people/"]
    assert gramps.objects["jer"]["family_list"] == []


async def test_a_change_without_what_it_needs_is_refused_and_nothing_is_written(
    monkeypatch,
):
    gramps, server = make(monkeypatch)
    card = (await show_card(server)).structured_content
    outcome = await confirm(
        server, card, {"0": False, "1": True, "2": False, "3": False}
    )
    assert outcome.status == "invalid"
    assert "Change 2 needs change 1" in outcome.message
    assert writes(gramps) == []


async def test_nothing_selected_writes_nothing(monkeypatch):
    gramps, server = make(monkeypatch)
    card = (await show_card(server)).structured_content
    outcome = await confirm(server, card, {k: False for k in ALL})
    assert (
        outcome.status == "not_applied" and "No change was selected" in outcome.message
    )
    assert writes(gramps) == []


async def test_a_fixed_name_is_what_gets_written(monkeypatch):
    gramps, server = make(monkeypatch)
    card = (await show_card(server)).structured_content
    outcome = await confirm(server, card, edits={"ed1": "  Ileanna "})
    assert outcome.status == "applied"
    assert gramps.objects["p7"]["primary_name"]["first_name"] == "Ileanna"
    assert "Ileanna" in " ".join(c.description for c in outcome.applied)


async def test_an_edit_of_an_unselected_change_is_ignored(monkeypatch):
    gramps, server = make(monkeypatch)
    card = (await show_card(server)).structured_content
    selection = {"0": True, "1": False, "2": False, "3": False}
    outcome = await confirm(server, card, selection, edits={"ed1": "Ignored"})
    assert outcome.status == "applied"
    assert "p7" not in gramps.objects


@pytest.mark.parametrize(
    ("edits", "message"),
    [
        ({"ed99": "x"}, "Unknown field"),
        ({"ed1": "bad\x00name"}, "control characters"),
        ({"ed1": "x" * 501}, "limited to 500"),
        ({"ed1": "   "}, "cannot be empty"),
    ],
)
async def test_bad_edits_are_refused_and_nothing_is_written(
    monkeypatch, edits, message
):
    gramps, server = make(monkeypatch)
    card = (await show_card(server)).structured_content
    outcome = await confirm(server, card, edits=edits)
    assert outcome.status == "invalid" and message in outcome.message
    assert writes(gramps) == []


async def test_an_object_changed_since_the_card_means_nothing_is_written(monkeypatch):
    gramps, server = make(monkeypatch)
    card = (await show_card(server)).structured_content
    gramps.objects["jer"]["primary_name"]["first_name"] = "Someone else"
    outcome = await confirm(server, card)
    assert outcome.status == "not_applied" and "changed since" in outcome.message
    assert writes(gramps) == []


async def test_a_confirmation_is_single_use(monkeypatch):
    gramps, server = make(monkeypatch)
    card = (await show_card(server)).structured_content
    assert (await confirm(server, card)).status == "applied"
    again = await confirm(server, card)
    assert again.status == "not_applied" and "expired" in again.message
    assert len(writes(gramps)) == 4


async def test_an_unknown_token_is_refused(monkeypatch):
    gramps, server = make(monkeypatch)
    card = (await show_card(server)).structured_content
    outcome = await confirm(server, card, token="made-up")
    assert outcome.status == "not_applied" and "expired" in outcome.message
    assert writes(gramps) == []


async def test_a_failure_part_way_is_undone(monkeypatch):
    gramps, server = make(monkeypatch)
    card = (await show_card(server)).structured_content
    gramps.fail = lambda method, path: (method, path) == ("PUT", "/api/people/jer")
    outcome = await confirm(server, card)
    assert outcome.status == "rolled_back"
    assert "p7" not in gramps.objects and "e6" not in gramps.objects


def test_pending_changes_expire_and_are_capped():
    store = PendingStore(ttl=0.0, limit=2)
    token = store.add([], "d")
    assert store.get(token) is None
    store = PendingStore(ttl=60, limit=2)
    first = store.add([], "a")
    store.add([], "b")
    store.add([], "c")
    assert store.get(first) is None


async def test_with_several_trees_the_button_applies_to_its_own_tree(monkeypatch):
    # Each tree is a mounted app. Apps with the same name make the button's tool
    # ambiguous, and the host then refuses the call: nothing is applied.
    monkeypatch.setenv("GRAMPS_MCP_UI", "prefab")
    trees = {
        name: FakeGramps({"jer": json.loads(json.dumps(JER))})
        for name in ("tree-a", "tree-b")
    }
    root = FastMCP()
    for name, gramps in trees.items():
        child = FastMCP()
        register_ui(
            child,
            gramps.client(),  # type: ignore[arg-type]
            confirmation="required",
            namespace=name,
        )
        root.mount(child, namespace=name)

    async with with_ui(root) as client:
        card = (
            await client.call_tool("tree-b_apply_changes", {"operations": SCENARIO})
        ).structured_content
        tool, arguments = button_call(card)
        assert tool == "tree-b_confirm_changes"
        assert tool in {t.name for t in await client.list_tools()}
        result = await client.call_tool(
            tool,
            {
                **arguments,
                "selection": {k: True for k in arguments["selection"]},
                "edits": {
                    k: str(v) for k, v in card["state"].items() if k.startswith("ed")
                },
            },
            raise_on_error=False,
        )

    assert not result.is_error
    assert writes(trees["tree-b"]) and not writes(trees["tree-a"])
