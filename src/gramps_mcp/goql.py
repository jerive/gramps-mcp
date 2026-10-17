"""The GOQL guide, published as an MCP resource."""

from __future__ import annotations

from importlib import resources

from fastmcp import FastMCP

GOQL_SPEC_URI = "gramps://goql-spec"
GOQL_SPEC_DESCRIPTION = (
    "Gramps Object Query Language (GOQL) guide. Read this before writing a "
    "`where_expr` for the tree-scoped `query_objects` tool, or a "
    "`MatchesQuery` rule for `list_objects`."
)


def read_goql_guide() -> str:
    return (
        resources.files("gramps_mcp")
        .joinpath("goql_docs.md")
        .read_text(encoding="utf-8")
    )


def register_goql_resource(mcp: FastMCP) -> None:
    """Serves the guide at `gramps://goql-spec`, for a server that builds its own
    `FastMCP` instead of using `build_server`."""
    mcp.resource(
        GOQL_SPEC_URI,
        name="gramps-goql-spec",
        title="Gramps Object Query Language guide",
        description=GOQL_SPEC_DESCRIPTION,
        mime_type="text/markdown",
    )(read_goql_guide)
