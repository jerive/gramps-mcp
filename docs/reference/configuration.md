# Configuration

All configuration is through environment variables. `.env.example` in the
repository is a ready-to-copy template.

## Required

| Variable | Description |
|---|---|
| `GRAMPS_MCP_TREES` | Comma-separated names of the trees to mount, e.g. `Example,Family`. |
| `GRAMPS_MCP_TREE_<NAME>_USERNAME` | Gramps Web username for that tree. |
| `GRAMPS_MCP_TREE_<NAME>_PASSWORD` | Gramps Web password for that tree. |

`<NAME>` is the tree name uppercased, with non-alphanumeric characters replaced
by `_`. A tree called `my-tree` reads `GRAMPS_MCP_TREE_MY_TREE_USERNAME`.

## Gramps backend

| Variable | Default | Description |
|---|---|---|
| `GRAMPS_BACKEND_URL` | `http://localhost:5000` | Base URL of the Gramps Web API instance. |
| `GRAMPS_MCP_LOGIN_ATTEMPTS` | `8` | How many times to retry the initial login per tree at startup, in case Gramps Web is still booting. |

## Server

| Variable | Default | Description |
|---|---|---|
| `MCP_HOST` | `0.0.0.0` | Interface to bind. Use `127.0.0.1` for an unauthenticated local server. |
| `MCP_PORT` | `8000` | Port to listen on. The MCP endpoint is `/mcp`. |
| `MCP_BASE_URL` | `http://localhost:8000` | Public URL of the server. Used to build OAuth URLs, so it must be the URL clients actually reach. |

## Google sign-in

Setting both `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET` turns
authentication on. Leave them unset for an unauthenticated server.

| Variable | Default | Description |
|---|---|---|
| `GOOGLE_CLIENT_ID` | unset | OAuth client ID from the Google Cloud Console. |
| `GOOGLE_CLIENT_SECRET` | unset | OAuth client secret. |
| `MCP_GOOGLE_REDIRECT_PATH` | `/auth/callback` | Path appended to `MCP_BASE_URL` to form the redirect URI registered with Google. |
| `MCP_CLIENT_STORAGE_URL` | `redis://localhost:6379/0` | Redis holding registered MCP clients and their tokens, so they survive restarts. |
| `GRAMPS_MCP_ADMIN_EMAILS` | unset | Comma-separated Google emails that bypass the per-tree membership check, on every mounted tree. |
| `GRAMPS_MCP_GOOGLE_TOKEN_CACHE_TTL` | `300` | Seconds a verified Google token is cached in memory. `0` disables the cache. |

See [Enable Google sign-in](../how-to/google-oauth.md) for the procedure.

## Logging

| Variable | Default | Description |
|---|---|---|
| `GRAMPS_MCP_LOG_LEVEL` | `INFO` | Python log level. |
| `GRAMPS_MCP_HTTP_LOG_LIMIT` | `4000` | Maximum number of characters of an HTTP request or response body written to the log. |
