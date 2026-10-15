# Deploy with Docker Compose

This assumes you already have a Gramps Web API instance running (for example
from its own [docker-compose.yml](https://github.com/gramps-project/gramps-web-api)).
The `docker-compose.yml` in this repository is an addition to that stack, not a
replacement: it only adds the `gramps-mcp` and `redis` services, joined to the
same Docker network as the existing `grampsweb` service(s) so
`GRAMPS_BACKEND_URL` can reach it.

If that base stack already runs its own Redis (Gramps Web API uses one for
Celery and rate limiting), you can point `MCP_CLIENT_STORAGE_URL` at it on an
unused DB index instead of adding a second `redis` service.

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

Copy `.env.example` to `.env` and fill in at least `GRAMPS_BACKEND_URL`,
`GRAMPS_MCP_TREES`, and the matching `GRAMPS_MCP_TREE_<NAME>_USERNAME` /
`_PASSWORD` pair(s). Then:

```sh
docker compose up -d
```

Google OAuth (and therefore Redis) is only exercised once `GOOGLE_CLIENT_ID` /
`GOOGLE_CLIENT_SECRET` are set; without them the server still runs,
unauthenticated, for local or single-user use. Point your MCP client at
`http://<host>:8000/mcp`.

!!! warning
    The container binds `0.0.0.0`. An unauthenticated server must not be
    reachable from a network: either [enable Google sign-in](google-oauth.md)
    or keep the port closed.
