# Security and risk model

## Scope

This is a community project, not affiliated with or endorsed by the Gramps
project. The Gramps Web API maintainers' position is stated in
[gramps-web-api#1003](https://github.com/gramps-project/gramps-web-api/issues/1003):
the REST API is intentionally open and scriptable, but the project will not
officially endorse an MCP layer on top of it.

## The data risk

The main reason is that **the Gramps database model is not very fault
tolerant**. A bad write, such as a dangling reference or a payload with the
wrong schema, can leave a tree in a state that Gramps itself struggles to
repair. A human writing against the API tends to be careful. An agent
generating requests is more likely to get one subtly wrong, and can do so many
times in a row.

What this means in practice:

- **Back up the tree before connecting an agent to it**, and know how to
  restore. Gramps Web's export and backup features exist for this.
- **Default to a Member account.** A read-only account cannot corrupt anything.
  Use Editor only when you want the agent to write.
- **Writes need the user's confirmation.** `apply_changes` shows every change
  and writes only if the user accepts; a client that cannot ask writes nothing.
  This guards against a model's mistake, not against a user who confirms
  without reading.
- **Review what the agent did.** Gramps Web's transaction history records
  changes and can undo them.
- **Prefer a copy.** Pointing the agent at a duplicate of the tree is the safest
  way to try a new workflow.

## Who can reach the server

Two configurations exist, and they have very different threat models.

### Unauthenticated (no Google credentials)

Anyone who can reach the port acts as the service account, with no check at
all. This is meant for a single user on one machine, and the server should be
started with `MCP_HOST=127.0.0.1` so it only listens on the loopback interface.
The container image binds `0.0.0.0`, so do not publish its port on a network
without enabling sign-in.

### Google sign-in

Every request carries a verified Google token. A user then only reaches a
tree if their email matches a Gramps Web user of that tree, or an address in
`GRAMPS_MCP_ADMIN_EMAILS`.

Things to be aware of:

- **Admin emails bypass the check on every tree.** They are a global grant.
- **The service account's role is the ceiling for everyone.** Letting a family
  member sign in does not give them their own Gramps permissions. Every call
  runs as the service account, so an Editor account means every authorised
  user can edit.
- **An Owner account widens the blast radius.** It is required to list users
  for the membership check, but it can also manage users. Weigh that against
  listing emails in `GRAMPS_MCP_ADMIN_EMAILS` instead.
- **The Google consent screen can stay in "Testing" mode** for a small circle
  of people.
- **Secrets stay in the environment.** The Gramps passwords and the Google
  client secret should come from environment variables or a secret manager,
  never from a committed file.

## What is excluded on purpose

User management, token endpoints, saved filters and server configuration are
never exposed as tools. Even an Owner service account cannot be used by an
agent to create users or change settings through MCP. See
[Tools and resources](../reference/tools.md#excluded-routes).
