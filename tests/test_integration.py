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

import os

import pytest
from fastmcp import Client

pytestmark = pytest.mark.skipif(
    not os.environ.get("GRAMPS_BACKEND_URL"),
    reason="requires a live Gramps Web API instance (set GRAMPS_BACKEND_URL)",
)


async def test_mounted_tree_exposes_people_tools():
    from gramps_mcp.server import mcp

    async with Client(mcp) as client:
        tools = await client.list_tools()
        tool_names = {t.name for t in tools}

    assert tool_names, "expected at least one generated tool, got none"
    get_people_tools = [name for name in tool_names if name.endswith("_get_people")]
    assert get_people_tools, f"no *_get_people tool in {sorted(tool_names)}"


async def test_get_people_returns_real_data():
    from gramps_mcp.server import mcp

    async with Client(mcp) as client:
        tools = await client.list_tools()
        (get_people_tool,) = [t.name for t in tools if t.name.endswith("_get_people")]
        result = await client.call_tool(get_people_tool, {"page": 1, "pagesize": 2})

    assert result.content, "tool call returned no content"
    assert not result.is_error, f"tool call failed: {result.content}"
