"""GeeTest v4 solve on official demo."""
from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.services.geetest import GeetestSolver
from src.core.config import load_config

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("geetest_v4")

GEETEST_V4 = "https://www.geetest.com/en/adaptive-captcha-demo"


async def main():
    cfg = load_config()
    solver = GeetestSolver(cfg)
    await solver.start()
    browser = solver._browser
    assert browser is not None

    ctx = await browser.new_context(viewport={"width": 1920, "height": 1080})
    page = await ctx.new_page()
    ss = Path(__file__).parent.parent / "test_screenshots"
    ss.mkdir(exist_ok=True)

    try:
        await page.goto(GEETEST_V4, wait_until="domcontentloaded", timeout=60000)
        await asyncio.sleep(4)

        # Find the verify button and click it
        log.info("Looking for GeeTest verify button...")
        for sel in [
            ".geetest_btn_click",
            ".geetest_btn",
            "[class*='geetest_btn_click']",
            ".geetest_btn_click_2e70dc1c",
        ]:
            try:
                btn = page.locator(sel).first
                if await btn.is_visible(timeout=2000):
                    log.info("Clicking verify button: %s", sel)
                    await btn.click()
                    await asyncio.sleep(3)
                    break
            except Exception:
                continue

        # After clicking, wait for the slide window
        await asyncio.sleep(3)

        # Check for slide elements or iframes
        page_info = await page.evaluate("""() => {
            const info = {};
            // Check for new geetest elements after click
            const els = document.querySelectorAll('[class*=\"geetest\"]');
            const classes = [...new Set([...els].map(e => e.className))];
            info.geetest_classes = classes.slice(0, 30);

            // Check all iframes (GeeTest v4 slide loads in iframe)
            info.iframes = [...document.querySelectorAll('iframe')].map(f => ({
                src: f.src.slice(0, 150),
                id: f.id,
            }));

            // Hidden inputs
            for (const name of ['geetest_challenge', 'geetest_validate', 'geetest_seccode']) {
                const el = document.querySelector(`[name=\"${name}\"]`);
                info[name] = el ? el.value || '(empty)' : '(not found)';
            }

            return info;
        }""")
        log.info("After click: %s", page_info)

        # Look for slide puzzle in iframes or page
        slide_found = False
        for frame in page.frames:
            try:
                el = frame.locator(".geetest_slider, .geetest_window").first
                if await el.is_visible(timeout=1000):
                    log.info("Slide found in frame: %s", frame.url[:100] if frame != page else "main page")
                    slide_found = True
                    break
            except Exception:
                continue

        if not slide_found:
            log.info("No slide found in any frame, checking page directly")
            try:
                el = page.locator(".geetest_slider, .geetest_window, canvas").first
                if await el.is_visible(timeout=3000):
                    log.info("Slide found on main page!")
                    slide_found = True
            except Exception:
                pass

        if not slide_found:
            log.warning("No slide puzzle appeared after clicking verify")
            # Take screenshot for debugging
            await page.screenshot(full_page=False, path=str(ss / "geetest_v4_noslide.png"))
            return

        # Capture + pixel detect
        shot = await solver._capture_slide_puzzle(page, None)
        if not shot:
            log.error("Failed to capture screenshot")
            return

        px = await solver._detect_gap_pixel(shot)
        if px is not None:
            log.info("Pixel analysis: gap=%d", px)
        else:
            log.info("Pixel analysis failed, using vision model")
            px = await solver._compute_slide_offset(shot)

        # Adjust for handle
        adjusted = px
        try:
            win = page.locator(".geetest_window").first
            handle = page.locator(".geetest_slider_button").first
            if await win.is_visible(timeout=1000) and await handle.is_visible(timeout=1000):
                wb = await win.bounding_box()
                hb = await handle.bounding_box()
                if wb and hb:
                    ho = hb["x"] + hb["width"] / 2 - wb["x"]
                    adjusted = max(0, min(300, (px or 0) - int(ho)))
                    log.info("Adjusted: gap=%d, handle=%.0f, drag=%d", px or 0, ho, adjusted)
        except Exception:
            adjusted = px or 0

        # Drag with nudge loop
        for nudge in [0, 1, -1, 3, -3, 5, -5, 10, -10]:
            offset = max(0, min(300, adjusted + nudge))
            log.info("Drag %d px (nudge=%+d)", offset, nudge)
            await solver._drag_slider(page, None, offset)

            tokens = await solver._extract_tokens(page)
            if tokens.get("validate"):
                log.info("✅ SOLVED! nudge=%d", nudge)
                print(f"\n🎉 GeeTest RESUELTO!")
                print(f"  Validate: {tokens['validate']}")
                print(f"  Seccode:  {tokens.get('seccode', 'N/A')}")
                print(f"  Nudge:    {nudge}")
                return

        log.warning("Not solved after nudge loop")
        await page.screenshot(full_page=False, path=str(ss / "geetest_v4_fail.png"))

    finally:
        await ctx.close()
        await solver.stop()


if __name__ == "__main__":
    asyncio.run(main())
