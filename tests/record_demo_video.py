# ruff: noqa: E501
"""Automated screencast recorder for gramps-mcp Elicitation / Prefab UI flow.

Simulates VS Code Copilot Chat interaction, tool invocations, Prefab UI
interactive card review with dependency cascade, live editing, and confirmation.
Records video using Playwright and encodes to MP4 and WebM using ffmpeg.
"""

from __future__ import annotations

import asyncio
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

STAGE_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <title>Copilot Chat - Gramps MCP Demo</title>
  <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/@vscode/codicons/dist/codicon.css">
  <style>
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      background-color: #1e1e1e;
      color: #cccccc;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
      overflow-x: hidden;
      display: flex;
      flex-direction: column;
      height: 100vh;
      user-select: none;
    }
    /* VS Code Titlebar */
    .titlebar {
      background: #323233;
      height: 35px;
      display: flex;
      align-items: center;
      padding: 0 12px;
      font-size: 12px;
      color: #999;
      border-bottom: 1px solid #252526;
      flex-shrink: 0;
    }
    .window-controls {
      display: flex;
      gap: 8px;
      margin-right: 16px;
    }
    .dot { width: 12px; height: 12px; border-radius: 50%; display: inline-block; }
    .dot-red { background: #ff5f56; }
    .dot-yellow { background: #ffbd2e; }
    .dot-green { background: #27c93f; }
    .titlebar-text { flex: 1; text-align: center; color: #cccccc; font-weight: 500; }

    /* App container */
    .app-body {
      display: flex;
      flex: 1;
      overflow: hidden;
    }
    /* Activity Bar */
    .activity-bar {
      width: 48px;
      background: #333333;
      display: flex;
      flex-direction: column;
      align-items: center;
      padding: 12px 0;
      gap: 18px;
      flex-shrink: 0;
      color: #858585;
      font-size: 20px;
    }
    .activity-bar .active { color: #ffffff; position: relative; }
    .activity-bar .active::before {
      content: "";
      position: absolute;
      left: -14px;
      top: -2px;
      width: 2px;
      height: 24px;
      background: #007acc;
    }

    /* Chat Container */
    .chat-container {
      flex: 1;
      display: flex;
      flex-direction: column;
      background: #1e1e1e;
      overflow-y: auto;
      padding: 24px 32px 60px;
      gap: 20px;
      max-width: 900px;
      margin: 0 auto;
      width: 100%;
    }

    /* Message bubbles */
    .msg-user {
      align-self: flex-end;
      background: #0e639c;
      color: #ffffff;
      padding: 14px 18px;
      border-radius: 12px 12px 2px 12px;
      max-width: 85%;
      font-size: 13.5px;
      line-height: 1.5;
      box-shadow: 0 2px 8px rgba(0,0,0,0.25);
    }
    .msg-user-header {
      font-size: 11px;
      text-transform: uppercase;
      letter-spacing: 0.5px;
      color: #b3d7ff;
      margin-bottom: 6px;
      font-weight: 600;
    }

    .msg-assistant {
      align-self: flex-start;
      display: flex;
      flex-direction: column;
      gap: 12px;
      width: 100%;
    }
    .assistant-header {
      display: flex;
      align-items: center;
      gap: 8px;
      font-size: 13px;
      font-weight: 600;
      color: #3794ff;
    }

    /* Tool Call Card */
    .tool-box {
      background: #252526;
      border: 1px solid #3c3c3c;
      border-radius: 8px;
      padding: 10px 14px;
      font-size: 12.5px;
      font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
      display: flex;
      flex-direction: column;
      gap: 6px;
    }
    .tool-header {
      display: flex;
      align-items: center;
      gap: 8px;
      color: #9cdcfe;
    }
    .tool-pill {
      background: #333333;
      padding: 2px 6px;
      border-radius: 4px;
      font-size: 11px;
      color: #4ec9b0;
    }
    .tool-result {
      color: #6a9955;
      font-size: 12px;
      padding-left: 20px;
    }

    /* Card Frame */
    .card-wrapper {
      background: #ffffff;
      border-radius: 12px;
      overflow: hidden;
      box-shadow: 0 4px 20px rgba(0,0,0,0.4);
      margin: 8px 0;
    }
    iframe {
      width: 100%;
      height: 960px;
      border: none;
      display: block;
    }

    /* Smooth animated cursor */
    #cursor {
      position: fixed;
      width: 22px;
      height: 22px;
      pointer-events: none;
      z-index: 999999;
      transform: translate(-3px, -2px);
      transition: transform 0.08s ease;
    }
    .click-ripple {
      position: fixed;
      width: 32px;
      height: 32px;
      border-radius: 50%;
      background: rgba(59, 130, 246, 0.4);
      border: 2px solid #3b82f6;
      pointer-events: none;
      z-index: 999998;
      transform: translate(-50%, -50%) scale(0);
      animation: ripple 0.45s ease-out forwards;
    }
    @keyframes ripple {
      to { transform: translate(-50%, -50%) scale(2); opacity: 0; }
    }
  </style>
</head>
<body>
  <div class="titlebar">
    <div class="window-controls">
      <span class="dot dot-red"></span>
      <span class="dot dot-yellow"></span>
      <span class="dot dot-green"></span>
    </div>
    <div class="titlebar-text">gramps-mcp — Visual Studio Code (Copilot Chat)</div>
  </div>

  <div class="app-body">
    <div class="activity-bar">
      <span>📄</span>
      <span>🔍</span>
      <span>🌿</span>
      <span class="active">💬</span>
      <span>⚙️</span>
    </div>

    <div class="chat-container" id="chat">
      <!-- User message -->
      <div class="msg-user">
        <div class="msg-user-header">User &bull; In tree viveret</div>
        William Shakespeare was born circa 23 April 1564 (baptized 26 April 1564) and died on 23 April 1616 in Stratford-upon-Avon; he married Anne Hathaway in late November 1582 in Warwickshire (under a marriage bond issued on 28 November 1582, with Stratford traditionally cited), having three children born in Stratford-upon-Avon—Susanna (baptized 26 May 1583) and twins Hamnet and Judith (baptized 2 February 1585)—while his parents, John Shakespeare and Mary Arden, married around 1557, typically believed to have taken place in Aston Cantlow. Please update William Shakespeare in the tree accordingly.
      </div>

      <div class="msg-assistant">
        <div class="assistant-header">
          <span>✨ GitHub Copilot</span>
        </div>

        <!-- Read Tool Call -->
        <div class="tool-box" id="tool-search">
          <div class="tool-header">
            <span>⚙️ tool call:</span>
            <span class="tool-pill">tree-viveret_get_search</span>
            <span>{"query": "William Shakespeare"}</span>
          </div>
          <div class="tool-result">
            ✓ Found 1 person: William Shakespeare (ID: I0001, handle: p_ws), with existing event: Estimated Death in London, 1615
          </div>
        </div>

        <div style="font-size: 13.5px; line-height: 1.5; color: #d4d4d4;">
          William Shakespeare is in the tree with an estimated death record from 1615. I've prepared an atomic set of changes: creating Anne Hathaway and their marriage event, recording his confirmed death in Stratford, linking his family, and deleting the obsolete 1615 event. Please review:
        </div>

        <!-- Apply Changes Tool Call with Prefab UI Card -->
        <div class="tool-box" id="tool-apply">
          <div class="tool-header">
            <span>⚙️ tool call:</span>
            <span class="tool-pill">tree-viveret_apply_changes</span>
            <span>{"operations": [6 operations]}</span>
          </div>
          <div style="color: #4ec9b0; font-size: 12px; margin-top: 4px;">
            ⚡ Elicitation requested: Interactive review card rendered below
          </div>
        </div>

        <!-- Card Frame -->
        <div class="card-wrapper" id="card-wrapper">
          <iframe id="card-iframe" src="shakespeare-card.html"></iframe>
        </div>

        <!-- Success Tool Call (Hidden from model, called by App) -->
        <div class="tool-box" id="tool-confirm" style="display: none;">
          <div class="tool-header">
            <span style="color: #4ec9b0;">⚡ app action:</span>
            <span class="tool-pill">tree-viveret_confirm_changes</span>
            <span>{"token": "auth_token_8a9f...", "selection": {...}}</span>
          </div>
          <div class="tool-result">
            ✓ status: "applied", 6 changes committed (transaction: tx_9f14)
          </div>
        </div>

        <!-- Final Message -->
        <div id="final-message" style="display: none; font-size: 13.5px; line-height: 1.5; color: #d4d4d4; background: #252526; border-left: 3px solid #4ec9b0; padding: 12px 16px; border-radius: 4px;">
          ✅ <strong>Changes successfully applied!</strong> William Shakespeare's tree has been updated with Anne Hathaway and their marriage event, his confirmed death in Stratford-upon-Avon (23 April 1616) has been recorded, and the outdated 1615 estimated death event has been removed.
        </div>
      </div>
    </div>
  </div>

  <!-- Simulated cursor -->
  <svg id="cursor" viewBox="0 0 24 24" fill="none">
    <path d="M5.5 3.2L18.8 12.8L12.4 14.2L15.6 20.4L13.2 21.6L10 15.4L5.5 19V3.2Z" fill="#111827" stroke="#ffffff" stroke-width="1.8"/>
  </svg>

  <script>
    const cursor = document.getElementById('cursor');
    function moveCursor(x, y) {
      cursor.style.left = x + 'px';
      cursor.style.top = y + 'px';
    }
    function ripple(x, y) {
      const r = document.createElement('div');
      r.className = 'click-ripple';
      r.style.left = x + 'px';
      r.style.top = y + 'px';
      document.body.appendChild(r);
      setTimeout(() => r.remove(), 500);
    }
  </script>
</body>
</html>
"""


async def record_screencast():
    assets_dir = ROOT / "docs" / "assets"
    assets_dir.mkdir(parents=True, exist_ok=True)
    stage_path = assets_dir / "demo_stage.html"
    stage_path.write_text(STAGE_HTML, encoding="utf-8")

    from playwright.async_api import async_playwright

    record_dir = assets_dir / "raw_video"
    if record_dir.exists():
        shutil.rmtree(record_dir)
    record_dir.mkdir(parents=True, exist_ok=True)

    print("🎥 Launching Chrome to record screencast...")
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            executable_path="/usr/bin/google-chrome",
            headless=True,
            args=[
                "--disable-gpu",
                "--hide-scrollbars",
                "--no-sandbox",
            ],
        )

        context = await browser.new_context(
            record_video_dir=str(record_dir),
            record_video_size={"width": 1280, "height": 720},
            viewport={"width": 1280, "height": 720},
        )

        page = await context.new_page()
        await page.goto(f"file://{stage_path.resolve()}")
        await page.wait_for_timeout(1000)

        # Wait for iframe to load
        iframe_element = page.frame_locator("#card-iframe")
        await page.wait_for_timeout(2000)

        async def smooth_move(target_x, target_y, steps=25):
            cur_pos = await page.evaluate(
                "() => ({x: parseFloat(cursor.style.left) || 640, y: parseFloat(cursor.style.top) || 360})"
            )
            start_x, start_y = cur_pos["x"], cur_pos["y"]
            for i in range(1, steps + 1):
                t = i / steps
                # easeInOutCubic
                e = 4 * t * t * t if t < 0.5 else 1 - pow(-2 * t + 2, 3) / 2
                x = start_x + (target_x - start_x) * e
                y = start_y + (target_y - start_y) * e
                await page.evaluate(f"moveCursor({x}, {y})")
                await asyncio.sleep(0.02)

        async def click_at(x, y):
            await page.evaluate(f"ripple({x}, {y})")
            await page.wait_for_timeout(200)

        # Initial scroll & presentation
        await smooth_move(640, 200, 30)
        await page.wait_for_timeout(1500)

        # Scroll down smoothly to show the Prefab card
        for scroll_step in range(1, 15):
            await page.evaluate(
                f"document.getElementById('chat').scrollTop = {scroll_step * 35}"
            )
            await page.wait_for_timeout(50)

        await page.wait_for_timeout(2000)

        # Locate checkbox 2 (Anne Hathaway) inside iframe
        # Get its bounding box in viewport coordinates
        iframe_box = await (await page.query_selector("#card-iframe")).bounding_box()
        assert iframe_box is not None

        boxes = iframe_element.locator('[role="checkbox"]')
        box1 = boxes.nth(1)  # Anne Hathaway
        box1_coords = await box1.bounding_box()

        if box1_coords:
            target_x = iframe_box["x"] + box1_coords["x"] + box1_coords["width"] / 2
            target_y = iframe_box["y"] + box1_coords["y"] + box1_coords["height"] / 2

            # Move to Anne Hathaway checkbox
            print("👉 Moving cursor to Anne Hathaway checkbox...")
            await smooth_move(target_x, target_y, 35)
            await page.wait_for_timeout(600)

            # Click to untick
            print("👉 Unticking Anne Hathaway (triggers cascade)...")
            await click_at(target_x, target_y)
            await box1.click()
            await page.wait_for_timeout(2500)  # Pause to appreciate cascade

            # Click again to retick
            print("👉 Reticking Anne Hathaway...")
            await click_at(target_x, target_y)
            await box1.click()
            await page.wait_for_timeout(1500)

        # Scroll down slightly inside chat
        for scroll_step in range(15, 26):
            await page.evaluate(
                f"document.getElementById('chat').scrollTop = {scroll_step * 35}"
            )
            await page.wait_for_timeout(40)

        await page.wait_for_timeout(1000)

        # Re-query iframe bounding box after scroll
        iframe_box = await (await page.query_selector("#card-iframe")).bounding_box()
        assert iframe_box is not None

        # Locate Apply button
        apply_btn = iframe_element.get_by_role("button", name="Apply")
        apply_box = await apply_btn.bounding_box()

        if apply_box:
            apply_x = iframe_box["x"] + apply_box["x"] + apply_box["width"] / 2
            apply_y = iframe_box["y"] + apply_box["y"] + apply_box["height"] / 2

            print("👉 Moving cursor to Apply button...")
            await smooth_move(apply_x, apply_y, 40)
            await page.wait_for_timeout(800)

            print("👉 Clicking Apply...")
            await click_at(apply_x, apply_y)
            await apply_btn.click()
            await page.wait_for_timeout(1000)

        # Show confirm tool call and final message in chat
        await page.evaluate(
            "document.getElementById('tool-confirm').style.display = 'flex';"
            "document.getElementById('final-message').style.display = 'block';"
        )

        # Scroll to bottom
        for scroll_step in range(26, 40):
            await page.evaluate(
                f"document.getElementById('chat').scrollTop = {scroll_step * 35}"
            )
            await page.wait_for_timeout(40)

        await smooth_move(640, 600, 30)
        await page.wait_for_timeout(3500)

        print("🎬 Finishing recording...")
        await context.close()
        await browser.close()

    # Find recorded webm
    recorded_webms = list(record_dir.glob("*.webm"))
    if not recorded_webms:
        print("❌ No video file recorded.")
        return

    raw_webm = recorded_webms[0]
    out_mp4 = assets_dir / "demo.mp4"
    out_webm = assets_dir / "demo.webm"

    print("🎞️ Encoding video to MP4 and WebM via ffmpeg...")
    # Convert to MP4 (H.264, yuv420p for max compatibility)
    cmd_mp4 = [
        "ffmpeg",
        "-y",
        "-i",
        str(raw_webm),
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-crf",
        "22",
        "-preset",
        "fast",
        "-movflags",
        "+faststart",
        str(out_mp4),
    ]
    subprocess.run(cmd_mp4, check=True)

    # Convert to optimized WebM
    cmd_webm = [
        "ffmpeg",
        "-y",
        "-i",
        str(raw_webm),
        "-c:v",
        "libvpx-vp9",
        "-crf",
        "30",
        "-b:v",
        "0",
        str(out_webm),
    ]
    subprocess.run(cmd_webm, check=True)

    # Clean up raw video dir & stage html
    shutil.rmtree(record_dir)
    stage_path.unlink(missing_ok=True)

    print(
        f"✅ Video created: {out_mp4} ({out_mp4.stat().st_size // 1024} KB), "
        f"{out_webm} ({out_webm.stat().st_size // 1024} KB)"
    )


if __name__ == "__main__":
    asyncio.run(record_screencast())
