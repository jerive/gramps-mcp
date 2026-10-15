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

## Prerequisites

- A running [Gramps Web API](https://github.com/gramps-project/gramps-web-api)
  instance (this project doesn't run Gramps itself, just sits in front of an
  existing deployment) with at least one tree.
- A Gramps Web user per tree you want to mount — see
  [below](#create-a-gramps-web-service-account).
- Python 3.12+ if running outside Docker.

## Create a Gramps Web service account

This server authenticates to Gramps Web API as a regular Gramps Web user, one
per mounted tree, and acts with exactly that user's permissions — there's no
separate "service account" concept in Gramps Web, just a user you create for
this purpose. In the Gramps Web UI, go to **Settings → Manage Users** (the
account creating it needs the Owner or Admin role) and create one user per
tree you intend to mount.

Pick the role deliberately, since it's the actual ceiling on what an LLM can
do through this server:

- **Member** — read-only (including private objects). Safest option for
  research/Q&A use cases where you don't want the server to write anything.
- **Editor** — can add, edit, and delete objects. Needed if you want to
  dictate changes to the tree through an agent. Combined with the warning in
  [Status and scope](#status-and-scope) above, treat this as "I'm OK with an
  LLM making real edits to this tree, backups permitting."
- **Owner** — everything Editor can do, plus user management. Required only
  if you enable Google sign-in and want the per-tree membership check (see
  below).

**Google sign-in and membership checks.** When Google OAuth is enabled, the
server decides who may use a tree by listing that tree's Gramps Web users
(`GET /api/users/`) and matching the signed-in Google email against theirs.
Gramps Web only allows that listing for **Owner** (or Admin), so with a
Member/Editor account the listing fails and only the emails in
`GRAMPS_MCP_ADMIN_EMAILS` can get in. You then have two options:

- Use an **Owner** account for the tree. Registered Gramps users can sign in
  with their own Google account, but the account this server runs as can now
  also add/remove users and write to the tree, so it's a much bigger blast
  radius if an agent misbehaves.
- Keep Member/Editor and list everyone who should have access in
  `GRAMPS_MCP_ADMIN_EMAILS`. Note that admin emails **bypass the membership
  check for every mounted tree**, so this grants access to all of them.

Without Google sign-in (local use), no user listing happens, and Member or
Editor is enough. See Gramps Web's own
[user roles documentation](https://www.grampsweb.org/install_setup/users/)
for the full permission matrix.

## Configure

Copy `.env.example` to `.env` and fill in, at minimum:

- `GRAMPS_BACKEND_URL` — your Gramps Web API instance's base URL.
- `GRAMPS_MCP_TREES` — comma-separated tree names to mount.
- `GRAMPS_MCP_TREE_<NAME>_USERNAME` / `_PASSWORD` — the Gramps Web user's
  credentials created above, for each tree in `GRAMPS_MCP_TREES` (`<NAME>` is
  the tree name uppercased, non-alphanumeric characters replaced by `_`).

Everything else in `.env.example` is optional. In particular, `GOOGLE_CLIENT_ID` /
`GOOGLE_CLIENT_SECRET` gate the server behind Google sign-in and a Redis-backed
per-tree membership check (`GRAMPS_MCP_ADMIN_EMAILS` plus each tree's own
Gramps Web user list) — leave them unset only for a server you run purely on
`localhost` for yourself. When Google sign-in is enabled, verified tokens are
cached in-memory for `GRAMPS_MCP_GOOGLE_TOKEN_CACHE_TTL` seconds (default
300) to avoid a Google tokeninfo/userinfo round-trip on every MCP request;
set it to `0` to disable caching.

By default, `/api/filters/`, `/api/token/`, `/api/users/`, `/api/facts/`, and
config endpoints are excluded from the generated tool set — see
`EXCLUDED_ROUTE_PREFIXES` in `src/gramps_mcp/trees.py`.

### Setting up Google OAuth

Needed only if you're exposing this server beyond `localhost`. In the
[Google Cloud Console](https://console.cloud.google.com/apis/credentials):

1. Create a project (or reuse one), then **Create credentials → OAuth client
   ID**, application type **Web application**.
2. Under **Authorized redirect URIs**, add
   `<MCP_BASE_URL><MCP_GOOGLE_REDIRECT_PATH>` — with the defaults below,
   that's `https://mcp.example.com/auth/callback`.
3. Copy the generated **Client ID** and **Client secret** into
   `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET`.
4. Set `GRAMPS_MCP_ADMIN_EMAILS` to the Google account email(s) that should
   bypass per-tree membership checks (e.g. your own); everyone else signing
   in with Google is only let into a tree if their email matches a Gramps
   Web user registered on that tree.

The OAuth consent screen can stay in "Testing" mode with an explicit list of
test users if this is just for your own family/friends — no need to publish
the app or go through Google's verification review.

## Docker Compose example

This assumes you already have a Gramps Web API instance running (e.g. from
its own
[docker-compose.yml](https://github.com/gramps-project/gramps-web-api)).
`docker-compose.yml` here is meant as an addition to that base stack, not a
replacement for it — it only adds the `gramps-mcp` and `redis` services shown
below, joined to the same Docker network as the existing `grampsweb`
service(s) so `GRAMPS_BACKEND_URL` can reach it. If that base stack already
runs its own Redis (Gramps Web API uses one for Celery and rate-limiting),
you can point `MCP_CLIENT_STORAGE_URL` at it on an unused DB index instead of
adding a second `redis` service.

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
      GRAMPS_MCP_GOOGLE_TOKEN_CACHE_TTL: "${GRAMPS_MCP_GOOGLE_TOKEN_CACHE_TTL:-300}"
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
still runs, unauthenticated, for local/single-user use. Point your MCP client
at `http://<host>:8000/mcp`.

## Running without Docker

For a quick local test against a Gramps Web API instance you can already
reach — no Redis, no OAuth, single user:

```sh
# Pin to a release tag for reproducibility; drop @v0.2.0 to track master instead.
pip install "gramps-mcp @ git+https://github.com/jerive/gramps-mcp@v0.2.0"
export GRAMPS_BACKEND_URL=http://localhost:5000
export GRAMPS_MCP_TREES=MyTree
export GRAMPS_MCP_TREE_MYTREE_USERNAME=myuser
export GRAMPS_MCP_TREE_MYTREE_PASSWORD=mypassword
export MCP_HOST=127.0.0.1   # without auth, only listen on localhost
gramps-mcp
```

This starts the same server as the Docker image (`MCP_HOST`/`MCP_PORT` env
vars apply here too; they default to `0.0.0.0:8000`, which is why the example
binds to `127.0.0.1` — an unauthenticated server must not listen on every
interface) — just without a container around it. Add `GOOGLE_CLIENT_ID`/`GOOGLE_CLIENT_SECRET`/
`MCP_CLIENT_STORAGE_URL` (pointing at a Redis you run separately) the same
way as in the Docker Compose example if you need auth.

## Connect an MCP client

Once the server is reachable (locally or at a public HTTPS URL), add it as a
remote MCP server in your client. For clients that accept a URL directly
(e.g. Claude's Connectors settings, in both the desktop app and claude.ai),
just give them `http://<host>:8000/mcp` (or your public HTTPS URL).

For clients that only support local/stdio MCP servers, bridge through
[`mcp-remote`](https://www.npmjs.com/package/mcp-remote) in your client's MCP
config instead. `mcp-remote` only accepts plain `http://` for `localhost`; for a
server on another host use its public `https://` URL (or pass `--allow-http`
at your own risk on a trusted network):

```json
{
  "mcpServers": {
    "gramps": {
      "command": "npx",
      "args": ["-y", "mcp-remote", "http://localhost:8000/mcp"]
    }
  }
}
```

If Google OAuth is enabled, the first connection opens a browser for the
consent screen; `mcp-remote` (or the client itself, for native remote
support) handles the redirect and stores the resulting token for you.

## GQL resource

The server also publishes a `gramps://gql-spec` MCP resource with the
[Gramps Query Language](https://gramps-project.org) reference, so an agent
can look up GQL syntax before constructing a `gql=` filter against a tree's
search tools.

## License

MIT.
