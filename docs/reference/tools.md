# Tools and resources

## Read tools

For every tree in `GRAMPS_MCP_TREES`, the server downloads that Gramps Web API
instance's `openapi.json` and generates one MCP tool for each **read**
operation: every `GET`, and the `POST .../query/` searches, which take their
criteria in the body. Tools are mounted under a per-tree namespace:

```
tree-<name>_<operationId>
```

For a tree named `example`, the operation `get_people` becomes
`tree-example_get_people`. Every operation has its own `operationId` in Gramps
Web API's OpenAPI document, so the names are stable and predictable.

The tool list follows whichever Gramps Web API version you run. If the backend
adds a read endpoint, it shows up after the next restart.

No generated tool writes. Every other `POST`, `PUT`, `PATCH` and `DELETE`
(including `merge`, the bulk `objects` endpoints, `transactions` and `chat`) is
excluded, so a new write endpoint is never exposed by accident.

## The write tool

`tree-<name>_apply_changes` is the only way to change a tree. It takes one set
of 1 to 20 operations, in order:

| `op` | Fields | Meaning |
|---|---|---|
| `create` | `type`, `object` | Add the full object. `_class` may be left out; if given it must match `type`. |
| `update` | `type`, `handle`, `changes` | Merge `changes`, a nested object holding only the changed fields, into the current object. A list replaces the whole list. |
| `delete` | `type`, `handle` | Delete the object. |

`type` is one of `people`, `families`, `events`, `places`, `citations`,
`sources`, `repositories`, `media`, `notes`, `tags`. Unknown keys are refused,
as are `handle`, `_class` and the read-only fields (`backlinks`, `extended`,
`profile`) in `changes`, a handle longer than 50 characters, and two operations
on the same object.

The server reads the current objects, builds a readable summary of every
change, and asks the user once to confirm, through MCP elicitation. The summary
names things instead of showing handles and raw fields: people, families and
events are named (through the API's `profile=self`, or from the objects created
in the same set), field names become words and a date changes as one line:

```
Apply these 4 change(s) to the tree?

1. Add event: Birth, 10 août 2010
2. Add person: Ileana Viveret
    - sex: female
    - events: Birth, 10 août 2010
3. Add family: family of Jérôme Benoît Viveret
    - father: Jérôme Benoît Viveret
    - children: Ileana Viveret
4. Edit person: Jérôme Benoît Viveret (I0001)
    - families: add family of Jérôme Benoît Viveret
```

The text is also valid Markdown, so clients that render it show a list with the
details nested under each change.

A reference that is not in the tree shows as "a person that is not in the tree"
rather than a handle. Nothing is
written if the user declines or cancels, or if the client cannot ask: a client
without elicitation support gets `not_applied` and the tree is untouched.

Both ways of asking are supported: the question sent in the middle of the call
by clients on protocol versions before `2026-07-28`, and the "input required"
result that the call returns, and the client answers by calling again, from
`2026-07-28` on. In the second flow the server sends a digest of the summary
with its question, and counts the answer only if the summary it rebuilds on the
retry has the same digest. If an object changed while the user was deciding, the
user is asked again about what would now be written; after three tries, nothing
is written.

If the answer is not a confirmation, the result says what the client sent:
`The user declined.`, `The user dismissed the confirmation (cancel).`, or
`The client did not confirm: accept without confirming (content: ...)` when a
client answered "accept" without the `confirm` field set to `true`. The server
logs one line per decision at `INFO`, with the client name and version and the
protocol version, for example `apply_changes confirmation from Copilot 1.2
(protocol 2026-07-28): accept`.

By default a client that cannot ask for confirmation writes nothing. Set
[`GRAMPS_MCP_CONFIRMATION=when-supported`](configuration.md#confirmation-of-writes)
to apply the changes directly for such clients instead; clients that can ask are
still asked.

The tool also carries `_meta["anthropic/requiresUserInteraction"] = true`, which
makes Claude Code show its own approval prompt on every call, whatever the
permission mode. That prompt shows the raw arguments; the readable summary
comes with the confirmation.

Changes are written in order. If one fails, the earlier ones are undone in a
single call to Gramps' transactions endpoint, which applies the undo all or
nothing and refuses it if an object has changed since.

The result has a `status`:

| `status` | Meaning |
|---|---|
| `applied` | Everything was written. Created objects come back with their handle. |
| `declined` | The user did not confirm. |
| `not_applied` | Nothing was written: the client cannot confirm (in `required` mode), or the first change failed. |
| `invalid` | The changes could not be planned, for example an unknown handle. |
| `rolled_back` | A change failed and the earlier ones were undone. |
| `partly_applied` | A change failed and the earlier ones could not be undone; the message says why. |

The preview shows the objects you name, not the ones Gramps updates as a
consequence (editing a family also rewrites its members, deleting an event
rewrites everyone who refers to it). The role of the service account still
bounds what can be written: see
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

The list lives in `EXCLUDED_ROUTE_PREFIXES` in `src/gramps_mcp/trees.py`; the read-only rule is `map_route` in the same file.

### Request handling

- **Output schemas of the read tools are dropped**, since Gramps' response schemas are too loose
  for strict validation to help.
- **Authentication** to Gramps Web is automatic. The server logs in once per
  tree and refreshes the token in the background.

## Resources

| URI | Content |
|---|---|
| `gramps://goql-spec` | A guide to the Gramps Object Query Language ([the same text is here](goql.md)), so an agent can write a valid `where_expr` for the `post_<type>_query` tools before calling them. |

## Interactive card (experimental)

With `GRAMPS_MCP_UI=prefab`, `apply_changes` shows its changes as a card in
clients that render MCP Apps, instead of asking for a confirmation:

![The card of apply_changes: boxes, a name being fixed, buttons](../assets/interactive-card.png)

- **Tick or untick** each change. A change is applied only if the changes it
  needs are ticked too: the person that cites a created event, the family that
  cites the created person, the father's edit that adds the family. Unticking
  one marks what depends on it "Skipped" at once, keeping the user's own boxes,
  so ticking it again brings them back.
![The first change is unticked, so the three that need it are skipped](../assets/interactive-card-skipped.png)

- **Fix a name** before applying: a first name, surname, event description,
  place name, note text, title. Only these text fields of a created object, or
  of an edited one that the change touches, can be edited, up to 500
  characters. Dates, handles and references cannot.
- **Apply** calls `confirm_changes`, a tool the host keeps away from the model
  (`visibility: ["app"]`). The server rebuilds the changes, refuses them if an
  object changed since the card was shown, checks the selection is consistent
  and the edits are allowed, writes what is selected, and the outcome is sent to
  the conversation as a message. **Cancel** writes nothing.
- A card can be applied once, within 30 minutes. They are kept in memory, so a
  restart expires them: ask for the changes again.

A client that does not advertise MCP Apps gets the confirmation described above
(elicitation), as if the card did not exist. `GRAMPS_MCP_UI=prefab-always` shows
the card to every client; one that cannot render it then writes nothing.

Limits to know. The guarantee that the model cannot confirm for the user rests
on the host keeping `confirm_changes` out of the model's tool list, as the MCP
Apps specification asks; check it in your client. The card does not set Claude
Code's `requiresUserInteraction` mark, and `apply_changes` has no output schema
in this mode. The labels that name an object do not follow a name you are
editing until you apply.

## Preview card (experimental)

With `GRAMPS_MCP_UI=prefab`, each tree also gets `preview_changes`. It takes the
same operations as `apply_changes` and writes nothing. It shows the changes as a
colour-coded card in clients that render MCP Apps: additions in green, edits in
orange, deletions in red, an icon for the kind of object, and the old value
struck through next to the new one.

![The preview card: two additions, two edits, one deletion](../assets/preview-changes.png)

A client that does not render MCP Apps gets the same summary as text, headed
"Preview of N change(s). Nothing was written." Which clients render the card is
up to them; this is only the preview, and `apply_changes` still asks for its
confirmation as described above.

## Endpoint

The server speaks MCP's streamable HTTP transport at `/mcp`. With Google
sign-in enabled it also serves the OAuth metadata and authorization endpoints
that MCP clients discover automatically.
