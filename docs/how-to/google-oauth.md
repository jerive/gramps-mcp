# Enable Google sign-in

Needed only if you expose the server beyond `localhost`. Without it the server
runs unauthenticated, for a single trusted local user. See
[Security and risk model](../explanation/security-model.md).

In the [Google Cloud Console](https://console.cloud.google.com/apis/credentials):

1. Create a project (or reuse one), then **Create credentials → OAuth client
   ID**, application type **Web application**.
2. Under **Authorized redirect URIs**, add
   `<MCP_BASE_URL><MCP_GOOGLE_REDIRECT_PATH>`. With the defaults that is
   `https://mcp.example.com/auth/callback`.
3. Copy the generated **Client ID** and **Client secret** into
   `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET`.
4. Set `GRAMPS_MCP_ADMIN_EMAILS` to the Google account email(s) that should
   bypass per-tree membership checks (for example your own). Everyone else
   signing in with Google is only let into a tree if their email matches a
   Gramps Web user registered on that tree.
5. Set `MCP_BASE_URL` to the public HTTPS URL of the server, and
   `MCP_CLIENT_STORAGE_URL` to a Redis instance (see
   [Deploy with Docker Compose](docker-compose.md)).

The OAuth consent screen can stay in "Testing" mode with an explicit list of
test users if this is just for family or friends. There is no need to publish
the app or go through Google's verification review.

Verified Google tokens are cached in memory for
`GRAMPS_MCP_GOOGLE_TOKEN_CACHE_TTL` seconds (default 300) to avoid a Google
round-trip on every MCP request; set it to `0` to disable caching.

The membership check needs an **Owner** service account or an entry in
`GRAMPS_MCP_ADMIN_EMAILS`: see
[Create a Gramps Web service account](service-account.md#with-google-sign-in-enabled).
