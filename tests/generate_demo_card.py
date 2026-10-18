"""Generate the demo Shakespeare Prefab UI card HTML and screenshot."""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
from pathlib import Path

# Ensure src/ and tests/ are in sys.path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

from fastmcp import FastMCP  # noqa: E402
from prefab_ui.app import PrefabApp  # noqa: E402
from test_interactive import FakeGramps, show_card  # noqa: E402

from gramps_mcp.ui import register_ui  # noqa: E402

OLD_DEATH = {
    "handle": "e_old_death",
    "type": "Death",
    "description": "Estimated Death in London, 1615",
    "date": {"text": "1615"},
}

WS = {
    "handle": "p_ws",
    "gramps_id": "I0001",
    "gender": 1,
    "primary_name": {
        "first_name": "William",
        "surname_list": [{"surname": "Shakespeare"}],
    },
    "event_ref_list": [{"ref": "e_old_death"}],
    "family_list": [],
}

SHAKESPEARE_SCENARIO = [
    {
        "op": "create",
        "type": "events",
        "object": {
            "handle": "e_marr",
            "type": "Marriage",
            "description": "Marriage to Anne Hathaway",
            "date": {"text": "late November 1582"},
        },
    },
    {
        "op": "create",
        "type": "people",
        "object": {
            "handle": "p_anne",
            "gender": 0,
            "primary_name": {
                "first_name": "Anne",
                "surname_list": [{"surname": "Hathaway"}],
            },
        },
    },
    {
        "op": "create",
        "type": "families",
        "object": {
            "handle": "f_ws_anne",
            "father_handle": "p_ws",
            "mother_handle": "p_anne",
            "event_ref_list": [{"ref": "e_marr"}],
        },
    },
    {
        "op": "create",
        "type": "events",
        "object": {
            "handle": "e_death",
            "type": "Death",
            "description": "Death in Stratford-upon-Avon",
            "date": {"text": "23 April 1616"},
        },
    },
    {
        "op": "update",
        "type": "people",
        "handle": "p_ws",
        "changes": {
            "family_list": ["f_ws_anne"],
            "event_ref_list": [{"ref": "e_death"}],
        },
    },
    {
        "op": "delete",
        "type": "events",
        "handle": "e_old_death",
    },
]


async def generate(take_screenshot: bool = True) -> tuple[Path, Path | None]:
    gramps = FakeGramps(
        {
            "p_ws": json.loads(json.dumps(WS)),
            "e_old_death": json.loads(json.dumps(OLD_DEATH)),
        }
    )
    server = FastMCP()
    register_ui(server, gramps.client(), confirmation="required")

    result = await show_card(server, SHAKESPEARE_SCENARIO)
    app = PrefabApp.from_json(result.structured_content)
    html_output = app.html(renderer_mode="cdn")

    assets_dir = ROOT / "docs" / "assets"
    assets_dir.mkdir(parents=True, exist_ok=True)
    html_path = assets_dir / "shakespeare-card.html"
    html_path.write_text(html_output, encoding="utf-8")
    print(f"✅ Generated bundled HTML: {html_path}")

    png_path = assets_dir / "shakespeare-card.png"
    if take_screenshot:
        cmd = [
            "google-chrome",
            "--headless=new",
            "--disable-gpu",
            "--hide-scrollbars",
            "--virtual-time-budget=2000",
            "--run-all-compositor-stages-before-draw",
            f"--screenshot={png_path}",
            "--window-size=740,960",
            f"file://{html_path}",
        ]
        try:
            subprocess.run(cmd, check=True, capture_output=True)
            print(f"✅ Captured screenshot: {png_path}")
        except (subprocess.CalledProcessError, FileNotFoundError) as e:
            print(f"⚠️ Could not capture screenshot via chrome: {e}")
            return html_path, None
        return html_path, png_path
    return html_path, None


if __name__ == "__main__":
    screenshot = "--screenshot" in sys.argv or "-s" in sys.argv
    asyncio.run(generate(take_screenshot=screenshot))
