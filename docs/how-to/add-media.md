# Add a media file

A media object is the one Gramps object `apply_changes` cannot create. Gramps
takes the file itself as the body of `POST /api/media/` and derives the path,
the MIME type and the checksum from it, so there is nothing a JSON operation
could carry. Nor could a tool argument carry it: an argument is written by the
model, token by token, and a scanned act is megabytes of it.

So the file comes from your own hands. Turn this on and your client grows a drop
zone; what you drop goes to the server, never to the model.

## Turn it on

```bash
GRAMPS_MCP_MEDIA_UPLOAD=on
```

Off by default. This is the only write in the server that does not go through
`apply_changes`, so you opt into it deliberately.

It needs a client that renders MCP Apps, the same ones that show the
[interactive card](../reference/tools.md#interactive-card-experimental). A
client that does not render them has no drop zone, and you add the file in
Gramps Web instead — everything after that step works the same.

## Add one

1. Ask the assistant for the upload card, or call `file_manager`. A drop zone
   appears.
2. Drop a scan or a PDF and press **Upload to Server**. The file waits on the
   server; nothing is written to any tree yet.
3. Say where it goes. The assistant calls `file_media`, which posts the file to
   that tree and returns the media's `handle`, `gramps_id` and `checksum`.
4. Describe and attach it with `apply_changes` as usual: `update media` for the
   description, the date or a tag, `update events` to add the media reference to
   the act it belongs to.

Steps 3 and 4 are separate on purpose. Only step 3 writes bytes; everything that
touches the graph stays in `apply_changes`, with its card, its confirmation and
its all-or-nothing rollback.

## Limits

| | |
|---|---|
| Accepted | JPEG, PNG, TIFF, WebP, PDF — recognised by the file's own first bytes, not by its name or by what the browser announced |
| One file | 25 MB |
| Waiting at once | 100 MB |
| How long a dropped file waits | 15 minutes, and a restart of the server forgets it |

A file that waits too long is simply gone: drop it again. A file that reaches a
tree is forgotten immediately, so it cannot be filed twice.

If your reverse proxy caps request bodies, raise that cap for this server: the
file travels base64-encoded inside the tool call, so 25 MB of scan needs about
40 MB of room.

## What this does not do

- **Undo.** Writing a file cannot be undone. `delete media` removes the object,
  and Gramps keeps the blob in storage, where it still counts against the tree's
  media quota. That asymmetry is exactly why this write is kept out of
  `apply_changes`, whose rollback would otherwise be promising more than it can
  do.
- **Attribution.** The server writes as its service account, so Gramps records
  the upload under that account and not under you.
- **Bulk.** For a backlog of scans, use Gramps Web's own zip import. This is for
  the act you have in front of you.
