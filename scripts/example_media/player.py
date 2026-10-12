"""Plays a scene's steps in Playwright pages, recorded or not (#213).

The generator calls :func:`perform` with a pace a person can follow; a smoke check
(the demo site, #214) can call it with ``paced=False`` and the same steps.
"""

from __future__ import annotations

import json
import typing as t

from playwright.sync_api import Page, expect

from .steps import Scene, Step

#: How long anything a step waits for may take, in milliseconds. The E2E harness's budget.
TIMEOUT_MS = 15_000

#: The pace of a person, in milliseconds: between keys, before a click lands, after a step.
KEY_DELAY = 70
AIM = 220
SETTLE = 450

#: A visible pointer that follows the mouse and rings on a press. Headless Chromium draws
#: no cursor, and a clip of buttons changing with nothing pressing them reads as a slideshow.
POINTER_SCRIPT = """
(() => {
  const put = () => {
    if (!document.body || document.getElementById("__media_pointer")) return;
    const p = document.createElement("div");
    p.id = "__media_pointer";
    p.style.cssText = "position:fixed;left:-40px;top:-40px;width:18px;height:18px;margin:-9px 0 0 -9px;" +
      "border-radius:50%;background:rgba(17,24,39,.35);border:2px solid rgba(255,255,255,.9);" +
      "box-shadow:0 1px 4px rgba(0,0,0,.35);z-index:2147483647;pointer-events:none;" +
      "transition:transform .12s ease-out";
    document.body.appendChild(p);
  };
  const at = (e) => {
    put();
    const p = document.getElementById("__media_pointer");
    if (p) { p.style.left = e.clientX + "px"; p.style.top = e.clientY + "px"; }
  };
  const ring = (scale) => () => {
    const p = document.getElementById("__media_pointer");
    if (p) p.style.transform = `scale(${scale})`;
  };
  window.addEventListener("mousemove", at, true);
  window.addEventListener("mousedown", ring(0.6), true);
  window.addEventListener("mouseup", ring(1), true);
  new MutationObserver(put).observe(document, { childList: true, subtree: true });
})();
"""

#: The name of a window's user in its corner, for a scene with two windows side by side.
LABEL_SCRIPT = """
(([label, color]) => {
  const put = () => {
    if (!document.body || document.getElementById("__media_label")) return;
    const el = document.createElement("div");
    el.id = "__media_label";
    el.textContent = label;
    el.style.cssText = "position:fixed;left:50%;bottom:10px;transform:translateX(-50%);z-index:2147483646;" +
      "padding:3px 12px;border-radius:999px;" +
      "font:600 13px/1.5 -apple-system,'Apple SD Gothic Neo',system-ui,sans-serif;" +
      "color:#fff;background:" + color + ";box-shadow:0 1px 4px rgba(0,0,0,.3);pointer-events:none;opacity:.92";
    document.body.appendChild(el);
  };
  new MutationObserver(put).observe(document, { childList: true, subtree: true });
  document.addEventListener("DOMContentLoaded", put);
})(__ARGS__);
"""


def label_script(text: str, color: str) -> str:
    return LABEL_SCRIPT.replace("__ARGS__", json.dumps([text, color]))


def perform(
    pages: t.Mapping[str, Page],
    scene: Scene,
    step: Step,
    base_url: str,
    values: t.Mapping[str, str],
    paced: bool = True,
) -> None:
    """Do one step in the page of the actor it names."""
    page = pages[scene.actor(step.who).name]
    target = step.target.format_map(values) if step.target else ""
    text = step.text.format_map(values) if step.text else ""
    action = step.action
    if action == "visit":
        page.goto(base_url + target)
    elif action == "live":
        page.wait_for_selector(target, timeout=TIMEOUT_MS)
    elif action == "pause":
        if paced:
            page.wait_for_timeout(step.ms)
        return
    elif action == "see":
        locator = page.locator(target).first
        expect(locator).to_be_visible(timeout=TIMEOUT_MS)
        if text:
            expect(locator).to_contain_text(text, timeout=TIMEOUT_MS)
    elif action == "scroll":
        pixels = int(text)
        if target == "window":
            page.mouse.wheel(0, pixels) if paced else page.evaluate(f"window.scrollBy(0, {pixels})")
        else:
            locator = page.locator(target).first
            if paced:
                _aim(page, locator)
                for _ in range(max(1, abs(pixels) // 120)):
                    page.mouse.wheel(0, 120 if pixels > 0 else -120)
                    page.wait_for_timeout(60)
            else:
                locator.evaluate(f"(el) => el.scrollBy(0, {pixels})")
    else:
        locator = page.locator(target).first
        expect(locator).to_be_visible(timeout=TIMEOUT_MS)
        if paced:
            _aim(page, locator)
        if action == "click":
            locator.click()
        elif action == "hover":
            locator.hover()
        elif action == "type":
            locator.click()
            locator.fill("")
            locator.press_sequentially(text, delay=KEY_DELAY if paced else 0)
        elif action == "press":
            locator.press(text)
        elif action == "select":
            locator.select_option(text)
    if paced:
        page.wait_for_timeout(SETTLE)


def _aim(page: Page, locator: t.Any) -> None:
    """Move the pointer onto the element the way a hand would, so the clip shows where it goes."""
    locator.scroll_into_view_if_needed()
    box = locator.bounding_box()
    if box:
        page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2, steps=14)
        page.wait_for_timeout(AIM)
