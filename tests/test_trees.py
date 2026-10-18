from __future__ import annotations

import json

import httpx2
import pytest
from fastmcp import Client, FastMCP
from fastmcp.exceptions import ToolError
from test_spec import make_spec

from gramps_mcp import trees as trees_module
from gramps_mcp.trees import RoutedClient, Tree, Trees, add_tree_tools


def tree(
    name: str, seen: list[tuple[str, str, str]], member: bool | None = None
) -> Tree:
    """A tree whose Gramps records what it is asked, with its own credentials."""

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append((name, str(request.url), request.headers.get("authorization", "")))
        return httpx2.Response(200, json={"tree": name})

    client = httpx2.AsyncClient(
        base_url="http://gramps",
        headers={"Authorization": f"Bearer token-of-{name}"},
        transport=httpx2.MockTransport(handler),
    )
    return Tree(
        name, name.title(), client, None if member is None else lambda _t: member
    )


def test_a_tree_that_is_unknown_or_not_yours_reads_the_same(monkeypatch):
    monkeypatch.setattr(trees_module, "get_access_token", lambda: None)
    trees = Trees([tree("mine", [], member=True), tree("theirs", [], member=False)])
    assert trees.client("mine") is not None
    messages = []
    for name in ("theirs", "nowhere"):
        with pytest.raises(ToolError) as error:
            trees.client(name)
        messages.append(str(error.value).replace(name, "X"))
    assert messages[0] == messages[1]
    assert [t.id for t in trees.visible()] == ["mine"]


def test_a_tree_has_to_be_named_unless_there_is_only_one():
    only = Trees([tree("a", [])])
    assert only.client(None) is not None
    with pytest.raises(ToolError, match="Say which tree"):
        Trees([tree("a", []), tree("b", [])]).client(None)


async def test_a_request_goes_through_the_client_of_its_tree():
    seen: list[tuple[str, str, str]] = []
    routed = RoutedClient(Trees([tree("a", seen), tree("b", seen)]))
    request = routed.build_request(
        "POST", "/api/people/query/?tree=b&page=2", json={"where_expr": "x"}
    )
    response = await routed.send(request)
    assert response.json() == {"tree": "b"}
    # b's own credentials, the `tree` parameter removed, the body untouched
    assert seen == [
        ("b", "http://gramps/api/people/query/?page=2", "Bearer token-of-b")
    ]


async def test_the_tools_are_shared_and_each_call_names_its_tree(monkeypatch):
    monkeypatch.setenv("GRAMPS_MCP_UI", "off")
    monkeypatch.setattr(trees_module, "get_access_token", lambda: None)
    seen: list[tuple[str, str, str]] = []
    trees = Trees(
        [
            tree("a", seen, member=True),
            tree("b", seen, member=True),
            tree("c", seen, member=False),
        ]
    )
    server = FastMCP()
    add_tree_tools(trees, make_spec(), server)

    async with Client(server) as client:
        tools = {t.name: t for t in await client.list_tools()}
        assert "list_trees" in tools and "apply_changes" in tools
        assert not [n for n in tools if n.startswith("tree-")]
        for name in ("list_objects", "get_search", "apply_changes"):
            assert tools[name].input_schema["properties"]["tree"]["enum"] == [
                "a",
                "b",
                "c",
            ]
        listed = await client.call_tool("list_trees", {})
        await client.call_tool("list_objects", {"object_type": "people", "tree": "b"})
        refused = await client.call_tool(
            "list_objects", {"object_type": "people", "tree": "c"}, raise_on_error=False
        )

    assert json.dumps(listed.structured_content).count('"id"') == 2  # a and b, not c
    assert [s[0] for s in seen] == ["b"]
    assert refused.is_error and "No tree 'c' is available" in refused.content[0].text
