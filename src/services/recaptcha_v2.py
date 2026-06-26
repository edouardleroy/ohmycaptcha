"""reCAPTCHA v2 solver using Playwright browser automation.

Supports NoCaptchaTaskProxyless, RecaptchaV2TaskProxyless,
and RecaptchaV2EnterpriseTaskProxyless task types.

Strategy:
  1. Visit the target page with a realistic browser context.
  2. Click the reCAPTCHA checkbox.
  3. If the challenge dialog appears (bot detected), intercept the audio
     network request, download the audio file, transcribe via Whisper,
     and submit the text.
  4. Extract the gRecaptchaResponse token.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx
from playwright.async_api import Browser
from invisible_playwright.async_api import InvisiblePlaywright

from ..core.config import Config

log = logging.getLogger(__name__)

_EXTRACT_TOKEN_JS = """() => {
    const textarea = document.querySelector('#g-recaptcha-response')
        || document.querySelector('[name="g-recaptcha-response"]');
    if (textarea && textarea.value && textarea.value.length > 20) {
        return textarea.value;
    }
    const gr = window.grecaptcha?.enterprise || window.grecaptcha;
    if (gr && typeof gr.getResponse === 'function') {
        const resp = gr.getResponse();
        if (resp && resp.length > 20) return resp;
    }
    return null;
}"""


class RecaptchaV2Solver:
    """Solves reCAPTCHA v2 tasks via headless Chromium with checkbox clicking.

    Falls back to the audio challenge path when Google presents a visual
    challenge to the headless browser.
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
        log.info("RecaptchaV2Solver browser started (invisible_playwright Firefox)")

    async def stop(self) -> None:
        if self._ipw:
            await self._ipw.__aexit__(None, None, None)
            self._ipw = None
            self._browser = None
        log.info("RecaptchaV2Solver stopped")

    async def solve(self, params: dict[str, Any]) -> dict[str, Any]:
        website_url = params["websiteURL"]
        website_key = params["websiteKey"]
        is_invisible = params.get("isInvisible", False)

        last_error: Exception | None = None
        for attempt in range(self._config.captcha_retries):
            try:
                token = await self._solve_once(website_url, website_key, is_invisible)
                return {"gRecaptchaResponse": token}
            except Exception as exc:
                last_error = exc
                log.warning(
                    "reCAPTCHA v2 attempt %d/%d failed: %s",
                    attempt + 1,
                    self._config.captcha_retries,
                    exc,
                )
                if attempt < self._config.captcha_retries - 1:
                    await asyncio.sleep(2)

        raise RuntimeError(
            f"reCAPTCHA v2 failed after {self._config.captcha_retries} attempts: {last_error}"
        )

    async def _solve_once(
        self, website_url: str, website_key: str, is_invisible: bool
    ) -> str:
        assert self._browser is not None

        context = await self._browser.new_context(
            viewport={"width": 1920, "height": 1080},
            locale="en-US",
        )
        page = await context.new_page()

        # Intercept audio requests from reCAPTCHA
        audio_promise: asyncio.Future[bytes] = asyncio.get_event_loop().create_future()

        async def handle_audio_route(route):
            url = route.request.url
            if "payload/audio" in url or ".mp3" in url:
                log.info("Intercepted audio request: %s", url[:80])
                resp = await route.fetch()
                audio_bytes = await resp.body()
                if not audio_promise.done():
                    audio_promise.set_result(audio_bytes)
                await route.fulfill(response=resp)
            else:
                await route.continue_()

        await page.route("**/*", handle_audio_route)

        try:
            timeout_ms = self._config.browser_timeout * 1000
            await page.goto(website_url, wait_until="networkidle", timeout=timeout_ms)
            await page.mouse.move(400, 300)
            await asyncio.sleep(0.5)

            if is_invisible:
                token = await asyncio.wait_for(
                    page.evaluate(
                        """
                        ([key]) => new Promise((resolve, reject) => {
                            const gr = window.grecaptcha?.enterprise || window.grecaptcha;
                            if (!gr) { reject(new Error('grecaptcha not found')); return; }
                            gr.ready(() => {
                                gr.execute(key).then(resolve).catch(reject);
                            });
                        })
                        """,
                        [website_key],
                    ),
                    timeout=15,
                )
            else:
                token = await self._solve_checkbox_with_image(page, website_key, audio_promise)

            if not isinstance(token, str) or len(token) < 20:
                raise RuntimeError(f"Invalid reCAPTCHA v2 token: {token!r}")

            log.info("Got reCAPTCHA v2 token (len=%d)", len(token))
            return token
        finally:
            await context.close()

    async def _solve_checkbox_with_image(
        self, page: Any, website_key: str, audio_promise: asyncio.Future[bytes]
    ) -> str | None:
        """Click checkbox, then try audio challenge via network interception + Whisper."""
        checkbox_frame = page.frame_locator('iframe[title="reCAPTCHA"]').first
        checkbox = checkbox_frame.locator("#recaptcha-anchor")
        await checkbox.click(timeout=10_000)
        await asyncio.sleep(2)

        # Check if token was issued immediately
        token = await page.evaluate(_EXTRACT_TOKEN_JS)
        if isinstance(token, str) and len(token) > 20:
            return token

        # Challenge appeared. Try audio by clicking the audio button
        log.info("Challenge detected, switching to audio mode...")
        try:
            bframe = None
            for f in page.frames:
                if 'bframe' in f.url:
                    bframe = f
                    break

            if bframe is None:
                log.warning("Could not find bframe for audio challenge")
            else:
                # Click the audio button
                await bframe.locator("#recaptcha-audio-button").click(timeout=8_000)
                log.info("Clicked audio button, waiting for audio request...")
                await asyncio.sleep(2)

                # Wait for audio request to be intercepted
                try:
                    audio_bytes = await asyncio.wait_for(audio_promise, timeout=15)
                    log.info("Audio file intercepted (%d bytes)", len(audio_bytes))

                    # Transcribe with Whisper
                    transcript = await self._transcribe_audio(audio_bytes)
                    log.info("Whisper transcription: %s", transcript)

                    if transcript:
                        # Submit the transcription
                        audio_input = bframe.locator("#audio-response")
                        await audio_input.fill(transcript.strip().lower())
                        await asyncio.sleep(0.5)
                        verify_btn = bframe.locator("#recaptcha-verify-button")
                        await verify_btn.click(timeout=8_000)
                        await asyncio.sleep(2)

                        token = await page.evaluate(_EXTRACT_TOKEN_JS)
                        if isinstance(token, str) and len(token) > 20:
                            log.info("Audio challenge solved!")
                            return token
                except asyncio.TimeoutError:
                    log.warning("Audio request not intercepted within 15s")
        except Exception as exc:
            log.warning("Audio challenge path failed: %s", exc)

        # Fallback: try grecaptcha.execute() directly (with timeout)
        log.info("Trying grecaptcha.execute() fallback...")
        try:
            token = await asyncio.wait_for(
                page.evaluate(
                    "([key]) => new Promise((resolve, reject) => {"
                    "  const gr = window.grecaptcha?.enterprise || window.grecaptcha;"
                    "  if (gr && typeof gr.execute === 'function') {"
                    "    gr.ready(() => { gr.execute(key).then(resolve).catch(reject); });"
                    "  } else { reject(new Error('no grecaptcha')); }"
                    "})",
                    [website_key]
                ),
                timeout=10
            )
            if isinstance(token, str) and len(token) > 20:
                return token
        except asyncio.TimeoutError:
            log.warning("grecaptcha.execute() fallback timed out")
        except Exception as exc:
            log.warning("grecaptcha.execute() fallback also failed: %s", exc)

        log.warning("All reCAPTCHA v2 fallbacks exhausted — site likely blocked the headless browser")
        return None

    async def _transcribe_audio(self, audio_bytes: bytes) -> str | None:
        """Transcribe reCAPTCHA audio using Whisper (local)."""
        import tempfile

        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
            f.write(audio_bytes)
            tmp_path = f.name

        try:
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(
                None, self._transcribe_sync, tmp_path
            )
            return result
        finally:
            import os
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

    @staticmethod
    def _transcribe_sync(audio_path: str) -> str | None:
        """Synchronous Whisper transcription (runs in thread pool)."""
        import whisper

        if not hasattr(RecaptchaV2Solver, '_whisper_model'):
            RecaptchaV2Solver._whisper_model = whisper.load_model("tiny")
        model = RecaptchaV2Solver._whisper_model

        result = model.transcribe(audio_path, language="en", fp16=False)
        text = result["text"].strip()

        if not text:
            return None

        # Normalize: extract only digits and spaces (reCAPTCHA audio is digits)
        import re
        digits_only = re.sub(r'[^0-9\s]', '', text)
        if digits_only.strip():
            return digits_only.strip()
        return text
