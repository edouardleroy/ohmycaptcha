"""GeeTest v3/v4 slide puzzle solver using Playwright browser automation.

Supports GeeTestTaskProxyless task type.
Visits the target page, triggers the GeeTest challenge, uses a vision model
to determine the slide offset, drags the slider, and extracts the validate token.

GeeTest background (v3/v4):
  - Widget is initialized via initGeetest() JS API
  - A clickable checkbox/radar-tip triggers the challenge
  - The challenge is a slide puzzle: full background image + sliced puzzle piece
  - The slider button has class 'geetest_slider_button'
  - On success, tokens are stored in hidden inputs:
      geetest_challenge, geetest_validate, geetest_seccode
  - The validate token and seccode are the final result
"""

from __future__ import annotations

import asyncio
import base64
import logging
from typing import Any

from playwright.async_api import Browser
from invisible_playwright.async_api import InvisiblePlaywright

from ..core.config import Config

log = logging.getLogger(__name__)

# JS to extract GeeTest validation tokens from the page
_EXTRACT_GEETEST_TOKEN_JS = """() => {
    const challenge = document.querySelector('[name="geetest_challenge"]');
    const validate  = document.querySelector('[name="geetest_validate"]');
    const seccode   = document.querySelector('[name="geetest_seccode"]');
    const data = {};
    if (challenge && challenge.value) data.challenge = challenge.value;
    if (validate && validate.value)   data.validate  = validate.value;
    if (seccode && seccode.value)     data.seccode   = seccode.value;
    // Try via the captchaObj API if available
    if (window.captchaObj && typeof window.captchaObj.getValidate === 'function') {
        const v = window.captchaObj.getValidate();
        if (v && v.validate) {
            data.challenge = v.challenge || '';
            data.validate  = v.validate  || '';
            data.seccode   = v.seccode   || '';
        }
    }
    return data;
}"""

# JS to trigger GeeTest execute (for v3 invisible/popup mode)
_EXECUTE_GEETEST_JS = """() => {
    if (window.captchaObj && typeof window.captchaObj.verify === 'function') {
        window.captchaObj.verify();
        return true;
    }
    if (window.captchaObj && typeof window.captchaObj.showCaptcha === 'function') {
        window.captchaObj.showCaptcha();
        return true;
    }
    // Click the geetest trigger element as fallback
    const trigger = document.querySelector('.geetest_radar_tip, .geetest-checkbox, [class*="geetest"] a');
    if (trigger) { trigger.click(); return true; }
    return false;
}"""

# Vision model system prompt for slide offset detection
_GEETEST_SLIDE_PROMPT = """You are analyzing a GeeTest slide captcha puzzle. The image shows:

1. A background image with a puzzle-shaped gap (missing piece area)
2. A loose puzzle piece positioned on the slider track (left side)
3. A slider handle at the bottom of the track that can be dragged horizontally

The puzzle piece starts at the LEFT side of the slider track. You must determine
exactly how many pixels to drag the slider so the puzzle piece aligns perfectly
with the gap in the background image.

Return STRICT JSON only. No markdown, no extra text.
{
  "drag_distance_px": <integer 0-300>,
  "reason": "brief explanation of why this offset aligns the piece"
}"""


class GeetestSolver:
    """Solves GeeTest v3/v4 tasks via invisible_playwright (patched Firefox).

    Uses vision model (llava) to determine the slide offset for the
    puzzle-piece captcha challenge.
    """

    def __init__(self, config: Config) -> None:
        self._config = config
        self._ipw: InvisiblePlaywright | None = None
        self._browser: Browser | None = None

    async def start(self) -> None:
        if self._browser is not None:
            return
        self._ipw = InvisiblePlaywright(
            headless=self._config.browser_headless,
            humanize=True,
            profile_dir=None,  # persistent context WIP
        )
        self._browser = await self._ipw.__aenter__()
        log.info("GeetestSolver browser started (invisible_playwright Firefox)")

    async def stop(self) -> None:
        if self._ipw:
            await self._ipw.__aexit__(None, None, None)
            self._ipw = None
            self._browser = None
        log.info("GeetestSolver stopped")

    async def solve(self, params: dict[str, Any]) -> dict[str, Any]:
        website_url = params["websiteURL"]
        website_key = params.get("websiteKey", params.get("gt", ""))
        challenge = params.get("challenge", "")

        last_error: Exception | None = None
        for attempt in range(self._config.captcha_retries):
            try:
                result = await self._solve_once(
                    website_url, website_key, challenge
                )
                return result
            except Exception as exc:
                last_error = exc
                log.warning(
                    "GeeTest attempt %d/%d failed: %s",
                    attempt + 1,
                    self._config.captcha_retries,
                    exc,
                )
                if attempt < self._config.captcha_retries - 1:
                    await asyncio.sleep(2)

        raise RuntimeError(
            f"GeeTest failed after {self._config.captcha_retries} attempts: {last_error}"
        )

    async def _solve_once(
        self, website_url: str, website_key: str, challenge: str
    ) -> dict[str, str]:
        """Single attempt at solving a GeeTest challenge.

        Steps:
          1. Navigate to the target page.
          2. Locate and click the GeeTest trigger (checkbox/radar-tip).
          3. Wait for the slide puzzle iframe to appear.
          4. Switch to the challenge iframe context.
          5. Take a full-page screenshot of the slide puzzle.
          6. Send the screenshot to the vision model to determine slide offset.
          7. Drag the slider to the computed position.
          8. Wait for verification and extract the validate/seccode tokens.
        """
        assert self._browser is not None

        context = await self._browser.new_context(
            viewport={"width": 1920, "height": 1080},
            locale="en-US",
        )
        page = await context.new_page()

        try:
            timeout_ms = self._config.browser_timeout * 1000
            await page.goto(website_url, wait_until="networkidle", timeout=timeout_ms)

            # Human-like mouse movement
            await page.mouse.move(400, 300)
            await asyncio.sleep(1)

            # ── Step 1: Trigger the GeeTest challenge ──
            await self._trigger_geetest(page)

            # ── Step 2: Wait for the slide puzzle to appear on page ──
            log.info("Waiting for GeeTest slide puzzle elements...")
            slide_elem = None
            challenge_frame = None
            
            for frame in page.frames:
                try:
                    el = frame.locator(".geetest_slider, .geetest_window, canvas.geetest_canvas").first
                    if await el.is_visible(timeout=2000):
                        slide_elem = el
                        challenge_frame = frame if frame != page else None
                        log.info("Found slide element in frame: %s", frame.url[:80] if frame != page else "main page")
                        break
                except Exception:
                    continue
            
            if slide_elem is None:
                log.info("No slide element in any frame, checking main page directly")
                try:
                    await page.wait_for_selector(
                        ".geetest_slider, .geetest_window, .geetest_canvas_area, canvas",
                        timeout=10_000
                    )
                    slide_elem = page.locator(".geetest_window, .geetest_canvas_area, canvas").first
                except Exception:
                    raise RuntimeError("GeeTest slide puzzle did not appear on page")

            # ── Step 3: Take screenshot of the slide puzzle ──
            slide_screenshot = await self._capture_slide_puzzle(page, challenge_frame)
            if not slide_screenshot:
                raise RuntimeError("Failed to capture slide puzzle screenshot")

            # ── Step 4: Vision model determines the slide offset ──
            drag_pixels = await self._compute_slide_offset(slide_screenshot)
            log.info("Vision model determined slide offset: %d px", drag_pixels)

            # ── Step 5: Adjust offset from handle position ──
            # The vision model reports offset from the LEFT EDGE of the puzzle window.
            # But the slider handle starts at some position WITHIN that window.
            # We need to subtract the handle's starting offset from the vision result.
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
                        log.info(
                            "Adjusted offset: vision=%d, handle_offset=%.0f, adjusted=%d",
                            drag_pixels, handle_offset, adjusted_offset,
                        )
            except Exception as e:
                log.debug("Offset adjustment failed: %s", str(e)[:60])
                adjusted_offset = drag_pixels

            log.info("Final drag offset: %d px", adjusted_offset)

            # ── Step 6: Drag the slider with nudge loop ──
            # Try the primary offset first, then nudge ±3, ±5, ±8 if needed
            nudge_offsets = [0, 3, -3, 5, -5, 10, -10, 15, -15, 20, -20, 30, -30, 40, -40, 50, -50, -60, 70, -70, 80, -80, 100, -100]
            drag_pixels_base = adjusted_offset
            for nudge in nudge_offsets:
                offset = max(0, min(300, drag_pixels_base + nudge))
                log.info(
                    "Dragging slider %d px (nudge=%+d)",
                    offset,
                    nudge,
                )
                await self._drag_slider(page, challenge_frame, offset)
                await asyncio.sleep(2)
                
                # Check if solved
                result_visible = await page.locator(".geetest_result").first.is_visible(timeout=1000)
                if result_visible:
                    tokens = await self._extract_tokens(page)
                    if tokens.get("validate"):
                        log.info("GeeTest solved with nudge=%d: validate=%s", nudge, tokens["validate"][:20])
                        return tokens
                
                log.debug("Nudge %d: not solved yet", nudge)
            
            raise RuntimeError(f"Failed to solve GeeTest after nudging from base={drag_pixels_base}")

        except Exception as e:
            log.warning(
                "GeeTest solve attempt failed: %s",
                str(e)[:120],
            )
            raise

        finally:
            await context.close()

    async def _trigger_geetest(self, page: Any) -> None:
        """Find and click the GeeTest trigger element.

        Tries multiple selector strategies:
          1. GeeTest checkbox iframe (radar/popup mode)
          2. In-page trigger elements (for bind/product mode)
          3. JavaScript API call as fallback
        """
        # Strategy 1: Look for the GeeTest checkbox iframe and click inside it
        geetest_frame = page.frame_locator(
            'iframe[src*="geetest"], '
            'iframe[id*="geetest"], '
            '[class*="geetest"] iframe'
        ).first

        try:
            # Try clicking the radar checkbox (v3 style)
            checkbox = geetest_frame.locator(
                ".geetest_radar_tip_content, "
                ".geetest-checkbox, "
                "#geetest_radar_tip"
            ).first
            await checkbox.click(timeout=8_000)
            log.info("Clicked GeeTest checkbox via iframe")
            await asyncio.sleep(1.5)
            return
        except Exception:
            log.info("GeeTest checkbox iframe not found, trying in-page trigger")

        # Strategy 2: Look for in-page trigger elements
        try:
            trigger_selector = (
                '.geetest_radar_tip, '
                '.geetest-checkbox, '
                '[class*="geetest-radar"], '
                '[class*="geetest_btn"], '
                '.gt_ajax_button, '
                '.geetest_btn'
            )
            trigger = page.locator(trigger_selector).first
            await trigger.click(timeout=5_000)
            log.info("Clicked GeeTest trigger element in-page")
            await asyncio.sleep(1.5)
            return
        except Exception:
            log.info("No in-page GeeTest trigger found, trying JS API")

        # Strategy 3: Use JS API as fallback
        triggered = await page.evaluate(_EXECUTE_GEETEST_JS)
        if triggered:
            log.info("GeeTest triggered via JS API")
            await asyncio.sleep(2)
        else:
            log.warning("Could not trigger GeeTest — no trigger element or API found")

    async def _wait_for_challenge_iframe(self, page: Any) -> Any | None:
        """Wait for the GeeTest challenge iframe to appear after triggering.

        GeeTest v3 loads the slide puzzle in a separate iframe with
        'geetest' in the src/name. V4 is similar but may use 'gt4'.
        Returns the iframe Frame object or None if not found.
        """
        selectors = [
            'iframe[src*="geetest"][src*="type=slide"]',
            'iframe[src*="geetest"][src*="s=1"]',
            'iframe[src*="gt4"]',
            'iframe[src*="geetest"]',
        ]
        for selector in selectors:
            try:
                frame_locator = page.frame_locator(selector).first
                frame = await frame_locator.owner_frame_locator().first.element_handle(
                    timeout=3_000
                )
                # Get the actual frame object
                for f in page.frames:
                    if "geetest" in f.url:
                        log.info("Found GeeTest challenge iframe: %s", f.url[:80])
                        return f
                # Fallback via element handle
                if frame:
                    for f in page.frames:
                        if "geetest" in f.url:
                            return f
            except Exception:
                continue

        # Last resort: search all frames
        for f in page.frames:
            if "geetest" in f.url:
                log.info("Found GeeTest frame (scan): %s", f.url[:80])
                return f

        log.warning("GeeTest challenge iframe not found")
        return None

    async def _capture_slide_puzzle(
        self, page: Any, challenge_frame: Any | None
    ) -> bytes | None:
        """Take a screenshot of the slide puzzle area.

        Uses full-page screenshot with clipping to the GeeTest challenge area
        (where the .geetest_window typically appears). Falls back to full page.
        """
        log.info("Capturing slide puzzle screenshot...")
        
        # Try to get the combined bounding box of puzzle + slider
        try:
            win = page.locator(".geetest_window").first
            slider = page.locator(".geetest_slider").first
            if await win.is_visible(timeout=2000) and await slider.is_visible(timeout=1000):
                win_box = await win.bounding_box()
                slider_box = await slider.bounding_box()
                if win_box and slider_box and win_box["width"] > 50:
                    # Combine: top=win.y, bottom=slider.y+slider.height, left=min, right=max
                    clip = {
                        "x": min(win_box["x"], slider_box["x"]),
                        "y": win_box["y"],
                        "width": max(win_box["width"], slider_box["width"]),
                        "height": slider_box["y"] + slider_box["height"] - win_box["y"],
                    }
                    shot = await page.screenshot(
                        full_page=False, clip=clip, timeout=5000,
                    )
                    log.info("Combined puzzle+slider clip: %s (%d bytes)", clip, len(shot))
                    return shot
        except Exception as e:
            log.debug("Combined clip failed: %s", str(e)[:60])
        
        # Fallback: full-page screenshot
        shot = await page.screenshot(full_page=False, timeout=10000)
        log.info("Full-page screenshot (%d bytes)", len(shot))
        return shot

    async def _compute_slide_offset(self, screenshot_bytes: bytes) -> int:
        """Use the vision model (llava) to determine the slide offset in pixels.

        Sends the screenshot to the configured multimodal model and parses
        the response to extract drag_distance_px.
        """
        from openai import AsyncOpenAI

        client = AsyncOpenAI(
            base_url=self._config.local_base_url,
            api_key=self._config.local_api_key,
        )

        b64 = base64.b64encode(screenshot_bytes).decode()
        data_url = f"data:image/png;base64,{b64}"

        last_error: Exception | None = None
        for attempt in range(self._config.captcha_retries):
            try:
                response = await client.chat.completions.create(
                    model=self._config.captcha_multimodal_model,
                    temperature=0.05,
                    max_tokens=512,
                    messages=[
                        {"role": "system", "content": _GEETEST_SLIDE_PROMPT},
                        {
                            "role": "user",
                            "content": [
                                {
                                    "type": "image_url",
                                    "image_url": {
                                        "url": data_url,
                                        "detail": "high",
                                    },
                                },
                                {
                                    "type": "text",
                                    "text": (
                                        "Analyze this GeeTest slide puzzle image. "
                                        "Determine the exact horizontal drag distance "
                                        "in pixels needed to align the puzzle piece "
                                        "with the gap."
                                    ),
                                },
                            ],
                        },
                    ],
                )
                raw = response.choices[0].message.content or ""
                offset = self._parse_drag_offset(raw)
                if offset > 0:
                    return offset
                raise ValueError(f"Invalid drag offset from vision model: {offset}")
            except Exception as exc:
                last_error = exc
                log.warning(
                    "Vision model analysis attempt %d/%d failed: %s",
                    attempt + 1,
                    self._config.captcha_retries,
                    exc,
                )

        raise RuntimeError(
            f"Slide offset detection failed after "
            f"{self._config.captcha_retries} attempts: {last_error}"
        )

    @staticmethod
    def _parse_drag_offset(text: str) -> int:
        """Extract drag_distance_px from vision model JSON response."""
        import json
        import re

        # Try to find JSON in code blocks
        match = re.search(
            r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL
        )
        if match:
            try:
                data = json.loads(match.group(1))
                offset = data.get("drag_distance_px", 0)
                return int(offset)
            except (json.JSONDecodeError, ValueError, TypeError):
                pass

        # Try to find any JSON object
        match = re.search(r"\{[^{}]*\}", text)
        if match:
            try:
                data = json.loads(match.group(0))
                offset = data.get("drag_distance_px", 0)
                if offset:
                    return int(offset)
            except (json.JSONDecodeError, ValueError, TypeError):
                pass

        # Try to find a plain integer in the response
        numbers = re.findall(r"\b(\d{2,4})\b", text)
        if numbers:
            return int(numbers[0])

        raise ValueError(f"Could not parse drag offset from: {text[:200]}")

    async def _drag_slider(
        self, page: Any, challenge_frame: Any | None, drag_pixels: int
    ) -> None:
        """Drag the GeeTest slider button by the computed pixel offset.

        Works in the challenge iframe if available, otherwise falls back
        to the main page. Finds the slider handle element and performs
        a human-like drag gesture.
        """
        target = challenge_frame if challenge_frame else page

        # Locate the slider button
        slider_selectors = [
            ".geetest_slider_button",
            ".gt_slider_knob",
            '[class*="geetest_slider"] button',
            ".slider-button",
        ]

        slider_handle = None
        for selector in slider_selectors:
            try:
                slider_handle = target.locator(selector).first
                await slider_handle.wait_for(timeout=3_000)
                break
            except Exception:
                continue

        if slider_handle is None:
            raise RuntimeError("Could not find GeeTest slider button")

        # Get the slider dimensions and starting position
        box = await slider_handle.bounding_box()
        if box is None:
            raise RuntimeError("Could not get slider bounding box")

        start_x = box["x"] + box["width"] / 2
        start_y = box["y"] + box["height"] / 2
        end_x = start_x + drag_pixels

        log.info(
            "Dragging slider from (%.0f, %.0f) to (%.0f, %.0f) — %d px",
            start_x,
            start_y,
            end_x,
            start_y,
            drag_pixels,
        )

        # Perform a human-like drag with multiple steps
        await page.mouse.move(start_x, start_y)
        await asyncio.sleep(0.1)
        await page.mouse.down()
        await asyncio.sleep(0.05)

        # Smooth drag: move in small steps with slight jitter
        steps = max(8, min(30, drag_pixels // 5))
        step_size = drag_pixels / steps
        for i in range(1, steps + 1):
            progress = i / steps
            # Add slight sine-wave jitter for human-like motion
            import math

            jitter = math.sin(progress * math.pi * 2) * 2
            current_x = start_x + step_size * i + jitter
            await page.mouse.move(
                current_x,
                start_y + random_uniform(-1, 1),
            )
            await asyncio.sleep(random_uniform(0.008, 0.025))

        await asyncio.sleep(random_uniform(0.1, 0.2))
        await page.mouse.up()
        log.info("Slider drag complete")

        # Wait for GeeTest to validate the slide
        await asyncio.sleep(2)

    async def _extract_tokens(self, page: Any) -> dict[str, str]:
        """Extract GeeTest validation tokens from the page.

        Returns dict with keys: challenge, validate, geetest_validate, seccode.
        If tokens are not found immediately, polls for a few seconds
        since GeeTest needs time to process the slide.
        """
        for attempt in range(10):
            result = await page.evaluate(_EXTRACT_GEETEST_TOKEN_JS)
            if result and isinstance(result, dict):
                validate_val = result.get("validate", "") or ""
                if validate_val and len(validate_val) > 10:
                    return {
                        "challenge": result.get("challenge", ""),
                        "validate": validate_val,
                        "geetest_validate": validate_val,
                        "seccode": result.get("seccode", ""),
                    }
            await asyncio.sleep(1)

        log.warning("GeeTest tokens not found after polling")
        return {
            "challenge": "",
            "validate": "",
            "geetest_validate": "",
            "seccode": "",
        }


def random_uniform(a: float, b: float) -> float:
    """Return a random float between a and b."""
    import random

    return random.uniform(a, b)
