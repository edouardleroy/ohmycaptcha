"""Verify GeeTest puzzle state after each drag attempt.
Inspect the geetest_result element class and HTML content
to understand what the demo actually validates."""
from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from src.services.geetest import GeetestSolver
from src.core.config import load_config

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
log = logging.getLogger("verify_state")

async def main():
    cfg = load_config()
    solver = GeetestSolver(cfg)
    await solver.start()

    try:
        browser = solver._browser
        ctx = await browser.new_context(viewport={"width": 1920, "height": 1080})
        page = await ctx.new_page()

        log.info("Navigating to demo...")
        await page.goto("https://2captcha.com/demo/geetest", wait_until="domcontentloaded", timeout=30000)
        await asyncio.sleep(3)

        log.info("Using solver trigger...")
        await solver._trigger_geetest(page)
        await asyncio.sleep(5)

        # Find slide frame
        slide_frame = None
        for frame in page.frames:
            try:
                el = await frame.wait_for_selector(
                    ".geetest_slider_button, .gt_slider_knob",
                    timeout=3000
                )
                if el:
                    slide_frame = frame
                    log.info("Slide frame: %s", frame.url[:80])
                    break
            except Exception:
                continue

        if not slide_frame:
            log.error("No slide frame")
            return

        # Capture the puzzle before drag (inspect DOM state)
        dom_state_before = await slide_frame.evaluate("""() => {
            const state = {};
            // Get all elements with geetest class
            const els = document.querySelectorAll('[class*=\"geetest\"]');
            state.elements = Array.from(els).map(e => ({
                tag: e.tagName,
                id: e.id,
                cls: e.className,
                text: (e.textContent || '').slice(0, 50)
            }));
            state.result_el = document.querySelector('.geetest_result') ? 
                document.querySelector('.geetest_result').outerHTML.slice(0, 200) : null;
            state.captcha_el = document.querySelector('#geetest_captcha') ?
                document.querySelector('#geetest_captcha').outerHTML.slice(0, 200) : null;
            state.wait_el = document.querySelector('.geetest_wait') ?
                document.querySelector('.geetest_wait').outerHTML.slice(0, 200) : null;
            state.success_el = document.querySelector('.geetest_success') ?
                document.querySelector('.geetest_success').outerHTML.slice(0, 200) : null;
            return state;
        }""")

        log.info("BEFORE DRAG:")
        for k, v in dom_state_before.items():
            if v:
                log.info("  %s: %s", k, str(v)[:200])

        if dom_state_before.get("wait_el"):
            log.info("→ Page is in WAITING state (slide not ready yet)")
            await asyncio.sleep(3)
            # Re-check
            dom_state_before2 = await slide_frame.evaluate("""() => {
                const els = document.querySelectorAll('[class*=\"geetest\"]');
                return Array.from(els).map(e => e.className).filter(c => c);
            }""")
            log.info("After extra wait: %s", dom_state_before2[:5])

        # Use solver's pixel analysis
        shot = await solver._capture_screenshot(page)
        if shot:
            log.info("Screenshot captured: %d bytes", len(shot))
            ps.pxd = await solver._detect_gap_pixel(shot)
            log.info("Pixel gap: %s", ps.pxd)
        else:
            ps.pxd = None

        # Try a drag and inspect the result element
        for offset_delta in [0, 3, -3, 5]:
            if ps.pxd:
                handle_offset = 27
                drag = ps.pxd - handle_offset + offset_delta
                log.info("=== Drag: %dpx (pixel_gap=%d, handle=%d, delta=%+d) ===",
                         drag, ps.pxd, handle_offset, offset_delta)

                # Find handle
                handle = slide_frame.locator(".geetest_slider_button").first
                try:
                    await handle.wait_for(timeout=3000)
                except Exception:
                    log.error("Handle not found, re-looking...")
                    for frame in page.frames:
                        try:
                            el = await frame.wait_for_selector(".geetest_slider_button", timeout=2000)
                            if el:
                                slide_frame = frame
                                handle = frame.locator(".geetest_slider_button").first
                                break
                        except Exception:
                            continue

                await solver._drag_slider(page, slide_frame, drag)
                await asyncio.sleep(3)

                # Inspect result elements after drag
                dom_state_after = await slide_frame.evaluate("""() => {
                    const state = {};
                    state.result_cls = document.querySelector('.geetest_result')?.className;
                    state.result_html = document.querySelector('.geetest_result')?.outerHTML?.slice(0, 300);
                    state.success_el = document.querySelector('.geetest_success')?.outerHTML?.slice(0, 200);
                    state.error_el = document.querySelector('.geetest_error')?.outerHTML?.slice(0, 200);
                    
                    // Also check for any validation message
                    state.all_classes = Array.from(document.querySelectorAll('[class*=\"geetest\"]'))
                        .map(e => e.className).filter(c => c);
                    
                    // Check if there's a canvas with result data
                    state.canvas_data = document.querySelector('canvas')?.toDataURL?.()?.slice(0, 50);
                    
                    // Check for any text nodes
                    state.texts = Array.from(document.querySelectorAll('.geetest_result_info, .geetest_result_content, [class*=geetest_msg]'))
                        .map(e => e.textContent);
                    return state;
                }""")

                log.info("AFTER DRAG (delta=%+d):", offset_delta)
                for k, v in dom_state_after.items():
                    if v:
                        log.info("  %s: %s", k, str(v)[:250])

                if "success" in str(dom_state_after):
                    log.info("*** SUCCESS for delta=%+d ***", offset_delta)

    finally:
        await solver.stop()

ps.pxd = None  # dummy for the loop

if __name__ == "__main__":
    asyncio.run(main())
