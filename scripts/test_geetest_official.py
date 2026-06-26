"""Test GeeTest solve on official geetest.com demo (v4)."""
from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.services.geetest import GeetestSolver
from src.core.config import load_config

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("geetest_official")

# Official GeeTest v4 demo page
GEETEST_V4_DEMO = "https://www.geetest.com/en/adaptive-captcha-demo"


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
        log.info("Navigating to %s", GEETEST_V4_DEMO)
        await page.goto(GEETEST_V4_DEMO, wait_until="domcontentloaded", timeout=60000)
        await asyncio.sleep(3)

        # Screenshot initial page
        initial = await page.screenshot(full_page=False)
        (ss / "geetest_v4_initial.png").write_bytes(initial)
        log.info("Page loaded, initial screenshot saved")

        # Look for GeeTest trigger / widget
        page_info = await page.evaluate("""() => {
            const info = {};
            // Check for geetest elements
            const geetestEls = document.querySelectorAll('[class*=\"geetest\"], [class*=\"gt_\"]');
            info.geetest_elements = [...geetestEls].slice(0, 10).map(e => e.className);

            // Check for gt4.js script
            const scripts = [...document.scripts].map(s => s.src || '(inline)');
            info.gt4_scripts = scripts.filter(s => s.includes('gt4') || s.includes('geetest'));

            // Look for GeeTest init data
            const initDiv = document.querySelector('#geetest-container, [class*=\"geetest\"]');
            info.init_div = initDiv ? initDiv.id || initDiv.className : 'not found';

            // Check window for initGeetest
            info.has_initGeetest = typeof window.initGeetest === 'function';

            return info;
        }""")
        log.info("Page info: %s", page_info)

        if page_info.get("has_initGeetest"):
            log.info("initGeetest available - calling it...")
            result = await page.evaluate("""async () => {
                return new Promise((resolve) => {
                    try {
                        window.initGeetest({
                            gt: '019924a82c70bb123aae90d483087f94',
                            challenge: '1234567890abcdef1234567890abcdef',
                            offline: false,
                            new_captcha: true,
                            product: 'bind',
                            width: '100%',
                        }, function(captchaObj) {
                            window.captchaObj = captchaObj;
                            captchaObj.appendTo('#geetest-container');
                            resolve({status: 'initialized', type: typeof captchaObj});
                        });
                    } catch (e) {
                        resolve({status: 'error', error: e.message});
                    }
                });
            }""")
            log.info("initGeetest result: %s", result)
            await asyncio.sleep(2)

        # Try clicking any GeeTest trigger
        triggers = [
            ".geetest_radar_tip",
            ".geetest_btn",
            ".gt_ajax_button",
            "[class*='geetest'] button",
            "#geetest-container",
            "[class*='gt_'] a",
        ]
        for sel in triggers:
            try:
                el = page.locator(sel).first
                if await el.is_visible(timeout=2000):
                    log.info("Clicking trigger: %s", sel)
                    await el.click()
                    await asyncio.sleep(3)
                    break
            except Exception:
                continue

        # Check for iframes
        iframes = await page.evaluate("""() => {
            return [...document.querySelectorAll('iframe')].map(f => ({
                src: f.src.slice(0, 120),
                id: f.id,
                cls: f.className,
            }));
        }""")
        log.info("Iframes: %s", iframes)

        # Wait and screenshot
        await asyncio.sleep(5)
        after = await page.screenshot(full_page=False)
        (ss / "geetest_v4_after.png").write_bytes(after)
        log.info("After screenshot saved")

    finally:
        await ctx.close()
        await solver.stop()


if __name__ == "__main__":
    asyncio.run(main())
