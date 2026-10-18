# How it works

## Eight tools, shared across all trees

Rather than exposing dozens of raw REST endpoints that clutter the model's context window, `gramps-mcp` exposes only **eight focused tools**:

- **Seven read operations**:
  - `list_objects`: Page objects of any of the 10 Gramps types (`people`, `families`, `events`, etc.).
  - `get_object`: Fetch a single object by handle.
  - `query_objects`: Query objects matching a [GOQL](../reference/goql.md) `where_expr`.
  - `get_search`: Full-text search across all object types.
  - `get_relation` / `get_relations`: Calculate relationship paths between people.
  - `get_person_timeline` / `get_family_timeline`: Chronological events for a person or family.
  - `list_trees`: Available trees for the authenticated user.
- **One write operation**:
  - `apply_changes`: The single atomic mutation entry point (`create`, `update`, `delete`), backed by human-in-the-loop confirmation.

All trees configured in `GRAMPS_MCP_TREES` share these same tools. Each tool takes a `tree` parameter (optional when only one tree is configured). The server keeps a dedicated HTTP client per tree logged in as that tree's service account, guaranteeing that requests for tree A can never use tree B's credentials.

## What the server adds on top of the generation

- **Route pruning.** Administration routes (users, tokens, server config) are never exposed to the agent.
- **Looser schemas.** Read output schemas are dropped so minor upstream variation never breaks validation.
- **Safe write flow.** Direct `PUT`/`POST` endpoints are excluded. `apply_changes` merges changes, builds a readable summary, asks for confirmation, and rolls back atomically on failure.
- **Token upkeep.** Automatic background login and token refresh for each tree's service account.
- **GOQL spec resource.** The `gramps://goql-spec` resource gives the agent query documentation on demand without bloating tool descriptions.

## Safe write flow with elicitation and MCP Apps

Modifying a genealogy database presents unique risks because relationships span across several tables (e.g. people, families, events, places). A direct REST `PUT` or `POST` by an LLM could leave dangling references or corrupt family links.

To protect data integrity, `gramps-mcp` funnels all writes through `apply_changes`. When connected to an MCP client supporting **MCP Apps (Prefab UI)** (such as Visual Studio Code with Copilot), it executes the following flow:

<style>
.sequence-diagram-viewer {
  position: relative;
  margin: 1.5rem 0 2rem;
  border: 1px solid var(--md-default-fg-color--lightest, #cbd5e1);
  border-radius: 8px;
  background: var(--md-code-bg-color, #f8fafc);
  overflow: hidden;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.05);
}

.diagram-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: 0.5rem;
  padding: 0.5rem 0.85rem;
  background: var(--md-default-bg-color, #ffffff);
  border-bottom: 1px solid var(--md-default-fg-color--lightest, #e2e8f0);
  font-size: 0.85rem;
}

.diagram-hint {
  color: var(--md-default-fg-color--lighter, #64748b);
  user-select: none;
  font-size: 0.82rem;
}

.diagram-actions {
  display: flex;
  gap: 0.35rem;
  align-items: center;
}

.diagram-btn {
  background: var(--md-default-bg-color, #ffffff);
  border: 1px solid var(--md-default-fg-color--lightest, #cbd5e1);
  border-radius: 4px;
  color: var(--md-default-fg-color, #334155);
  cursor: pointer;
  padding: 0.25rem 0.65rem;
  font-size: 0.82rem;
  font-weight: 500;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  transition: all 0.15s ease;
}
.diagram-btn:hover {
  background: var(--md-accent-fg-color, #0d9488);
  color: #ffffff;
  border-color: var(--md-accent-fg-color, #0d9488);
}

.diagram-viewport {
  overflow: auto;
  padding: 1.25rem;
  cursor: zoom-in;
  background: var(--md-default-bg-color, #ffffff);
  min-height: 480px;
}

.diagram-content {
  display: flex;
  justify-content: center;
  transform-origin: top center;
  transition: transform 0.15s ease-out;
}

.diagram-content .mermaid {
  min-width: 1050px !important;
  display: flex !important;
  justify-content: center !important;
}

/* Fullscreen mode */
.sequence-diagram-viewer.is-fullscreen {
  position: fixed !important;
  inset: 0 !important;
  z-index: 99999 !important;
  margin: 0 !important;
  border-radius: 0 !important;
  border: none !important;
  display: flex !important;
  flex-direction: column !important;
  background: var(--md-default-bg-color, #ffffff) !important;
  box-shadow: none !important;
}

.sequence-diagram-viewer.is-fullscreen .diagram-toolbar {
  padding: 0.75rem 1.25rem;
  box-shadow: 0 1px 4px rgba(0, 0, 0, 0.08);
  z-index: 2;
}

.sequence-diagram-viewer.is-fullscreen .diagram-viewport {
  flex: 1;
  padding: 2rem;
  display: flex;
  align-items: center;
  justify-content: center;
  cursor: default;
}

.sequence-diagram-viewer.is-fullscreen .diagram-content {
  transform-origin: center center;
}

.sequence-diagram-viewer.is-fullscreen .fullscreen-open-label {
  display: none;
}
.sequence-diagram-viewer.is-fullscreen .fullscreen-close-label {
  display: inline !important;
}
</style>

<div class="sequence-diagram-viewer" id="sequence-diagram-box">
  <div class="diagram-toolbar">
    <span class="diagram-hint">💡 <em>Click anywhere on the diagram or use controls to expand & zoom</em></span>
    <div class="diagram-actions">
      <button type="button" class="diagram-btn zoom-out-btn" title="Zoom out" aria-label="Zoom out">−</button>
      <button type="button" class="diagram-btn zoom-reset-btn" title="Reset zoom" aria-label="Reset zoom">100%</button>
      <button type="button" class="diagram-btn zoom-in-btn" title="Zoom in" aria-label="Zoom in">+</button>
      <button type="button" class="diagram-btn fullscreen-toggle-btn" title="Toggle fullscreen view" aria-label="Toggle fullscreen view">
        <span class="fullscreen-open-label">⛶ Fullscreen</span>
        <span class="fullscreen-close-label" style="display:none;">✕ Close</span>
      </button>
    </div>
  </div>
  <div class="diagram-viewport">
    <div class="diagram-content">

```mermaid
sequenceDiagram
    autonumber
    actor User as User (VS Code)
    participant Copilot as AI Assistant (Copilot)
    participant MCP as gramps-mcp Server
    participant Gramps as Gramps Web API

    User->>Copilot: "Update William Shakespeare in the tree..."
    Note over Copilot,MCP: 1. Read & Discovery Phase
    Copilot->>MCP: get_search(tree="viveret", query="William Shakespeare")
    MCP->>Gramps: GET /api/search/?query=William+Shakespeare
    Gramps-->>MCP: Matching records & existing relationships
    MCP-->>Copilot: Grounding context (handles, current state)

    Note over Copilot,MCP: 2. Write Preparation & Mutation Proposal
    Copilot->>MCP: apply_changes(tree="viveret", operations=[...])
    Note over MCP: GRAMPS_MCP_UI=prefab generates interactive card
    MCP-->>User: Elicitation: Interactive Prefab UI card rendered (dependency tree)
    
    Note over User: 3. Human Review & Interaction
    User->>User: Reviews operations (Green / Orange / Red)<br/>Tests dependency uncheck & text edit
    User->>MCP: Clicks [Apply] -> confirm_changes(tree="viveret", token, selection, edits)
    MCP->>Gramps: Atomic execution (POST, PUT, DELETE)
    Gramps-->>MCP: Success (status: applied, created handles)
    MCP-->>Copilot: Confirmation message with applied result
    Copilot-->>User: Final confirmation in chat
```

    </div>
  </div>
</div>

<script>
document.addEventListener("DOMContentLoaded", function () {
  const box = document.getElementById("sequence-diagram-box");
  if (!box) return;

  const content = box.querySelector(".diagram-content");
  const viewport = box.querySelector(".diagram-viewport");
  const zoomInBtn = box.querySelector(".zoom-in-btn");
  const zoomOutBtn = box.querySelector(".zoom-out-btn");
  const zoomResetBtn = box.querySelector(".zoom-reset-btn");
  const fullscreenBtn = box.querySelector(".fullscreen-toggle-btn");

  let scale = 1.0;
  const minScale = 0.6;
  const maxScale = 2.5;
  const step = 0.2;

  function updateScale(newScale) {
    scale = Math.min(maxScale, Math.max(minScale, Math.round(newScale * 10) / 10));
    content.style.transform = `scale(${scale})`;
    zoomResetBtn.textContent = `${Math.round(scale * 100)}%`;
  }

  zoomInBtn.addEventListener("click", function (e) {
    e.stopPropagation();
    updateScale(scale + step);
  });

  zoomOutBtn.addEventListener("click", function (e) {
    e.stopPropagation();
    updateScale(scale - step);
  });

  zoomResetBtn.addEventListener("click", function (e) {
    e.stopPropagation();
    updateScale(1.0);
  });

  function toggleFullscreen() {
    const isFull = box.classList.toggle("is-fullscreen");
    if (isFull) {
      document.body.style.overflow = "hidden";
      if (scale < 1.1) updateScale(1.2);
    } else {
      document.body.style.overflow = "";
      updateScale(1.0);
    }
  }

  fullscreenBtn.addEventListener("click", function (e) {
    e.stopPropagation();
    toggleFullscreen();
  });

  viewport.addEventListener("click", function (e) {
    if (!box.classList.contains("is-fullscreen") && !e.target.closest("button")) {
      toggleFullscreen();
    }
  });

  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape" && box.classList.contains("is-fullscreen")) {
      toggleFullscreen();
    }
  });

  viewport.addEventListener("wheel", function (e) {
    if (box.classList.contains("is-fullscreen") || e.ctrlKey) {
      e.preventDefault();
      if (e.deltaY < 0) {
        updateScale(scale + 0.1);
      } else {
        updateScale(scale - 0.1);
      }
    }
  }, { passive: false });
});
</script>

1. **Discovery**: The model queries the tree to ground itself on real handles and current data.
2. **Proposal**: The model drafts the atomic operations (`create`, `update`, `delete`).
3. **Interactive Elicitation**: Instead of writing immediately, `gramps-mcp` yields an interactive card with dependency closure and inline editing.
4. **Human Confirmation**: The user reviews, edits if needed, and clicks **Apply** to execute the mutations atomically.
5. **Atomic Rollback**: If an operation fails, previous operations in the set are rolled back atomically via Gramps Web API transactions.

## Authentication, in two layers

There are two unrelated identities in play:

1. **Who may talk to the MCP server.** Optional Google sign-in, enabled by setting `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET`. MCP clients register dynamically and obtain tokens; Redis stores them so a restart does not invalidate sessions.
2. **What the server may do in Gramps.** The service-account user of each tree. Its role is the hard ceiling on every action an agent can take.

Between the two sits the **per-tree membership check**: a signed-in Google user may use a tree only if their email belongs to a Gramps Web user of that tree, or to `GRAMPS_MCP_ADMIN_EMAILS`. It is checked on every call for the named `tree`. The membership list comes from `GET /api/users/`, cached for `GRAMPS_MCP_GOOGLE_TOKEN_CACHE_TTL` seconds.

When Google sign-in is disabled, only the service-account layer applies.
