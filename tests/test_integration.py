"""End-to-end test against a real, running Gramps Web API instance.

Unlike test_core.py, this doesn't mock anything: it builds the actual MCP
server from GRAMPS_BACKEND_URL/GRAMPS_MCP_TREES/GRAMPS_MCP_TREE_*_* env vars
and calls a real generated tool over an in-process MCP client. See
.github/workflows/ci.yml's `integration` job for how that instance is
bootstrapped, and docs/tutorial.md for the equivalent steps by hand.

Skipped unless GRAMPS_BACKEND_URL is set, so the regular `test` job (which
has no Gramps Web API instance to talk to) doesn't try to run this.
"""

from __future__ import annotations

import json
import os

import pytest
from fastmcp import Client

pytestmark = pytest.mark.skipif(
    not os.environ.get("GRAMPS_BACKEND_URL"),
    reason="requires a live Gramps Web API instance (set GRAMPS_BACKEND_URL)",
)


async def test_mounted_tree_exposes_the_read_tools():
    from gramps_mcp.server import mcp

    async with Client(mcp) as client:
        tools = await client.list_tools()
        tool_names = {t.name for t in tools}

    for tool in ("list_objects", "get_object", "query_objects", "get_search"):
        assert any(n.endswith(f"_{tool}") for n in tool_names), sorted(tool_names)


async def test_the_generic_tools_return_real_data():
    from gramps_mcp.server import mcp

    async with Client(mcp) as client:
        tools = {t.name.split("_", 1)[1]: t.name for t in await client.list_tools()}
        listed = await client.call_tool(
            tools["list_objects"], {"object_type": "people", "page": 1, "pagesize": 2}
        )
        people = json.loads(listed.content[0].text)
        if isinstance(people, dict):  # a list may come wrapped as {"result": [...]}
            people = people["result"]
        handle = people[0]["handle"]
        one = await client.call_tool(
            tools["get_object"], {"object_type": "people", "handle": handle}
        )
        found = await client.call_tool(
            tools["query_objects"],
            {
                "object_type": "people",
                "where_expr": "gender == Person.FEMALE",
                "limit": 2,
            },
        )

    for result in (listed, one, found):
        assert result.content, "tool call returned no content"
        assert not result.is_error, f"tool call failed: {result.content}"


async def test_goql_guide_resource_is_published():
    from gramps_mcp.server import mcp

    async with Client(mcp) as client:
        uris = {str(r.uri) for r in await client.list_resources()}
        contents = await client.read_resource("gramps://goql-spec")

    assert "gramps://goql-spec" in uris
    assert "gramps://gql-spec" not in uris
    assert "where_expr" in contents[0].text


async def test_writes_only_go_through_apply_changes():
    from gramps_mcp.server import mcp

    async with Client(mcp) as client:
        names = {t.name for t in await client.list_tools()}

    assert any(n.endswith("_apply_changes") for n in names)
    writes = [
        n
        for n in names
        if any(f"_{verb}_" in n for verb in ("put", "delete", "post", "patch"))
        and not n.endswith("_query")
    ]
    assert not writes, f"generated write tools still exposed: {sorted(writes)}"
