"""Puts the latest release tag into the install snippets at build time."""

from __future__ import annotations

import os
import subprocess

PLACEHOLDER = "%%LATEST_TAG%%"


def _latest_tag() -> str:
    tag = os.environ.get("LATEST_TAG", "").strip()
    if tag:
        return tag
    try:
        described = subprocess.run(
            ["git", "describe", "--tags", "--abbrev=0", "--match", "v[0-9]*"],
            capture_output=True,
            text=True,
            check=True,
        )
        return described.stdout.strip() or "master"
    except (OSError, subprocess.CalledProcessError):
        return "master"


LATEST_TAG = _latest_tag()


def on_page_markdown(markdown: str, **kwargs: object) -> str:
    return markdown.replace(PLACEHOLDER, LATEST_TAG)
