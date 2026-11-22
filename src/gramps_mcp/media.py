"""Adding a media file to a tree, with its bytes.

A media object is the one Gramps object `apply_changes` cannot create:
`POST /api/media/` takes the file itself as the request body, derives `path`,
`mime` and the checksum from it, and ignores every field a JSON operation could
carry. The bytes cannot travel in a tool argument either -- an argument is
written by the model, token by token, and a scanned act is megabytes of it.

So the bytes come from the user's own hands. `FileUpload` renders a drop zone in
hosts that render MCP Apps and hands what is dropped to `store_files`, a tool
the host keeps away from the model, exactly as `confirm_changes` is kept away in
`ui.py`. `file_media` then pours one dropped file into the tree the conversation
names.

Why this one write lives outside `apply_changes`: writing a file is not
undoable. Replaying a transaction with `undo=true` removes the media object and
leaves the blob in storage, where it keeps counting against the tree's media
quota. Keeping byte-writing out is what lets `apply_changes` promise that a
rolled-back set left the tree as it was. Everything that touches the graph --
the description, the reference from an event, the citation -- stays an ordinary
`update`.
"""

from __future__ import annotations

import base64
import os
from typing import Any

from fastmcp import Context, FastMCP

# Not re-exported from `fastmcp.apps`, though the module's own docstring says it
# is: `from fastmcp.apps import FileUpload` raises ImportError in 4.1.4 and in
# 4.1.6.
from fastmcp.apps.file_upload import FileUpload
from fastmcp.exceptions import ToolError
from mcp.types import ToolAnnotations

from .changes import (
    TreeClients,
    _created_handle,
    _error_message,
    client_label,
    with_tree_argument,
)
from .core import logger

# What we accept, recognised by the file's own first bytes. Gramps' guard is
# `get_extension(mime)`, which takes almost anything -- `application/json`
# becomes `.json` -- so the useful barrier has to be ours. No `text/html` and no
# `image/svg+xml`: `/api/media/{handle}/file` serves back what we store, and
# active content served from the Gramps origin is not worth the convenience.
SIGNATURES: tuple[tuple[bytes, str], ...] = (
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"%PDF-", "application/pdf"),
    (b"II*\x00", "image/tiff"),
    (b"MM\x00*", "image/tiff"),
)
ACCEPTED = "JPEG, PNG, TIFF, WebP and PDF"

# An act scanned at 300 dpi runs 1 to 5 MB; a multipage PDF, 10 to 20. The
# bytes sit in this process between the drop and the pour, and Gramps buffers
# the whole file again on its side, so these are memory, not just policy.
MAX_FILE_BYTES = 25 * 1024 * 1024
MAX_TOTAL_BYTES = 100 * 1024 * 1024
FILE_TTL_SECONDS = 900


def media_upload_enabled() -> bool:
    """Whether the drop zone and `file_media` are registered at all.

    Off by default, and deliberately so: this is a public package, and nobody
    should gain a way of writing files into their trees by upgrading.
    """
    value = os.environ.get("GRAMPS_MCP_MEDIA_UPLOAD", "").strip().lower()
    if value in ("", "off"):
        return False
    if value == "on":
        return True
    raise ValueError(f"GRAMPS_MCP_MEDIA_UPLOAD must be one of on, off, got {value!r}")


def sniff(data: bytes) -> str:
    """The type of a file, read from the file itself.

    Never the type the browser announced. That type is what Gramps turns into
    the extension of the path it stores, so a lie about it becomes permanent.
    """
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    for magic, mime in SIGNATURES:
        if data.startswith(magic):
            return mime
    raise ToolError(
        f"That file is not one this server accepts ({ACCEPTED}), going by its "
        "contents rather than its name."
    )


class MediaUpload(FileUpload):
    """The drop zone, keeping each file only until it is poured into a tree.

    Storage, size limits and expiry are the stock ones; this subclass only
    remembers the base64 of what it stored, so that `file_media` can post the
    whole file. It holds references to the very strings the parent keeps, so
    remembering them costs nothing, and it drops a name as soon as the parent
    stops listing it.

    The inherited `read_file`, which the model *can* call, returns a truncated
    preview. So the bytes go in, get poured, and never become something the
    model can read back out.
    """

    def __init__(self) -> None:
        super().__init__(
            name="Media",
            max_file_size=MAX_FILE_BYTES,
            max_total_size=MAX_TOTAL_BYTES,
            file_ttl_seconds=FILE_TTL_SECONDS,
            title="Add a media file",
            description=(
                "Drop a scanned act here. It is uploaded to the server, not to "
                "the model, and nothing is written to a tree until you say "
                "which one."
            ),
            drop_label="Drop a scan or a PDF here",
        )
        self._bytes: dict[tuple[str, str], str] = {}

    # `_get_scope_key` carries an underscore but is the documented way to
    # partition storage, and overriding it is the supported answer to stateless
    # deployments. Using it keeps this class off the parent's `_store`.
    def _scope(self, ctx: Context) -> str:
        return self._get_scope_key(ctx)

    def on_store(
        self, files: list[dict[str, Any]], ctx: Context
    ) -> list[dict[str, Any]]:
        summaries = super().on_store(files, ctx)
        scope = self._scope(ctx)
        for file in files:
            self._bytes[(scope, file["name"])] = file["data"]
        live = {summary["name"] for summary in summaries}
        for key in [k for k in self._bytes if k[0] == scope and k[1] not in live]:
            del self._bytes[key]
        return summaries

    def pending(self, name: str, ctx: Context) -> bytes:
        """The whole file dropped under this name, for `file_media` alone."""
        scope = self._scope(ctx)
        listed = {summary["name"] for summary in self.on_list(ctx)}
        if name not in listed:
            self._bytes.pop((scope, name), None)
        data = self._bytes.get((scope, name)) if name in listed else None
        if data is None:
            dropped = sorted(listed)
            raise ToolError(
                f"No file named {name!r} is waiting. "
                + (
                    f"Waiting now: {', '.join(dropped)}."
                    if dropped
                    else "Nothing has been dropped, or what was dropped has "
                    "expired: a file waits 15 minutes, and a restart of the "
                    "server forgets it. Drop it again."
                )
            )
        return base64.b64decode(data)

    def forget(self, name: str, ctx: Context) -> None:
        """Lets go of a file that reached a tree, so it cannot be poured twice."""
        self._bytes.pop((self._scope(ctx), name), None)


FILE_MEDIA_DESCRIPTION = (
    "Put a file the user dropped into a tree, and return the media object it "
    "became. `name` is the file's name as the drop zone lists it; `list_files` "
    "names what is waiting. This writes the file and nothing else: to give the "
    "media a description or a date, or to attach it to an event, follow with "
    "`apply_changes` on the handle this returns. You cannot supply the file "
    "yourself -- only a file the user dropped can be filed."
)


def register_media_upload(server: FastMCP, trees: TreeClients) -> None:
    """Adds the drop zone and `file_media` to `server`."""
    uploads = MediaUpload()
    server.add_provider(uploads)

    async def file_media(
        name: str,
        tree: Any = None,
        ctx: Context = None,  # type: ignore[assignment]
    ) -> dict[str, Any]:
        client = trees.client(tree)
        data = uploads.pending(name, ctx)
        mime = sniff(data)
        logger.info(
            "file_media from %s: %s (%s, %d bytes) into %s",
            client_label(ctx),
            name,
            mime,
            len(data),
            tree,
        )
        response = await client.post(
            "/api/media/", content=data, headers={"Content-Type": mime}
        )
        if response.status_code == 405:
            raise ToolError(
                f"The tree {tree!r} is at its media quota, so Gramps refused the "
                "file. Nothing was written."
            )
        if response.status_code >= 400:
            raise ToolError(
                f"Gramps refused the file: {_error_message(response)}. "
                "Nothing was written."
            )
        transaction = response.json()
        handle = _created_handle(transaction, "Media")
        if handle is None:
            raise ToolError(
                "Gramps accepted the file but named no media object, so there "
                "is nothing to attach it to."
            )
        uploads.forget(name, ctx)
        created = next(
            (
                item.get("new") or {}
                for item in transaction
                if item.get("handle") == handle
            ),
            {},
        )
        return {
            "tree": tree,
            "handle": handle,
            "gramps_id": created.get("gramps_id"),
            "checksum": created.get("checksum"),
            "mime": mime,
            "size": len(data),
        }

    server.tool(
        name="file_media",
        description=FILE_MEDIA_DESCRIPTION,
        annotations=ToolAnnotations(read_only_hint=False, open_world_hint=False),
    )(with_tree_argument(file_media, trees.ids))
