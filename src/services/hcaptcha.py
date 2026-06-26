"""HCaptcha solver using Playwright browser automation (invisible_playwright).

Supports HCaptchaTaskProxyless task type.
Visits the target page, interacts with the hCaptcha widget, and extracts the response token.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from playwright.async_api import Browser
from invisible_playwright.async_api import InvisiblePlaywright

from ..core.config import Config

log = logging.getLogger(__name__)

_EXTRACT_HCAPTCHA_TOKEN_JS = """() => {
    const textarea = document.querySelector('[name="h-captcha-response"]')
        || document.querySelector('[name="g-recaptcha-response"]');
    if (textarea && textarea.value && textarea.value.length > 20) {
        return textarea.value;
    }
    if (window.hcaptcha && typeof window.hcaptcha.getResponse === 'function') {
        const resp = window.hcaptcha.getResponse();
        if (resp && resp.length > 20) return resp;
    }
    return null;
}
"""


class HCaptchaSolver:
    """Solves HCaptchaTaskProxyless tasks via invisible_playwright (patched Firefox)."""

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
        log.info("HCaptchaSolver browser started (invisible_playwright Firefox)")

    async def stop(self) -> None:
        if self._ipw:
            await self._ipw.__aexit__(None, None, None)
            self._ipw = None
            self._browser = None
        log.info("HCaptchaSolver stopped")

    async def solve(self, params: dict[str, Any]) -> dict[str, Any]:
        website_url = params["websiteURL"]
        website_key = params["websiteKey"]

        last_error: Exception | None = None
        for attempt in range(self._config.captcha_retries):
            try:
                token = await self._solve_once(website_url, website_key)
                return {"gRecaptchaResponse": token}
            except Exception as exc:
                last_error = exc
                log.warning(
                    "HCaptcha attempt %d/%d failed: %s",
                    attempt + 1,
                    self._config.captcha_retries,
                    exc,
                )
                if attempt < self._config.captcha_retries - 1:
                    await asyncio.sleep(2)

        raise RuntimeError(
            f"HCaptcha failed after {self._config.captcha_retries} attempts: {last_error}"
        )

    async def _solve_once(self, website_url: str, website_key: str) -> str:
        assert self._browser is not None

        context = await self._browser.new_context(
            viewport={"width": 1920, "height": 1080},
            locale="en-US",
        )
        page = await context.new_page()

        try:
            timeout_ms = self._config.browser_timeout * 1000
            await page.goto(website_url, wait_until="networkidle", timeout=timeout_ms)

            await page.mouse.move(400, 300)
            await asyncio.sleep(1)

            # Click only the checkbox iframe — match by specific title to avoid the challenge iframe
            iframe_element = page.frame_locator(
                'iframe[title="Widget containing checkbox for hCaptcha security challenge"]'
            )
            checkbox = iframe_element.locator("#checkbox")
            await checkbox.click(timeout=10_000)

            # Wait for token — may require challenge completion; poll up to 30s
            for _ in range(6):
                await asyncio.sleep(5)
                token = await page.evaluate(_EXTRACT_HCAPTCHA_TOKEN_JS)
                if isinstance(token, str) and len(token) > 20:
                    break
            else:
                token = None

            if not isinstance(token, str) or len(token) < 20:
                raise RuntimeError(f"Invalid hCaptcha token: {token!r}")

            log.info("Got hCaptcha token (len=%d)", len(token))
            return token
        finally:
            await context.close()
