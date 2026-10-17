"""Entry point: builds the MCP server and mounts every configured tree."""

from __future__ import annotations

import os

from fastmcp import FastMCP

from .core import build_google_auth_provider
from .goql import register_goql_resource
from .spec import fetch_openapi_spec
from .trees import mount_trees


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

    register_goql_resource(mcp)

    openapi_spec = fetch_openapi_spec()

    mount_trees(trees, openapi_spec, mcp)

    return mcp


mcp = build_server()


def main() -> None:
    host = os.environ.get("MCP_HOST", "0.0.0.0")
    port = int(os.environ.get("MCP_PORT", "8000"))
    mcp.run(transport="http", host=host, port=port)


if __name__ == "__main__":
    main()
