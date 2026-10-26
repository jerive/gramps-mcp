# Writing to a tree from Python

`apply_changes` is the MCP tool, but the machinery under it is ordinary Python
and is importable on its own. Use it when something other than an MCP client
needs to write to a Gramps tree and wants the same validation, ordering and
undo: a script, a web service, a queue of changes a person reviews in your own
interface.

Everything listed in `gramps_mcp.__all__` is supported. Anything else, including
every other module, is internal and may change in any release.

```python
from gramps_mcp import (
    ApplyChangesResult,
    ChangeError,
    ChangeSummary,
    CreateOperation,
    DeleteOperation,
    Failure,
    MAX_OPERATIONS,
    Operations,
    Planned,
    UpdateOperation,
    execute,
    format_confirmation,
    plan,
    rollback,
)
```

## The flow

Three steps, and you own what happens between them.

```python
import httpx2
from pydantic import TypeAdapter
from gramps_mcp import Operations, execute, format_confirmation, plan

operations = TypeAdapter(Operations).validate_python(
    [{"op": "update", "type": "people", "handle": "abc", "changes": {"gender": 1}}]
)

async with httpx2.AsyncClient(
    base_url="https://gramps.example.com",
    headers={"Authorization": f"Bearer {token}"},
) as client:
    planned = await plan(client, operations)  # reads the current objects
    print(format_confirmation(planned))  # show it to a person
    result = await execute(client, planned)  # writes, in order
```

`TypeAdapter(Operations)` validates the whole set: the discriminated union,
unknown keys, read-only and immutable fields, the handle length, the 1 to
`MAX_OPERATIONS` bound and the refusal of two operations on the same
`(type, handle)`. Build the adapter once and reuse it.

`plan` reads each object the set touches, deep-merges an `update` into the
object it found, and builds a readable view of every change. It raises
`ChangeError` when an object does not exist or cannot be read; the message is
safe to show to whoever is deciding.

`format_confirmation` renders the plan as text that is also valid Markdown.
Between showing it and applying it an object may change under you, so plan
again and compare the rendering (or a digest of it) before writing: that is
exactly what the tool does.

`execute` applies the changes in order. If one fails, the ones already written
are undone and the status says so.

## What `ApplyChangesResult.status` guarantees

| status | What is true |
|---|---|
| `applied` | Every change was written. `applied` lists them, with the handle assigned to each created object. |
| `not_applied` | The first change failed; nothing was written. |
| `rolled_back` | A change failed and every earlier one was undone. The tree is as it was. |
| `partly_applied` | A change failed and the earlier ones could **not** be undone. `applied` lists what is still in the tree; `message` says why the undo failed. This is the one case that needs a human. |
| `invalid` | The set could not be planned. Nothing was read or written. |
| `declined` | Only produced by the MCP tool, never by `execute`. |

`failed` carries the change that failed and the error Gramps gave; `not_applied`
lists the changes after it, which were never tried.

## Undoing a set that succeeded

The write endpoints return the Gramps transaction they performed, and replaying
those with `undo=true` is how `execute` undoes a failure. The same call undoes a
set that succeeded, so keep the transactions if you want to offer that:

```python
error = await rollback(client, transactions)
if error is not None:
    ...  # Gramps refused: an object changed since
```

`rollback` returns `None` on success and an error message otherwise. Gramps
replays the inverse of the whole payload, all or nothing, and refuses if any of
the objects has changed since, so an undo either restores everything or does
nothing.

Collecting the transactions is the caller's job: `execute` does not hand them
back. Today it keeps them for its own rollback and returns only the summaries.

## Limits that shape the caller

- **At most `MAX_OPERATIONS` (20) operations per set.** Applying more means
  splitting it, and each set is then its own all-or-nothing unit.
- **Never two operations on the same `(type, handle)`.** Three changes to one
  person have to be merged into a single `update`.
- **A list replaces the whole list.** Adding one `EventRef` means reading the
  person and passing the whole rebuilt `event_ref_list`.
- **`create` refuses `media`.** See below.
- The client must be an `httpx2.AsyncClient` with a `base_url` and an
  `Authorization` header, because that is what these functions call. `httpx`
  and `requests` are not substitutes; a synchronous caller needs a bridge.

## The invariant worth relying on

**No operation writes or erases a single byte of a media file**, so
`rollback`'s all-or-nothing guarantee holds for everything this surface can do.

That is not free: it is why `create` refuses `media`. Gramps takes the file
itself as the body of `POST /api/media/`, and a file written to storage is not
undone by replaying a transaction — the object disappears and the bytes stay,
still counted against the tree's media quota. Deleting is safe in the other
direction: Gramps never removes a media file, so undoing a `delete media`
restores a working media object. Keeping byte-writing out of the operations is
what keeps the guarantee honest.

## Sharp edges

- **`execute` does not catch transport errors.** A refusal from Gramps is
  handled and triggers the rollback, but a connection dropping mid-set raises
  out of `execute`, leaving the earlier changes written and not undone. Wrap the
  call if that matters, and treat it like `partly_applied`.
- **Importing pulls the server in, but nothing starts.** `gramps_mcp` reaches
  `fastmcp`, `mcp`, `starlette`, `uvicorn` and the Redis-backed key-value store
  through its logger, even when all you want is `plan`. They are declared
  dependencies, so nothing is missing, and the import opens no connection and
  builds no provider or store: it is bulky, not active.
- **Importing does configure logging.** The `gramps_mcp` logger gets a
  `StreamHandler` on stdout and the level of `GRAMPS_MCP_LOG_LEVEL` (`INFO` by
  default), and it still propagates. A host application that configures the root
  logger therefore sees every line from this package twice, once in its own
  format and once in ours — and keeps seeing the `INFO` ones even if it runs at
  `WARNING`, because the level is set on our logger and a propagated record is
  not filtered again. An application with its own logging takes it back with:

    ```python
    logger = logging.getLogger("gramps_mcp")
    logger.handlers.clear()  # your format
    logger.setLevel(logging.NOTSET)  # your level, inherited from the root
    ```

    Keeping our handler and setting `propagate = False` works too, if our format
    on stdout is what you want. Configuring the logger yourself *before*
    importing is not a third way: the handler is only added when the logger has
    none, but the level is set either way, and relying on import order is
    fragile anyway.
- **`Operations` does not need the server to be wired.** The per-type object
  schemas the MCP tool advertises come from a running backend's OpenAPI document
  and only affect the JSON schema shown to a model, never validation, so a
  library caller can ignore them.
