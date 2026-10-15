# Tools and resources

## Generated tools

For every tree in `GRAMPS_MCP_TREES`, the server downloads that Gramps Web API
instance's `openapi.json` and generates one MCP tool per operation. Tools are
mounted under a per-tree namespace:

```
tree-<name>_<operationId>
```

For a tree named `example`, the operation `get_people` becomes
`tree-example_get_people`, and `put_person` becomes `tree-example_put_person`.
Every operation has its own `operationId` in Gramps Web API's OpenAPI document,
so the names are stable and predictable. They follow the pattern
`get_*`, `post_*`, `put_*` and `delete_*`.

The tool list therefore follows whichever Gramps Web API version you run. If
the backend adds an endpoint, it shows up after the next restart.

A role limits what the tools can actually do: a **Member** account can call
`put_person`, but Gramps Web will refuse it. See
[Create a Gramps Web service account](../how-to/service-account.md).

### Excluded routes

These prefixes are never turned into tools:

| Prefix | Why |
|---|---|
| `/api/filters/` | Saved (custom) filters are better kept out of an agent's hands. |
| `/api/token/` | The server already handles authentication and token refresh. |
| `/api/users/` | The server uses it itself for the membership check; user management is not for an agent. |
| `/api/facts/`, `/api/facts` | Not useful to an agent. |
| `/api/config`, `/api/oidc/config` | Server configuration is not useful to an agent. |

The list lives in `EXCLUDED_ROUTE_PREFIXES` in `src/gramps_mcp/trees.py`.

### Request handling

- **Output schemas are dropped**, since Gramps' response schemas are too loose
  for strict validation to help.
- **`PUT` and `PATCH` bodies get the object `handle` filled in.** The tool
  schema leaves `handle` out of the body because it collides with the path
  parameter of the same name, but Gramps Web rejects an update without it in
  the payload, so the server adds it.
- **Authentication** to Gramps Web is automatic. The server logs in once per
  tree and refreshes the token in the background.

## Resources

| URI | Content |
|---|---|
| `gramps://gql-spec` | Reference for the Gramps Query Language, so an agent can write a valid `gql=` filter before calling a search tool. |

## Endpoint

The server speaks MCP's streamable HTTP transport at `/mcp`. With Google
sign-in enabled it also serves the OAuth metadata and authorization endpoints
that MCP clients discover automatically.
