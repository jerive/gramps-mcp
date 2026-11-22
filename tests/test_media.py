"""The one write that does not go through `apply_changes`.

What these tests pin down is less the happy path than the two properties the
design rests on: the bytes reach Gramps whole, and they never become something
the model can read back.
"""

from __future__ import annotations

import base64
from typing import Any

import httpx2
import pytest
from fastmcp import Client, FastMCP

from gramps_mcp.changes import OneTree
from gramps_mcp.media import media_upload_enabled, register_media_upload, sniff

JPEG = b"\xff\xd8\xff\xe0" + b"VIVERET" * 400 + b"\xff\xd9"
PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 64
PDF = b"%PDF-1.7\n" + b"0" * 64


def dropped(data: bytes, name: str = "acte.jpg", type_: str = "image/jpeg") -> dict:
    return {
        "name": name,
        "size": len(data),
        "type": type_,
        "data": base64.b64encode(data).decode(),
    }


class Gramps:
    """A Gramps that remembers the body and the type of each upload."""

    def __init__(self, status: int = 201) -> None:
        self.status = status
        self.uploads: list[tuple[str, bytes]] = []

    def handler(self, request: httpx2.Request) -> httpx2.Response:
        assert request.url.path == "/api/media/", request.url.path
        body = request.content
        if self.status >= 400:
            return httpx2.Response(
                self.status, json={"error": {"message": "Not allowed by media quota"}}
            )
        self.uploads.append((request.headers["content-type"], body))
        return httpx2.Response(
            self.status,
            json=[
                {
                    "type": "add",
                    "_class": "Media",
                    "handle": "M-NEW",
                    "old": None,
                    "new": {
                        "handle": "M-NEW",
                        "gramps_id": "O0042",
                        "checksum": "dead-beef",
                    },
                }
            ],
        )

    def client(self) -> httpx2.AsyncClient:
        return httpx2.AsyncClient(
            base_url="http://gramps", transport=httpx2.MockTransport(self.handler)
        )


def build(gramps: Gramps) -> FastMCP:
    mcp = FastMCP("test")
    register_media_upload(mcp, OneTree(gramps.client(), "viveret"))
    return mcp


async def drop(client: Client, *files: dict[str, Any]) -> Any:
    return await client.call_tool("store_files", {"files": list(files)})


def test_the_upload_is_off_unless_asked_for(monkeypatch):
    monkeypatch.delenv("GRAMPS_MCP_MEDIA_UPLOAD", raising=False)
    assert not media_upload_enabled()
    monkeypatch.setenv("GRAMPS_MCP_MEDIA_UPLOAD", " On ")
    assert media_upload_enabled()
    monkeypatch.setenv("GRAMPS_MCP_MEDIA_UPLOAD", "off")
    assert not media_upload_enabled()
    monkeypatch.setenv("GRAMPS_MCP_MEDIA_UPLOAD", "yes")
    with pytest.raises(ValueError, match="GRAMPS_MCP_MEDIA_UPLOAD"):
        media_upload_enabled()


@pytest.mark.parametrize(
    ("data", "expected"),
    [
        (JPEG, "image/jpeg"),
        (PNG, "image/png"),
        (PDF, "application/pdf"),
        (b"II*\x00" + b"0" * 32, "image/tiff"),
        (b"MM\x00*" + b"0" * 32, "image/tiff"),
        (b"RIFF\x10\x00\x00\x00WEBPVP8 ", "image/webp"),
    ],
)
def test_the_type_is_read_from_the_file_itself(data, expected):
    assert sniff(data) == expected


def test_a_file_we_do_not_accept_is_refused_by_its_contents():
    with pytest.raises(Exception, match="not one this server accepts"):
        sniff(b"<svg xmlns='http://www.w3.org/2000/svg'></svg>")


async def test_a_dropped_file_reaches_gramps_whole():
    gramps = Gramps()
    async with Client(build(gramps)) as client:
        await drop(client, dropped(JPEG))
        result = await client.call_tool(
            "file_media", {"name": "acte.jpg", "tree": "viveret"}
        )

    assert result.data["handle"] == "M-NEW"
    assert result.data["gramps_id"] == "O0042"
    assert result.data["checksum"] == "dead-beef"
    assert result.data["mime"] == "image/jpeg"
    [(sent_type, sent_body)] = gramps.uploads
    assert sent_type == "image/jpeg"
    assert sent_body == JPEG, "the bytes Gramps stores must be the bytes dropped"


async def test_the_declared_type_is_ignored_in_favour_of_the_contents():
    # A PDF announced as a JPEG: Gramps turns the type into the stored path's
    # extension, so believing the client would make the lie permanent.
    gramps = Gramps()
    async with Client(build(gramps)) as client:
        await drop(client, dropped(PDF, name="acte.pdf", type_="image/jpeg"))
        await client.call_tool("file_media", {"name": "acte.pdf", "tree": "viveret"})

    [(sent_type, _)] = gramps.uploads
    assert sent_type == "application/pdf"


async def test_a_file_is_poured_once_and_then_forgotten():
    gramps = Gramps()
    async with Client(build(gramps)) as client:
        await drop(client, dropped(JPEG))
        await client.call_tool("file_media", {"name": "acte.jpg", "tree": "viveret"})
        with pytest.raises(Exception, match="No file named"):
            await client.call_tool(
                "file_media", {"name": "acte.jpg", "tree": "viveret"}
            )

    assert len(gramps.uploads) == 1


async def test_asking_for_a_file_nobody_dropped_says_what_is_waiting():
    gramps = Gramps()
    async with Client(build(gramps)) as client:
        await drop(client, dropped(JPEG, name="acte-1876.jpg"))
        with pytest.raises(Exception, match="acte-1876.jpg"):
            await client.call_tool(
                "file_media", {"name": "autre.jpg", "tree": "viveret"}
            )

    assert gramps.uploads == []


async def test_nothing_is_dropped_at_all():
    gramps = Gramps()
    async with Client(build(gramps)) as client:
        with pytest.raises(Exception, match="expired"):
            await client.call_tool(
                "file_media", {"name": "acte.jpg", "tree": "viveret"}
            )


async def test_a_full_quota_is_said_plainly():
    gramps = Gramps(status=405)
    async with Client(build(gramps)) as client:
        await drop(client, dropped(JPEG))
        with pytest.raises(Exception, match="media quota"):
            await client.call_tool(
                "file_media", {"name": "acte.jpg", "tree": "viveret"}
            )


async def test_a_refused_file_is_never_sent_to_gramps():
    gramps = Gramps()
    svg = b"<svg xmlns='http://www.w3.org/2000/svg'></svg>"
    async with Client(build(gramps)) as client:
        await drop(client, dropped(svg, name="acte.svg", type_="image/jpeg"))
        with pytest.raises(Exception, match="not one this server accepts"):
            await client.call_tool(
                "file_media", {"name": "acte.svg", "tree": "viveret"}
            )

    assert gramps.uploads == []


async def test_the_model_can_never_read_the_bytes_back():
    """`read_file` is visible to the model. It must only ever give a preview."""
    gramps = Gramps()
    async with Client(build(gramps)) as client:
        await drop(client, dropped(JPEG))
        read = await client.call_tool("read_file", {"name": "acte.jpg"})

    preview = read.data.get("content_base64", "")
    assert preview
    assert len(preview) < len(base64.b64encode(JPEG).decode()) / 4
    assert base64.b64encode(JPEG).decode() not in preview


async def test_the_drop_tool_is_kept_away_from_the_model():
    """The property the whole design leans on, and it is fastmcp's to keep:
    `store_files` is declared for the app only, like `confirm_changes`."""
    gramps = Gramps()
    async with Client(build(gramps)) as client:
        tools = {tool.name: (tool.meta or {}) for tool in await client.list_tools()}

    assert tools["store_files"]["ui"]["visibility"] == ["app"]
    assert "model" in tools["file_manager"]["ui"]["visibility"]
    assert "model" in tools["read_file"]["ui"]["visibility"]
    assert "file_media" in tools
