# gramps-mcp

An unofficial [MCP](https://modelcontextprotocol.io/) server that exposes one
or more [Gramps Web API](https://github.com/gramps-project/gramps-web-api)
trees to LLM assistants (Claude, etc.), against your own genealogy data.

Since several assistants (e.g. Claude, Antigravity) support voice input, this
works particularly well hands-free and conversationally: you can ask an
open-ended research question — *"give me a breakdown of the social classes of
the people in this tree"* — or edit the tree live by voice, the same way you'd
dictate a change to an assistant in any other MCP-backed app.

It works by pointing [FastMCP](https://gofastmcp.com/)'s
`FastMCP.from_openapi()` at your Gramps Web API instance's `openapi.json` and
auto-generating one MCP tool per REST endpoint, mounting one sub-server per
configured tree behind optional Google OAuth and a per-tree membership check.

## Status and scope

This is a community project, not affiliated with or endorsed by the Gramps
project. See
[gramps-web-api#1003](https://github.com/gramps-project/gramps-web-api/issues/1003)
for the maintainer's position: Gramps Web API intentionally exposes an open,
scriptable REST API, but the project won't officially endorse an MCP layer on
top of it, in part because **the Gramps database model is not very fault
tolerant** — a bad write (dangling reference, wrong schema, etc.) can corrupt
a tree, and that risk is higher when requests are agent-generated rather than
hand-written. Point this at a tree you're prepared to back up and, if
necessary, recover from an LLM's mistakes. It's aimed at users comfortable
with that trade-off, not a general recommendation.

[gramps-web-api#943](https://github.com/gramps-project/gramps-web-api/issues/943)
and its follow-ups
([#944](https://github.com/gramps-project/gramps-web-api/pull/944),
[#971](https://github.com/gramps-project/gramps-web-api/pull/971)) improved
`openapi.json` specifically to make this kind of generation work cleanly —
every operation now has a unique `operationId` and a specific summary, and
mutation endpoints document their request body.

## Configure

Copy `.env.example` to `.env` and fill in, at minimum:

- `GRAMPS_BACKEND_URL` — your Gramps Web API instance's base URL.
- `GRAMPS_MCP_TREES` — comma-separated tree names to mount.
- `GRAMPS_MCP_TREE_<NAME>_USERNAME` / `_PASSWORD` — a Gramps Web user's
  credentials for each tree in `GRAMPS_MCP_TREES` (`<NAME>` is the tree name
  uppercased, non-alphanumeric characters replaced by `_`).

Everything else in `.env.example` is optional. In particular, `GOOGLE_CLIENT_ID` /
`GOOGLE_CLIENT_SECRET` gate the server behind Google sign-in and a Redis-backed
per-tree membership check (`GRAMPS_MCP_ADMIN_EMAILS` plus each tree's own
Gramps Web user list) — leave them unset only for a server you run purely on
`localhost` for yourself.

By default, `/api/filters/`, `/api/token/`, `/api/users/`, `/api/facts/`, and
config endpoints are excluded from the generated tool set — see
`EXCLUDED_ROUTE_PREFIXES` in `src/gramps_mcp/trees.py`.

## Run

```sh
gramps-mcp
```

This starts an HTTP-transport MCP server on `MCP_HOST`:`MCP_PORT` (default
`0.0.0.0:8000`). Point your MCP client at `http://<host>:<port>/mcp`.

## Docker Compose example

`docker-compose.yml` (built from the included `Dockerfile`) runs the server
alongside a Redis instance:

```yaml
services:
  redis:
    image: redis:7-alpine
    restart: unless-stopped
    volumes:
      - redis_data:/data

  gramps-mcp:
    build: .
    restart: unless-stopped
    ports:
      - "8000:8000"
    environment:
      GRAMPS_BACKEND_URL: "${GRAMPS_BACKEND_URL}"
      GRAMPS_MCP_TREES: "${GRAMPS_MCP_TREES}"
      GRAMPS_MCP_TREE_MYTREE_USERNAME: "${GRAMPS_MCP_TREE_MYTREE_USERNAME}"
      GRAMPS_MCP_TREE_MYTREE_PASSWORD: "${GRAMPS_MCP_TREE_MYTREE_PASSWORD}"
      GRAMPS_MCP_ADMIN_EMAILS: "${GRAMPS_MCP_ADMIN_EMAILS:-}"
      GOOGLE_CLIENT_ID: "${GOOGLE_CLIENT_ID:-}"
      GOOGLE_CLIENT_SECRET: "${GOOGLE_CLIENT_SECRET:-}"
      MCP_BASE_URL: "${MCP_BASE_URL:-https://mcp.example.com}"
      MCP_GOOGLE_REDIRECT_PATH: "${MCP_GOOGLE_REDIRECT_PATH:-/auth/callback}"
      # Redis backs the OAuth client store, so registered MCP clients and
      # their tokens survive a container restart instead of forcing every
      # client to re-authorize.
      MCP_CLIENT_STORAGE_URL: "redis://redis:6379/0"
      MCP_HOST: "0.0.0.0"
      MCP_PORT: "8000"
    depends_on:
      - redis

volumes:
  redis_data:
```

Run it with `docker compose up -d` after populating a `.env` (see
[Configure](#configure)) with at least `GRAMPS_BACKEND_URL`,
`GRAMPS_MCP_TREES`, and the matching `GRAMPS_MCP_TREE_<NAME>_USERNAME`/`_PASSWORD`
pair(s). Google OAuth (and therefore Redis) is only exercised once
`GOOGLE_CLIENT_ID`/`GOOGLE_CLIENT_SECRET` are set; without them the server
still runs, unauthenticated, for local/single-user use.

## GQL resource

The server also publishes a `gramps://gql-spec` MCP resource with the
[Gramps Query Language](https://gramps-project.org) reference, so an agent
can look up GQL syntax before constructing a `gql=` filter against a tree's
search tools.

## License

MIT.
