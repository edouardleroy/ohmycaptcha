"""Full GeeTest solve: visit demo, trigger, pixel-detect, drag, extract tokens."""
from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.services.geetest import GeetestSolver
from src.core.config import load_config

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("geetest_full_test")

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
        await page.goto(GEETEST_DEMO, wait_until="domcontentloaded", timeout=60000)
        await asyncio.sleep(1)

        # ── Trigger GeeTest ──
        log.info("Triggering GeeTest...")
        await solver._trigger_geetest(page)
        await asyncio.sleep(3)

        # ── Wait for slide puzzle ──
        log.info("Waiting for slide puzzle...")
        slide_elem = None
        challenge_frame = None
        for frame in page.frames:
            try:
                el = frame.locator(".geetest_slider, .geetest_window, canvas.geetest_canvas").first
                if await el.is_visible(timeout=2000):
                    slide_elem = el
                    challenge_frame = frame if frame != page else None
                    log.info("Found slide in frame: %s", frame.url[:80] if frame != page else "main")
                    break
            except Exception:
                continue

        if slide_elem is None:
            try:
                await page.wait_for_selector(
                    ".geetest_slider, .geetest_window, .geetest_canvas_area, canvas",
                    timeout=10000,
                )
                slide_elem = page.locator(".geetest_window, .geetest_canvas_area, canvas").first
            except Exception:
                raise RuntimeError("Slide puzzle did not appear")

        # ── Capturar + pixel detect ──
        shot = await solver._capture_slide_puzzle(page, challenge_frame)
        if not shot:
            raise RuntimeError("Failed to capture screenshot")

        pixel_offset = await solver._detect_gap_pixel(shot)
        if pixel_offset is not None:
            drag_pixels = pixel_offset
            log.info("Pixel analysis: gap at column %d", drag_pixels)
        else:
            drag_pixels = await solver._compute_slide_offset(shot)
            log.info("Vision model fallback: offset %d", drag_pixels)

        # ── Ajustar handle offset ──
        adjusted_offset = drag_pixels
        try:
            win = page.locator(".geetest_window").first
            handle = page.locator(".geetest_slider_button").first
            if await win.is_visible(timeout=1000) and await handle.is_visible(timeout=1000):
                win_box = await win.bounding_box()
                handle_box = await handle.bounding_box()
                if win_box and handle_box and win_box["width"] > 50:
                    handle_center = handle_box["x"] + handle_box["width"] / 2
                    handle_offset = handle_center - win_box["x"]
                    adjusted_offset = max(0, min(300, drag_pixels - int(handle_offset)))
                    log.info("Adjusted: gap=%d, handle=%.0f, drag=%d",
                             drag_pixels, handle_offset, adjusted_offset)
        except Exception:
            adjusted_offset = drag_pixels

        log.info("Final drag: %d px", adjusted_offset)

        # ── Drag slider with nudge loop ──
        nudge_offsets = [0, 1, -1, 3, -3, 5, -5, 10, -10]
        solved = False
        tokens = {}

        for nudge in nudge_offsets:
            offset = max(0, min(300, adjusted_offset + nudge))
            log.info("Dragging %d px (nudge=%+d)", offset, nudge)

            await solver._drag_slider(page, challenge_frame, offset)

            # Check result
            try:
                result_el = page.locator(".geetest_result, .geetest_success, .geetest_fail").first
                if await result_el.is_visible(timeout=2000):
                    log.info("Result element visible after nudge=%d", nudge)
            except Exception:
                pass

            tokens = await solver._extract_tokens(page)
            if tokens.get("validate"):
                log.info("✅ SOLVED! nudge=%d, validate=%s", nudge, tokens["validate"][:20])
                solved = True
                break

            log.debug("Nudge %d: not solved yet", nudge)

        if solved:
            print(f"\n🎉 GeeTest RESUELTO!")
            print(f"  Validate: {tokens['validate']}")
            print(f"  Seccode:  {tokens.get('seccode', 'N/A')}")
            print(f"  Challenge: {tokens.get('challenge', 'N/A')[:30]}...")
        else:
            print(f"\n❌ No se pudo resolver después de {len(nudge_offsets)} intentos")
            print(f"  Últimos tokens: {tokens}")

        # Save final screenshot
        final_shot = await page.screenshot(full_page=False)
        result_path = screenshots_dir / "geetest_result.png"
        result_path.write_bytes(final_shot)
        print(f"  Screenshot final: {result_path}")

    finally:
        await context.close()
        await solver.stop()


if __name__ == "__main__":
    asyncio.run(main())
