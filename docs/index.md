# gramps-mcp

An unofficial [MCP](https://modelcontextprotocol.io/) server that exposes one
or more [Gramps Web API](https://github.com/gramps-project/gramps-web-api)
trees to LLM assistants (Claude, etc.), against your own genealogy data.

Since several assistants (e.g. Claude, Antigravity) support voice input, this
works particularly well hands-free and conversationally: you can ask an
open-ended research question — *"give me a breakdown of the social classes of
the people in this tree"* — or edit the tree live by voice, the same way you'd
dictate a change to an assistant in any other MCP-backed app.

Built on [FastMCP](https://gofastmcp.com/), it exposes a curated set of eight
tools—seven read operations and a single safe write tool, `apply_changes`—shared
across all configured trees behind optional Google OAuth and a per-tree membership
check. Rather than exposing dozens of raw REST endpoints that clutter the LLM's
context window, it unifies tree operations behind clean abstractions, presenting
every planned mutation to the user for interactive review and writing only once
confirmed.

## Safe, interactive writes with MCP Apps

Writing to a genealogy database requires strict care: invalid references or wrong schemas can corrupt a tree. `gramps-mcp` channels all modifications through a single entry point, `apply_changes`. When connected to an MCP client supporting **MCP Apps (Prefab UI)** (e.g. Visual Studio Code with Copilot), it turns planned changes into a live, interactive review card:

<p align="center">
  <img src="assets/shakespeare-card.png" alt="Interactive change card: additions in green, edits in orange, deletions in red, with editable fields and checkboxes" width="600" />
</p>
<p align="center" style="margin-top: -0.5rem; margin-bottom: 1.5rem; display: flex; justify-content: center; gap: 12px; flex-wrap: wrap;">
  <a href="assets/shakespeare-card.html" target="_blank" class="md-button md-button--primary">
    Try the live interactive card :material-open-in-new:
  </a>
  <a href="assets/demo.mp4" target="_blank" class="md-button">
    Watch demo video :material-play-circle:
  </a>
</p>

- **Visual status badges**: Additions in green, edits in orange, deletions in red.
- **Automatic dependency cascades**: Unticking a change automatically unticks everything that depends on it.
- **Inline text editing**: Fix names, descriptions, or surnames directly in the card before applying.
- **Model isolation**: Clicking **Apply** calls `confirm_changes` with a single-use token; the tool is hidden from the model (`visibility: ["app"]`), ensuring the AI cannot self-approve.
- **Atomic rollback**: If any change fails, all earlier changes in the transaction are rolled back atomically.

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
pip install "gramps-mcp @ git+https://github.com/jerive/gramps-mcp"
```

Installing this way follows `master`. To pin a release, append `@<tag>`, for
example `@v0.5.0`; the tags are listed on the
[Releases page](https://github.com/jerive/gramps-mcp/releases).
Python 3.12 or newer is required.
