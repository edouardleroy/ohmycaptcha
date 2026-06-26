"""Debug GeeTest result: check what happens after drag."""
from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.services.geetest import GeetestSolver
from src.core.config import load_config

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("geetest_debug")

GEETEST_DEMO = "https://2captcha.com/demo/geetest"


async def main():
    cfg = load_config()
    solver = GeetestSolver(cfg)
    await solver.start()
    browser = solver._browser

    ctx = await browser.new_context(viewport={"width": 1920, "height": 1080})
    page = await ctx.new_page()
    ss = Path(__file__).parent.parent / "test_screenshots"
    ss.mkdir(exist_ok=True)

    try:
        await page.goto(GEETEST_DEMO, wait_until="domcontentloaded", timeout=60000)
        await asyncio.sleep(2)

        # Trigger GeeTest
        await solver._trigger_geetest(page)
        await asyncio.sleep(3)

        # Find slide
        for f in page.frames:
            try:
                el = f.locator(".geetest_slider, .geetest_window").first
                if await el.is_visible(timeout=2000):
                    log.info("Slide found in frame: %s", f.url[:80] if f != page else "main")
                    break
            except Exception:
                pass

        # Capture + detect
        shot = await solver._capture_slide_puzzle(page, None)
        px = await solver._detect_gap_pixel(shot)

        # Adjust
        adjusted = px or 0
        win = page.locator(".geetest_window").first
        handle = page.locator(".geetest_slider_button").first
        if await win.is_visible(timeout=2000) and await handle.is_visible(timeout=2000):
            wb = await win.bounding_box()
            hb = await handle.bounding_box()
            if wb and hb:
                ho = hb["x"] + hb["width"] / 2 - wb["x"]
                adjusted = max(0, min(300, (px or 0) - int(ho)))
                log.info("Gap=%d, handle_offset=%.0f, drag=%d", px or 0, ho, adjusted)

        # Drag at the adjusted offset
        log.info("Dragging %d px...", adjusted)
        await solver._drag_slider(page, None, adjusted)
        await asyncio.sleep(3)

        # Screenshot after drag
        after = await page.screenshot(full_page=False)
        (ss / "geetest_after_drag.png").write_bytes(after)
        log.info("Screenshot saved")

        # Evaluate page for GeeTest elements/state
        geetest_info = await page.evaluate("""() => {
            const info = {};
            // Check all elements with geetest class
            const els = document.querySelectorAll('[class*=\"geetest\"]');
            info.geetest_elements_count = els.length;
            info.geetest_classes = [...new Set([...els].map(e => e.className))].slice(0, 20);

            // Check hidden inputs
            for (const name of ['geetest_challenge', 'geetest_validate', 'geetest_seccode']) {
                const el = document.querySelector(`[name=\"${name}\"]`);
                info[name] = el ? el.value || '(empty)' : '(not found)';
            }

            // Check captchaObj
            if (window.captchaObj) {
                info.captchaObj_type = typeof window.captchaObj;
                if (typeof window.captchaObj.getValidate === 'function') {
                    info.validate_result = JSON.stringify(window.captchaObj.getValidate());
                }
            } else {
                info.captchaObj = 'not found';
            }

            // Visible result text
            const resultEl = document.querySelector('.geetest_result, .geetest_success, .geetest_fail, .geetest_msg');
            if (resultEl) {
                info.result_text = resultEl.textContent?.trim() || '(empty)';
                info.result_class = resultEl.className;
                info.result_display = getComputedStyle(resultEl).display;
                info.result_visible = resultEl.offsetHeight > 0;
            }

            // Check for any iframe with geetest
            info.iframes = [...document.querySelectorAll('iframe')].map(f => f.src).filter(s => s.includes('geetest') || s.includes('gt4'));

            // Check page for 2captcha demo state
            const demoResult = document.querySelector('.demo-result, #result, .captcha-result, [data-result]');
            if (demoResult) {
                info.demo_result = demoResult.textContent?.trim() || '(empty)';
            }

            return info;
        }""")

        print("\n=== Page state after drag ===")
        for k, v in geetest_info.items():
            print(f"  {k}: {v}")

    finally:
        await ctx.close()
        await solver.stop()


if __name__ == "__main__":
    asyncio.run(main())
