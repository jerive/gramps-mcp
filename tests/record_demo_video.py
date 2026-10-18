# ruff: noqa: E501
"""Automated screencast recorder for gramps-mcp Elicitation / Prefab UI flow.

Progressive step-by-step demonstration:
1. Slower, readable typewriter prompt in chat input with natural pauses.
2. User sends prompt.
3. Copilot "Thinking..." loader appears.
4. Multiple discovery tool calls (get_search across people, spouses, places) appear sequentially.
5. Assistant drafts atomic plan.
6. apply_changes is called, revealing the tree-structured Prefab interactive card.
7. Simulated mouse interaction: unticking Anne Hathaway (showing indented dependency cascade),
   re-ticking, and clicking [Apply].
8. confirm_changes executes, followed by final success response.

Encodes to docs/assets/demo.mp4 and docs/assets/demo.webm.
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
  <style>
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      background-color: #1e1e1e;
      color: #cccccc;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
      overflow: hidden;
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
    .titlebar-text { flex: 1; text-align: center; color: #cccccc; font-weight: 500; font-size: 12px; }

    /* Main body */
    .app-body {
      display: flex;
      flex: 1;
      overflow: hidden;
      position: relative;
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

    /* Chat layout */
    .chat-layout {
      flex: 1;
      display: flex;
      flex-direction: column;
      background: #1e1e1e;
      overflow: hidden;
      position: relative;
    }

    /* Chat feed */
    .chat-feed {
      flex: 1;
      overflow-y: auto;
      padding: 24px 32px;
      display: flex;
      flex-direction: column;
      gap: 16px;
      max-width: 880px;
      margin: 0 auto;
      width: 100%;
    }

    /* Message bubbles */
    .msg-user {
      align-self: flex-end;
      background: #0e639c;
      color: #ffffff;
      padding: 12px 16px;
      border-radius: 12px 12px 2px 12px;
      max-width: 88%;
      font-size: 13.5px;
      line-height: 1.5;
      box-shadow: 0 2px 8px rgba(0,0,0,0.25);
      animation: fadeIn 0.3s ease;
    }
    .msg-user-header {
      font-size: 11px;
      text-transform: uppercase;
      letter-spacing: 0.5px;
      color: #b3d7ff;
      margin-bottom: 5px;
      font-weight: 600;
    }

    .msg-assistant {
      align-self: flex-start;
      display: flex;
      flex-direction: column;
      gap: 12px;
      width: 100%;
      animation: fadeIn 0.3s ease;
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
      animation: fadeIn 0.35s ease;
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

    /* Thinking spinner */
    .thinking {
      display: flex;
      align-items: center;
      gap: 10px;
      color: #858585;
      font-size: 13px;
      font-style: italic;
      padding: 6px 0;
      animation: fadeIn 0.25s ease;
    }
    .spinner {
      width: 16px;
      height: 16px;
      border: 2px solid #3c3c3c;
      border-top-color: #3794ff;
      border-radius: 50%;
      animation: spin 0.8s linear infinite;
    }
    @keyframes spin { to { transform: rotate(360deg); } }
    @keyframes fadeIn { from { opacity: 0; transform: translateY(6px); } to { opacity: 1; transform: translateY(0); } }

    /* Card Frame */
    .card-wrapper {
      background: #ffffff;
      border-radius: 12px;
      overflow: hidden;
      box-shadow: 0 4px 20px rgba(0,0,0,0.4);
      margin: 8px 0;
      animation: fadeIn 0.4s ease;
    }
    iframe {
      width: 100%;
      height: 980px;
      border: none;
      display: block;
    }

    /* Bottom input bar */
    .chat-input-bar {
      background: #1e1e1e;
      border-top: 1px solid #2d2d2d;
      padding: 14px 24px;
      display: flex;
      align-items: center;
      gap: 12px;
      max-width: 880px;
      width: 100%;
      margin: 0 auto;
      flex-shrink: 0;
    }
    .input-box {
      flex: 1;
      background: #252526;
      border: 1px solid #3c3c3c;
      border-radius: 8px;
      color: #ffffff;
      padding: 10px 14px;
      font-size: 13px;
      min-height: 42px;
      display: flex;
      align-items: center;
      white-space: pre-wrap;
      word-break: break-word;
      line-height: 1.4;
    }
    .cursor-blink {
      display: inline-block;
      width: 2px;
      height: 15px;
      background: #3794ff;
      margin-left: 2px;
      animation: blink 1s infinite;
      vertical-align: middle;
    }
    @keyframes blink { 0%, 50% { opacity: 1; } 51%, 100% { opacity: 0; } }

    .send-btn {
      width: 34px;
      height: 34px;
      border-radius: 6px;
      background: #007acc;
      border: none;
      color: white;
      display: flex;
      align-items: center;
      justify-content: center;
      cursor: pointer;
      font-size: 15px;
      flex-shrink: 0;
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
    <div class="titlebar-text">Visual Studio Code — Copilot Chat (tree: viveret)</div>
  </div>

  <div class="app-body">
    <div class="activity-bar">
      <span>📄</span>
      <span>🔍</span>
      <span>🌿</span>
      <span class="active">💬</span>
      <span>⚙️</span>
    </div>

    <div class="chat-layout">
      <div class="chat-feed" id="chat">
        <!-- Messages will be injected sequentially -->
      </div>

      <div class="chat-input-bar">
        <div class="input-box" id="input-box"><span id="input-text"></span><span class="cursor-blink" id="input-cursor"></span></div>
        <button class="send-btn" id="send-btn">➤</button>
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

    print("🎥 Launching Chrome to record progressive screencast...")
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

        async def smooth_move(target_x, target_y, steps=25):
            cur_pos = await page.evaluate(
                "() => ({x: parseFloat(cursor.style.left) || 640, y: parseFloat(cursor.style.top) || 360})"
            )
            start_x, start_y = cur_pos["x"], cur_pos["y"]
            for i in range(1, steps + 1):
                t = i / steps
                e = 4 * t * t * t if t < 0.5 else 1 - pow(-2 * t + 2, 3) / 2
                x = start_x + (target_x - start_x) * e
                y = start_y + (target_y - start_y) * e
                await page.evaluate(f"moveCursor({x}, {y})")
                await asyncio.sleep(0.02)

        async def click_at(x, y):
            await page.evaluate(f"ripple({x}, {y})")
            await page.wait_for_timeout(200)

        async def scroll_chat_to_bottom():
            await page.evaluate(
                "document.getElementById('chat').scrollTop = document.getElementById('chat').scrollHeight"
            )

        # ---------------------------------------------------------
        # 1. Typewriter prompt in input box (paced for easy reading)
        # ---------------------------------------------------------
        prompt_text = (
            "William Shakespeare was born circa 23 April 1564 (baptized 26 April 1564) "
            "and died on 23 April 1616 in Stratford-upon-Avon; he married Anne Hathaway "
            "in late November 1582 in Warwickshire, having three children born in "
            "Stratford-upon-Avon—Susanna (baptized 26 May 1583) and twins Hamnet and Judith "
            "(baptized 2 February 1585)—while his parents, John Shakespeare and Mary Arden, "
            "married around 1557 in Aston Cantlow. Please update William Shakespeare in the tree accordingly."
        )

        print("⌨️ Typing prompt into chat input (paced for reading)...")
        current_typed = ""
        for word in prompt_text.split(" "):
            current_typed += word + " "
            escaped = current_typed.replace("'", "\\'")
            await page.evaluate(
                f"document.getElementById('input-text').innerText = '{escaped}';"
            )
            # Natural pauses on punctuation, steady reading pace on words
            if any(p in word for p in [";", ".", "—", ","]):
                await asyncio.sleep(0.18)
            else:
                await asyncio.sleep(0.08)

        await page.wait_for_timeout(1000)

        # Click send button
        send_btn_box = await (await page.query_selector("#send-btn")).bounding_box()
        assert send_btn_box is not None
        btn_x = send_btn_box["x"] + send_btn_box["width"] / 2
        btn_y = send_btn_box["y"] + send_btn_box["height"] / 2

        await smooth_move(btn_x, btn_y, 20)
        await click_at(btn_x, btn_y)

        # Append User message to chat
        await page.evaluate(
            f"""
            document.getElementById('input-text').innerText = '';
            const userMsg = document.createElement('div');
            userMsg.className = 'msg-user';
            userMsg.innerHTML = '<div class="msg-user-header">User &bull; Tree viveret</div>' + {repr(prompt_text)};
            document.getElementById('chat').appendChild(userMsg);
        """
        )
        await page.wait_for_timeout(1000)

        # ---------------------------------------------------------
        # 2. Copilot Thinking appears
        # ---------------------------------------------------------
        print("🤔 Copilot starts thinking...")
        await page.evaluate(
            """
            const assistantMsg = document.createElement('div');
            assistantMsg.className = 'msg-assistant';
            assistantMsg.id = 'assistant-container';
            assistantMsg.innerHTML = `
                <div class="assistant-header"><span>✨ GitHub Copilot</span></div>
                <div class="thinking" id="thinking-loader"><div class="spinner"></div><span>Searching genealogy tree...</span></div>
            `;
            document.getElementById('chat').appendChild(assistantMsg);
        """
        )
        await scroll_chat_to_bottom()
        await page.wait_for_timeout(2000)

        # ---------------------------------------------------------
        # 3. Discovery Tool 1: get_search (William Shakespeare)
        # ---------------------------------------------------------
        print("🔍 Tool call 1: get_search (William Shakespeare)...")
        await page.evaluate(
            """
            document.getElementById('thinking-loader').remove();
            const tool1 = document.createElement('div');
            tool1.className = 'tool-box';
            tool1.innerHTML = `
                <div class="tool-header">
                    <span>⚙️ tool call:</span>
                    <span class="tool-pill">get_search</span>
                    <span>{"tree": "viveret", "query": "William Shakespeare"}</span>
                </div>
                <div class="tool-result">
                    ✓ Found 1 person: William Shakespeare (ID: I0001, handle: p_ws) &bull; 1 event: Estimated Death in London, 1615
                </div>
            `;
            document.getElementById('assistant-container').appendChild(tool1);

            const thinking2 = document.createElement('div');
            thinking2.className = 'thinking';
            thinking2.id = 'thinking-loader';
            thinking2.innerHTML = '<div class="spinner"></div><span>Checking spouse and family...</span>';
            document.getElementById('assistant-container').appendChild(thinking2);
        """
        )
        await scroll_chat_to_bottom()
        await page.wait_for_timeout(2200)

        # ---------------------------------------------------------
        # 4. Discovery Tool 2: get_search (Anne Hathaway)
        # ---------------------------------------------------------
        print("🔍 Tool call 2: get_search (Anne Hathaway)...")
        await page.evaluate(
            """
            document.getElementById('thinking-loader').remove();
            const tool2 = document.createElement('div');
            tool2.className = 'tool-box';
            tool2.innerHTML = `
                <div class="tool-header">
                    <span>⚙️ tool call:</span>
                    <span class="tool-pill">get_search</span>
                    <span>{"tree": "viveret", "query": "Anne Hathaway"}</span>
                </div>
                <div class="tool-result">
                    ✓ 0 matches found (Anne Hathaway not yet in tree)
                </div>
            `;
            document.getElementById('assistant-container').appendChild(tool2);

            const thinking3 = document.createElement('div');
            thinking3.className = 'thinking';
            thinking3.id = 'thinking-loader';
            thinking3.innerHTML = '<div class="spinner"></div><span>Checking parents (John Shakespeare, Mary Arden)...</span>';
            document.getElementById('assistant-container').appendChild(thinking3);
        """
        )
        await scroll_chat_to_bottom()
        await page.wait_for_timeout(2200)

        # ---------------------------------------------------------
        # 5. Discovery Tool 3: get_search (John Shakespeare)
        # ---------------------------------------------------------
        print("🔍 Tool call 3: get_search (John Shakespeare)...")
        await page.evaluate(
            """
            document.getElementById('thinking-loader').remove();
            const tool3 = document.createElement('div');
            tool3.className = 'tool-box';
            tool3.innerHTML = `
                <div class="tool-header">
                    <span>⚙️ tool call:</span>
                    <span class="tool-pill">get_search</span>
                    <span>{"tree": "viveret", "query": "John Shakespeare"}</span>
                </div>
                <div class="tool-result">
                    ✓ 0 matches found (Parents not yet in tree)
                </div>
            `;
            document.getElementById('assistant-container').appendChild(tool3);

            const thinking4 = document.createElement('div');
            thinking4.className = 'thinking';
            thinking4.id = 'thinking-loader';
            thinking4.innerHTML = '<div class="spinner"></div><span>Checking places (Stratford-upon-Avon)...</span>';
            document.getElementById('assistant-container').appendChild(thinking4);
        """
        )
        await scroll_chat_to_bottom()
        await page.wait_for_timeout(2000)

        # ---------------------------------------------------------
        # 6. Discovery Tool 4: get_search (Stratford-upon-Avon)
        # ---------------------------------------------------------
        print("🔍 Tool call 4: get_search (Stratford-upon-Avon)...")
        await page.evaluate(
            """
            document.getElementById('thinking-loader').remove();
            const tool4 = document.createElement('div');
            tool4.className = 'tool-box';
            tool4.innerHTML = `
                <div class="tool-header">
                    <span>⚙️ tool call:</span>
                    <span class="tool-pill">get_search</span>
                    <span>{"tree": "viveret", "query": "Stratford-upon-Avon"}</span>
                </div>
                <div class="tool-result">
                    ✓ 0 matches found (Place will be created)
                </div>
            `;
            document.getElementById('assistant-container').appendChild(tool4);

            const thinking5 = document.createElement('div');
            thinking5.className = 'thinking';
            thinking5.id = 'thinking-loader';
            thinking5.innerHTML = '<div class="spinner"></div><span>Planning atomic mutations...</span>';
            document.getElementById('assistant-container').appendChild(thinking5);
        """
        )
        await scroll_chat_to_bottom()
        await page.wait_for_timeout(2200)

        # ---------------------------------------------------------
        # 7. Assistant drafts plan & calls apply_changes
        # ---------------------------------------------------------
        print("📋 Assistant drafts plan...")
        await page.evaluate(
            """
            document.getElementById('thinking-loader').remove();
            const planText = document.createElement('div');
            planText.style.cssText = 'font-size: 13.5px; line-height: 1.5; color: #d4d4d4;';
            planText.innerText = "William Shakespeare is present in the tree with an estimated death in London (1615). I've prepared an atomic transaction with 6 operations structured as a dependency tree: creating Anne Hathaway, adding the marriage, recording his confirmed death in Stratford (1616), updating his links, and removing the obsolete 1615 event.";
            document.getElementById('assistant-container').appendChild(planText);
        """
        )
        await scroll_chat_to_bottom()
        await page.wait_for_timeout(2500)

        print("⚡ Executing tool call: apply_changes...")
        await page.evaluate(
            """
            const toolApply = document.createElement('div');
            toolApply.className = 'tool-box';
            toolApply.id = 'tool-apply-box';
            toolApply.innerHTML = `
                <div class="tool-header">
                    <span>⚙️ tool call:</span>
                    <span class="tool-pill">apply_changes</span>
                    <span>{"tree": "viveret", "operations": [6 operations]}</span>
                </div>
                <div style="color: #4ec9b0; font-size: 12px; margin-top: 4px;">
                    ⚡ Elicitation requested: Interactive review card rendered below (dependency tree)
                </div>
            `;
            document.getElementById('assistant-container').appendChild(toolApply);
        """
        )
        await scroll_chat_to_bottom()
        # Ample pause so user has time to read and understand apply_changes & elicitation
        await page.wait_for_timeout(3800)

        print("🎴 Rendering Prefab review card...")
        await page.evaluate(
            """
            const cardWrapper = document.createElement('div');
            cardWrapper.className = 'card-wrapper';
            cardWrapper.id = 'card-wrapper';
            cardWrapper.innerHTML = '<iframe id="card-iframe" src="shakespeare-card.html"></iframe>';
            document.getElementById('assistant-container').appendChild(cardWrapper);
        """
        )
        await page.wait_for_timeout(1500)

        # Smoothly scroll down to show the Prefab card
        print("📜 Scrolling down to review card...")
        for scroll_step in range(1, 24):
            await page.evaluate(
                f"document.getElementById('chat').scrollTop = {scroll_step * 35}"
            )
            await page.wait_for_timeout(45)

        # Wait for iframe contents to fully render and let viewer take in the card
        iframe_element = page.frame_locator("#card-iframe")
        await page.wait_for_timeout(3500)

        # ---------------------------------------------------------
        # 8. Interactive Card: Checkbox cascade on tree hierarchy
        # ---------------------------------------------------------
        iframe_box = await (await page.query_selector("#card-iframe")).bounding_box()
        assert iframe_box is not None

        boxes = iframe_element.locator('[role="checkbox"]')
        # In the tree outline:
        # box 0 is Person: Anne Hathaway
        # box 1 is indented Family under Anne Hathaway
        # box 2 is indented Event: Marriage under Family
        box_anne = boxes.nth(0)  # Person: Anne Hathaway
        box_coords = await box_anne.bounding_box()

        if box_coords:
            target_x = iframe_box["x"] + box_coords["x"] + box_coords["width"] / 2
            target_y = iframe_box["y"] + box_coords["y"] + box_coords["height"] / 2

            print("👉 Moving cursor to Anne Hathaway checkbox (tree root)...")
            await smooth_move(target_x, target_y, 35)
            await page.wait_for_timeout(1000)

            print("👉 Unticking Anne Hathaway (dependency tree cascade)...")
            await click_at(target_x, target_y)
            await box_anne.click()
            await page.wait_for_timeout(3000)  # Ample pause to show tree cascade

            print("👉 Reticking Anne Hathaway (tree branches restore)...")
            await click_at(target_x, target_y)
            await box_anne.click()
            await page.wait_for_timeout(2000)

        # Scroll down inside chat to see bottom of card and Apply button
        for scroll_step in range(24, 38):
            await page.evaluate(
                f"document.getElementById('chat').scrollTop = {scroll_step * 35}"
            )
            await page.wait_for_timeout(40)

        await page.wait_for_timeout(1200)

        # Re-query iframe bounding box after scroll
        iframe_box = await (await page.query_selector("#card-iframe")).bounding_box()
        assert iframe_box is not None

        # ---------------------------------------------------------
        # 9. Click Apply Button
        # ---------------------------------------------------------
        apply_btn = iframe_element.get_by_role("button", name="Apply")
        apply_box = await apply_btn.bounding_box()

        if apply_box:
            apply_x = iframe_box["x"] + apply_box["x"] + apply_box["width"] / 2
            apply_y = iframe_box["y"] + apply_box["y"] + apply_box["height"] / 2

            print("👉 Moving cursor to Apply button...")
            await smooth_move(apply_x, apply_y, 35)
            await page.wait_for_timeout(1000)

            print("👉 Clicking Apply...")
            await click_at(apply_x, apply_y)
            await apply_btn.click()
            await page.wait_for_timeout(1200)

        # ---------------------------------------------------------
        # 10. Confirmation & Final Success Response
        # ---------------------------------------------------------
        print("✅ Revealing confirm_changes and final success message...")
        await page.evaluate(
            """
            const toolConfirm = document.createElement('div');
            toolConfirm.className = 'tool-box';
            toolConfirm.innerHTML = `
                <div class="tool-header">
                    <span style="color: #4ec9b0;">⚡ app action:</span>
                    <span class="tool-pill">confirm_changes</span>
                    <span>{"tree": "viveret", "token": "auth_token_8a9f...", "selection": {"0": true, "1": true, "2": true, "3": true, "4": true, "5": true}}</span>
                </div>
                <div class="tool-result">
                    ✓ status: "applied", 6 changes committed (transaction: tx_9f14)
                </div>
            `;
            document.getElementById('assistant-container').appendChild(toolConfirm);

            const finalMsg = document.createElement('div');
            finalMsg.style.cssText = 'font-size: 13.5px; line-height: 1.5; color: #d4d4d4; background: #252526; border-left: 3px solid #4ec9b0; padding: 12px 16px; border-radius: 4px; margin-top: 8px; animation: fadeIn 0.4s ease;';
            finalMsg.innerHTML = '✅ <strong>Changes successfully applied!</strong> William Shakespeare\\'s family tree has been updated: Anne Hathaway and their marriage event are recorded, his confirmed death in Stratford-upon-Avon (23 April 1616) is saved, and the outdated 1615 estimated death event has been removed.';
            document.getElementById('assistant-container').appendChild(finalMsg);
        """
        )

        # Scroll to bottom to show final success
        for scroll_step in range(38, 52):
            await page.evaluate(
                f"document.getElementById('chat').scrollTop = {scroll_step * 35}"
            )
            await page.wait_for_timeout(35)

        await smooth_move(640, 620, 25)
        await page.wait_for_timeout(4500)

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
        f"✅ Progressive video created: {out_mp4} ({out_mp4.stat().st_size // 1024} KB), "
        f"{out_webm} ({out_webm.stat().st_size // 1024} KB)"
    )


if __name__ == "__main__":
    asyncio.run(record_screencast())
