"""Inspect GeeTest puzzle DOM state after each drag attempt.
Uses the solver's solve pipeline then inspects result elements."""
from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from src.services.geetest import GeetestSolver
from src.core.config import load_config

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
log = logging.getLogger("inspect_state")

async def main():
    cfg = load_config()
    solver = GeetestSolver(cfg)
    await solver.start()

    try:
        browser = solver._browser
        ctx = await browser.new_context(viewport={"width": 1920, "height": 1080})
        page = await ctx.new_page()

        log.info("Navigating to 2captcha GeeTest demo...")
        await page.goto("https://2captcha.com/demo/geetest", wait_until="domcontentloaded", timeout=30000)
        await asyncio.sleep(3)

        log.info("Triggering GeeTest...")
        await solver._trigger_geetest(page)
        await asyncio.sleep(5)

        # Find the slide frame
        slide_frame = None
        for frame in page.frames:
            try:
                el = await frame.wait_for_selector(".geetest_slider_button", timeout=2000)
                if el:
                    slide_frame = frame
                    log.info("Slide frame found: %s", frame.url[:80])
                    break
            except Exception:
                continue

        if not slide_frame:
            log.error("No slide frame! Inspecting page state:")
            # Check what elements exist
            page_state = await page.evaluate("""() => {
                const frames = document.querySelectorAll('iframe');
                return {
                    frame_count: frames.length,
                    frame_srcs: Array.from(frames).map(f => f.src.slice(0, 100)),
                    page_classes: Array.from(document.querySelectorAll('[class*=\"geetest\"]'))
                        .map(e => e.className).slice(0, 5),
                };
            }""")
            log.info("Page state: %s", page_state)
            return

        # Inspect DOM before any interaction
        dom_before = await slide_frame.evaluate("""() => {
            const state = {};
            // Full element inventory
            const all = document.querySelectorAll('*');
            const classes = new Set();
            all.forEach(el => {
                if (el.className && typeof el.className === 'string')
                    el.className.split(/\\s+/).forEach(c => {
                        if (c.includes('geetest') || c.includes('gt_'))
                            classes.add(c);
                    });
            });
            state.geetest_classes = Array.from(classes);
            
            // Result element state
            const r = document.querySelector('.geetest_result');
            state.result_exists = !!r;
            state.result_cls = r?.className || null;
            state.result_style = r?.style?.display || null;
            state.result_text = r?.textContent?.slice(0, 100) || null;
            
            // Wait state
            const w = document.querySelector('.geetest_wait');
            state.wait_cls = w?.className || null;
            
            // Canvas state
            const c = document.querySelector('canvas');
            state.canvas_exists = !!c;
            state.canvas_dims = c ? `${c.width}x${c.height}` : null;
            
            // Slider track
            const s = document.querySelector('.geetest_slider');
            state.slider_exists = !!s;
            
            return state;
        }""")
        
        log.info("DOM STATE (before drag):")
        for k, v in dom_before.items():
            log.info("  %s: %s", k, v)

        # Now use solver detection pipeline
        shot = await solver._capture_slide_puzzle(page, slide_frame)
        if shot:
            gap = await solver._detect_gap_pixel(shot)
            log.info("Pixel gap detection: col=%s, size=%dKB", gap, len(shot)//1024)
            if gap:
                handle_offset = 27
                drag = gap - handle_offset
                log.info("DRAG: gap=%d, handle_offset=%d, drag=%d", gap, handle_offset, drag)
                
                # Drag
                await solver._drag_slider(page, slide_frame, drag)
                await asyncio.sleep(3)
                
                # Inspect result after drag
                dom_after = await slide_frame.evaluate("""() => {
                    const state = {};
                    const r = document.querySelector('.geetest_result');
                    state.result_cls = r?.className || null;
                    state.result_display = r?.style?.display || null;
                    state.result_html = r?.outerHTML?.slice(0, 500) || null;
                    
                    // Check all nearby elements
                    state.geetest_els = Array.from(
                        document.querySelectorAll('[class*=\"geetest\"]')
                    ).slice(0, 10).map(e => ({
                        cls: e.className.slice(0, 80),
                        display: e.style?.display || null,
                        text: (e.textContent || '').slice(0, 80),
                    }));
                    
                    // Check for success/fail indicator
                    state.success_cls = document.querySelector('.geetest_success')?.className || null;
                    state.error_cls = document.querySelector('.geetest_error')?.className || null;
                    
                    return state;
                }""")
                
                log.info("AFTER DRAG:")
                for k, v in dom_after.items():
                    if v:
                        log.info("  %s: %s", k, str(v)[:300])

    finally:
        await solver.stop()

if __name__ == "__main__":
    asyncio.run(main())
