"""Entry point: builds the MCP server and mounts every configured tree."""

from __future__ import annotations

import os
from importlib import resources

import httpx2
from fastmcp import FastMCP

from .core import GRAMPS_BACKEND_URL, build_google_auth_provider
from .trees import mount_tree

GOQL_SPEC_DESCRIPTION = (
    "Gramps Object Query Language (GOQL) guide. Read this before writing a "
    "`where_expr` for the tree-scoped `query_objects` tool, or a "
    "`MatchesQuery` rule for `list_objects`."
)


def _read_goql_docs() -> str:
    return (
        resources.files("gramps_mcp")
        .joinpath("goql_docs.md")
        .read_text(encoding="utf-8")
    )


def build_server() -> FastMCP:
    trees_env = os.environ.get("GRAMPS_MCP_TREES", "").strip()
    if not trees_env:
        raise RuntimeError(
            "GRAMPS_MCP_TREES is not set. Provide a comma-separated list of tree "
            "names to mount, e.g. GRAMPS_MCP_TREES=Smith,Jones."
        )
    trees = [name.strip() for name in trees_env.split(",") if name.strip()]

    auth_provider = build_google_auth_provider()
    mcp = FastMCP(auth=auth_provider)

    mcp.resource(
        "gramps://goql-spec",
        name="gramps-goql-spec",
        title="Gramps Object Query Language guide",
        description=GOQL_SPEC_DESCRIPTION,
        mime_type="text/markdown",
    )(_read_goql_docs)

    openapi_spec_url = f"{GRAMPS_BACKEND_URL}/api/openapi.json"
    openapi_spec = httpx2.get(openapi_spec_url, timeout=30).raise_for_status().json()

    for project in trees:
        mount_tree(project, openapi_spec, mcp)

    return mcp


mcp = build_server()


def main() -> None:
    host = os.environ.get("MCP_HOST", "0.0.0.0")
    port = int(os.environ.get("MCP_PORT", "8000"))
    mcp.run(transport="http", host=host, port=port)


if __name__ == "__main__":
    main()
