"""Entry point: builds the MCP server and mounts every configured tree."""

from __future__ import annotations

import os
from importlib import resources

import httpx2
from fastmcp import FastMCP

from .core import GRAMPS_BACKEND_URL, build_google_auth_provider
from .trees import mount_tree

GQL_SPEC_DESCRIPTION = (
    "Gramps Query Language (GQL) syntax reference. Read this before writing any `gql=` "
    "filter expression passed to the tree-scoped Gramps search tools."
)


def _read_gql_docs() -> str:
    return resources.files("gramps_mcp").joinpath("gql_docs.md").read_text(
        encoding="utf-8"
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
        "gramps://gql-spec",
        name="gramps-gql-spec",
        title="Gramps Query Language specification",
        description=GQL_SPEC_DESCRIPTION,
        mime_type="text/markdown",
    )(_read_gql_docs)

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
