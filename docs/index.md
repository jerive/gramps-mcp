# gramps-mcp

An unofficial [MCP](https://modelcontextprotocol.io/) server that exposes one
or more [Gramps Web API](https://github.com/gramps-project/gramps-web-api)
trees to LLM assistants (Claude, etc.), against your own genealogy data.

Since several assistants (e.g. Claude, Antigravity) support voice input, this
works particularly well hands-free and conversationally: you can ask an
open-ended research question — *"give me a breakdown of the social classes of
the people in this tree"* — or edit the tree live by voice, the same way you'd
dictate a change to an assistant in any other MCP-backed app.

It points [FastMCP](https://gofastmcp.com/)'s `FastMCP.from_openapi()` at your
Gramps Web API instance's `openapi.json` and auto-generates the read tools
from the REST endpoints, mounting one sub-server per configured tree behind
optional Google OAuth and a per-tree membership check. Writing goes through a
single tool, `apply_changes`, which shows every change to the user and writes
only once they confirm.

!!! warning "Read this before pointing it at a real tree"
    This is a community project, not affiliated with or endorsed by the Gramps
    project. Gramps Web API intentionally exposes an open REST API, but its
    maintainers won't endorse an MCP layer on top of it, partly because the
    Gramps database model is not very fault tolerant: a bad write (dangling
    reference, wrong schema) can corrupt a tree, and that risk is higher when
    requests are agent-generated. Use a tree you can back up and recover. See
    [Security and risk model](explanation/security-model.md).

## Where to start

The documentation follows the [Diátaxis](https://diataxis.fr/) framework.

<div class="grid cards" markdown>

-   **Learn** — [Tutorial](tutorial.md)

    Run a throwaway Gramps Web API with the example tree and ask it for people
    through MCP, in about 15 minutes.

-   **Do** — [How-to guides](how-to/service-account.md)

    Create the service account, deploy with Docker Compose, enable Google
    sign-in, connect an MCP client.

-   **Look up** — [Reference](reference/configuration.md)

    Every environment variable, and exactly which tools get generated.

-   **Understand** — [Explanation](explanation/how-it-works.md)

    How the server is put together, and what its security model does and does
    not protect you from.

</div>

## Install

```sh
pip install "gramps-mcp @ git+https://github.com/jerive/gramps-mcp@%%LATEST_TAG%%"
```

Pin a release tag for reproducibility; drop `@%%LATEST_TAG%%` to track `master`.
Python 3.12 or newer is required.
