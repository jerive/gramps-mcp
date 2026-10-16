# Connect an MCP client

Once the server is reachable (locally or at a public HTTPS URL), add it as a
remote MCP server in your client.

## Clients that accept a URL

Claude's Connectors settings (desktop app and claude.ai) and other clients with
native remote MCP support just need the URL:

```
http://<host>:8000/mcp
```

or your public HTTPS URL.

## Clients that only support stdio

Bridge through [`mcp-remote`](https://www.npmjs.com/package/mcp-remote) in your
client's MCP config. `mcp-remote` only accepts plain `http://` for
`localhost`; for a server on another host use its public `https://` URL, or
pass `--allow-http` at your own risk on a trusted network.

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

## With Google sign-in

The first connection opens a browser for the consent screen. `mcp-remote` (or
the client itself, for native remote support) handles the redirect and stores
the resulting token for you. See [Enable Google sign-in](google-oauth.md).

## Query language help for the agent

The server publishes a `gramps://goql-spec` MCP resource with a guide to the
Gramps Object Query Language, so an agent can look up the syntax before
building a `where_expr` for a tree's `post_<type>_query` tools. The same text
is on the [GOQL page](../reference/goql.md).
