# Run without Docker

For a quick local test against a Gramps Web API instance you can already
reach: no Redis, no OAuth, single user.

```sh
# Follows master. To pin a release, append @<tag>, for example @v0.5.0.
pip install "gramps-mcp @ git+https://github.com/jerive/gramps-mcp"
export GRAMPS_BACKEND_URL=http://localhost:5000
export GRAMPS_MCP_TREES=MyTree
export GRAMPS_MCP_TREE_MYTREE_USERNAME=myuser
export GRAMPS_MCP_TREE_MYTREE_PASSWORD=mypassword
export MCP_HOST=127.0.0.1   # without auth, only listen on localhost
gramps-mcp
```

This starts the same server as the Docker image. `MCP_HOST` and `MCP_PORT`
apply here too; they default to `0.0.0.0:8000`, which is why the example binds
to `127.0.0.1`: an unauthenticated server must not listen on every interface.

To add authentication, set `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET` and
`MCP_CLIENT_STORAGE_URL` (pointing at a Redis you run separately), as in
[Enable Google sign-in](google-oauth.md).

For a guided walk-through including a throwaway Gramps instance, see the
[tutorial](../tutorial.md).
