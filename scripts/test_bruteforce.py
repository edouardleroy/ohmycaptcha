"""Brute-force test: try multiple drag offsets to find exact position.
This determines if the 2captcha demo actually validates position
or just always returns fail."""
from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from src.services.geetest import GeetestSolver
from src.core.config import load_config

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
log = logging.getLogger("bruteforce")

async def main():
    cfg = load_config()
    solver = GeetestSolver(cfg)
    await solver.start()

    try:
        browser = solver._browser
        ctx = await browser.new_context(viewport={"width": 1920, "height": 1080})
        page = await ctx.new_page()

        log.info("Navigating to 2captcha demo...")
        await page.goto("https://2captcha.com/demo/geetest", wait_until="domcontentloaded", timeout=30000)
        await asyncio.sleep(3)

        log.info("Triggering GeeTest...")
        try:
            checkbox_frame = page.frame_locator("#geetest-checkbox")
            checkbox = checkbox_frame.locator(".geetest-checkbox")
            await checkbox.wait_for(timeout=8000)
            await checkbox.click()
        except Exception:
            log.info("Checkbox iframe not found, trying in-page trigger")
            for sel in [".geetest_trigger", ".gt_ajax", "[class*=geetest] a", "button:has-text('Verify')"]:
                try:
                    btn = page.locator(sel).first
                    await btn.wait_for(timeout=3000)
                    await btn.click()
                    log.info("Clicked trigger: %s", sel)
                    break
                except Exception:
                    continue

        await asyncio.sleep(5)

        # Find slide frame
        slide_frame = None
        for frame in page.frames:
            try:
                el = await frame.wait_for_selector(".geetest_slider_button, .gt_slider_knob", timeout=2000)
                if el:
                    slide_frame = frame
                    log.info("Found slide in frame: %s", frame.url[:80])
                    break
            except Exception:
                continue

        if not slide_frame:
            log.error("No slide frame found")
            return

        await asyncio.sleep(1)

        # Get handle position
        slider_selectors = [".geetest_slider_button", ".gt_slider_knob"]
        handle = None
        for sel in slider_selectors:
            try:
                handle = slide_frame.locator(sel).first
                await handle.wait_for(timeout=3000)
                break
            except Exception:
                continue

        if not handle:
            log.error("No handle found")
            return

        box = await handle.bounding_box()
        start_x = box["x"] + box["width"] / 2
        start_y = box["y"] + box["height"] / 2
        log.info("Handle at: (%.0f, %.0f)", start_x, start_y)

        # Get window position for pixel analysis
        win = slide_frame.locator(".geetest_window").first
        win_box = await win.bounding_box()
        handle_offset = start_x - win_box["x"]
        log.info("Handle offset from window: %.0f", handle_offset)

        # Try a range of offsets (brute force: 60-160px, step 5)
        results = []
        for offset in range(60, 165, 5):
            drag = offset - handle_offset
            if drag <= 0:
                continue

            log.info("--- Trying offset %d (drag=%d) ---", offset, drag)

            # Reset: reload and trigger again
            await page.goto("https://2captcha.com/demo/geetest", wait_until="domcontentloaded", timeout=20000)
            await asyncio.sleep(3)
            
            # Re-trigger
            try:
                checkbox_frame = page.frame_locator("#geetest-checkbox")
                checkbox = checkbox_frame.locator(".geetest-checkbox")
                await checkbox.wait_for(timeout=5000)
                await checkbox.click()
            except Exception:
                for sel in [".geetest_trigger", ".gt_ajax", "[class*=geetest] a", "button:has-text('Verify')"]:
                    try:
                        btn = page.locator(sel).first
                        await btn.wait_for(timeout=3000)
                        await btn.click()
                        break
                    except Exception:
                        continue

            await asyncio.sleep(5)

            # Re-find slide frame
            slide_frame2 = None
            for frame in page.frames:
                try:
                    el = await frame.wait_for_selector(".geetest_slider_button, .gt_slider_knob", timeout=2000)
                    if el:
                        slide_frame2 = frame
                        break
                except Exception:
                    continue
            if not slide_frame2:
                log.warning("No slide frame after reload")
                continue

            # Find handle again
            handle2 = slide_frame2.locator(".geetest_slider_button").first
            try:
                await handle2.wait_for(timeout=3000)
            except Exception:
                continue

            box2 = await handle2.bounding_box()
            sx = box2["x"] + box2["width"] / 2
            sy = box2["y"] + box2["height"] / 2
            ex = sx + drag

            # Simple drag
            await page.mouse.move(sx, sy)
            await asyncio.sleep(0.1)
            await page.mouse.down()
            await asyncio.sleep(0.05)

            steps = 20
            for i in range(steps):
                t = (i + 1) / steps
                cx = sx + drag * t
                await page.mouse.move(cx, sy)
                await asyncio.sleep(0.02)

            await asyncio.sleep(0.1)
            await page.mouse.up()
            await asyncio.sleep(3)

            # Check result
            result_el = slide_frame2.locator(".geetest_result").first
            try:
                result_class = await result_el.get_attribute("class")
            except Exception:
                result_class = "not_found"

            # Also check HTML content
            try:
                inner_html = await result_el.inner_html()
            except Exception:
                inner_html = ""

            is_success = result_class and "success" in result_class
            is_fail = result_class and "fail" in result_class

            status = "success" if is_success else ("fail" if is_fail else f"unknown({result_class})")
            results.append((offset, drag, status, inner_html[:100]))
            log.info("  Offset %d (drag=%d): %s", offset, drag, status)

            if is_success:
                log.info("*** FOUND SUCCESS OFFSET: %d (drag=%d)! ***", offset, drag)
                break

        # Summary
        print("\n" + "=" * 60)
        print("BRUTE FORCE RESULTS")
        print("=" * 60)
        successes = [r for r in results if r[2] == "success"]
        failures = [r for r in results if r[2] == "fail"]

        if successes:
            print(f"\n✅ SUCCESSES: {len(successes)}")
            for r in successes:
                print(f"   offset={r[0]}px, drag={r[1]}px")
        else:
            print(f"\n❌ No successes found ({len(results)} attempts)")
            print(f"   Failures: {len(failures)}")
            print(f"   Each showed .geetest_fail or no result")
            print("\n→ This confirms the 2captcha demo does NOT validate position.")
            print("  It always returns 'fail' regardless of drag position.")
            print("  OR the drag is being detected as bot by GeeTest server-side.")

        for r in results[:3]:
            print(f"  offset={r[0]:3d}px drag={r[1]:3d}px → {r[2]}: {r[3][:60]}")

    finally:
        await solver.stop()

if __name__ == "__main__":
    asyncio.run(main())
