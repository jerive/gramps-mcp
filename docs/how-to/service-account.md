# Create a Gramps Web service account

`gramps-mcp` authenticates to Gramps Web API as a regular Gramps Web user, one
per mounted tree, and acts with exactly that user's permissions. There is no
separate "service account" concept in Gramps Web, just a user you create for
this purpose.

1. In the Gramps Web UI, go to **Settings → Manage Users**. The account doing
   this needs the Owner or Admin role.
2. Create one user per tree you intend to mount.
3. Put each user's credentials in
   `GRAMPS_MCP_TREE_<NAME>_USERNAME` / `_PASSWORD` (see
   [Configuration](../reference/configuration.md#required)).

## Choose the role deliberately

The role is the actual ceiling on what an LLM can do through this server.

| Role | What it allows | Use it when |
|---|---|---|
| **Member** | Read-only, including private objects | Research and Q&A; you don't want the server to write anything |
| **Editor** | Add, edit and delete objects | You want to dictate changes to the tree through an agent |
| **Owner** | Everything Editor can, plus user management | You enable Google sign-in and want the per-tree membership check to list users |

Prefer **Member** unless you need writes. With **Editor**, treat it as "I'm OK
with an LLM making real edits to this tree, backups permitting".

See Gramps Web's own
[user roles documentation](https://www.grampsweb.org/install_setup/users/) for
the full permission matrix.

## With Google sign-in enabled

The server decides who may use a tree by listing that tree's Gramps Web users
(`GET /api/users/`) and matching the signed-in Google email against theirs.
Gramps Web only allows that listing for **Owner** (or Admin). With a
Member/Editor account the listing fails and only the emails in
`GRAMPS_MCP_ADMIN_EMAILS` can get in. You then have two options:

- Use an **Owner** account for the tree. Registered Gramps users can sign in
  with their own Google account, but the account this server runs as can now
  also add or remove users and write to the tree, a much bigger blast radius if
  an agent misbehaves.
- Keep Member/Editor and list everyone who should have access in
  `GRAMPS_MCP_ADMIN_EMAILS`. Admin emails **bypass the membership check for
  every mounted tree**, so this grants access to all of them.

Without Google sign-in (local use) no user listing happens, and Member or
Editor is enough.
