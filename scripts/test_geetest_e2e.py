"""End-to-end GeeTest test: navigate, clip to puzzle, pixel-detect."""
from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.services.geetest import GeetestSolver
from src.core.config import load_config
from src.core.config import Config

logging.basicConfig(level=logging.DEBUG, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("geetest_e2e")

GEETEST_DEMO = "https://2captcha.com/demo/geetest"


async def main():
    config_instance = load_config()
    solver = GeetestSolver(config_instance)
    await solver.start()

    browser = solver._browser
    assert browser is not None

    context = await browser.new_context(
        viewport={"width": 1920, "height": 1080},
        locale="en-US",
    )
    page = await context.new_page()
    screenshots_dir = Path(__file__).parent.parent / "test_screenshots"
    screenshots_dir.mkdir(exist_ok=True)

    try:
        log.info("Navigating to %s", GEETEST_DEMO)
        await page.goto(GEETEST_DEMO, wait_until="networkidle", timeout=30000)
        await asyncio.sleep(2)

        # ── Trigger GeeTest ──
        log.info("Triggering GeeTest...")
        await solver._trigger_geetest(page)
        await asyncio.sleep(3)

        # ── Find slide elements ──
        slide_elem = None
        for frame in page.frames:
            try:
                el = frame.locator(".geetest_slider, .geetest_window, canvas.geetest_canvas").first
                if await el.is_visible(timeout=2000):
                    slide_elem = el
                    log.info("Found slide element in frame: %s", frame.url[:80] if frame != page else "main page")
                    break
            except Exception:
                continue

        if slide_elem is None:
            # Check main page
            try:
                await page.wait_for_selector(
                    ".geetest_slider, .geetest_window, .geetest_canvas_area, canvas",
                    timeout=8000
                )
                slide_elem = page.locator(".geetest_window, .geetest_canvas_area, canvas").first
            except Exception:
                log.warning("No slide puzzle found - taking full page shot anyway")

        # ── Take a screenshot, clipped to puzzle area ──
        try:
            win = page.locator(".geetest_window").first
            slider = page.locator(".geetest_slider").first
            win_visible = await win.is_visible(timeout=2000)
            slider_visible = await slider.is_visible(timeout=2000)
        except Exception:
            win_visible = False
            slider_visible = False

        if win_visible and slider_visible:
            win_box = await win.bounding_box()
            slider_box = await slider.bounding_box()
            if win_box and slider_box and win_box["width"] > 50:
                clip = {
                    "x": min(win_box["x"], slider_box["x"]),
                    "y": win_box["y"],
                    "width": max(win_box["width"], slider_box["width"]),
                    "height": slider_box["y"] + slider_box["height"] - win_box["y"],
                }
                shot = await page.screenshot(full_page=False, clip=clip, timeout=5000)
                log.info("Clipped puzzle+slider: %s (%d bytes)", clip, len(shot))
                clip_path = screenshots_dir / "geetest_clipped.png"
                clip_path.write_bytes(shot)
                print(f"Clipped screenshot saved: {clip_path}")
            else:
                shot = await page.screenshot(full_page=False)
        else:
            shot = await page.screenshot(full_page=False)
            full_path = screenshots_dir / "geetest_full.png"
            full_path.write_bytes(shot)
            print(f"Full screenshot saved: {full_path}")

        # ── Run pixel analysis ──
        result = await solver._detect_gap_pixel(shot)
        if result:
            print(f"\n✅ Pixel analysis: GAP DETECTED at column {result}px")
        else:
            print("\n⚠️ Pixel analysis: no gap detected (falling back to vision model)")
            offset = await solver._compute_slide_offset(shot)
            print(f"Vision model: offset={offset}px")

        # ── Parse handle offset ──
        if win_visible and await win.is_visible(timeout=1000):
            handle = page.locator(".geetest_slider_button").first
            if await handle.is_visible(timeout=1000):
                win_box2 = await win.bounding_box()
                handle_box2 = await handle.bounding_box()
                if win_box2 and handle_box2:
                    handle_center = handle_box2["x"] + handle_box2["width"] / 2
                    handle_offset = handle_center - win_box2["x"]
                    adjusted = max(0, result - int(handle_offset)) if result else 0
                    print(f"Window box: x={win_box2['x']:.0f}, w={win_box2['width']:.0f}")
                    print(f"Handle center offset from window left: {handle_offset:.0f}px")
                    if result:
                        print(f"Gap - handle_offset = {result} - {int(handle_offset)} = {adjusted}px")
                        print(f"→ Final drag distance: {adjusted}px")
        else:
            print("Could not measure handle offset (elements not visible)")

        # ── Save debug overlay (visualize detection) ──
        if result:
            import cv2, numpy as np
            nparr = np.frombuffer(shot, np.uint8)
            img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
            if img is not None:
                h, w = img.shape[:2]
                cv2.line(img, (result, 0), (result, h), (0, 0, 255), 3)
                cv2.putText(img, f"Gap: {result}px", (result + 5, 30),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 255), 2)
                debug_path = screenshots_dir / "geetest_debug.png"
                cv2.imwrite(str(debug_path), img)
                print(f"\nDebug overlay saved: {debug_path}")

    finally:
        await context.close()
        await solver.stop()


if __name__ == "__main__":
    asyncio.run(main())
