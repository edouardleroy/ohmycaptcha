"""Cloudflare Turnstile solver using Playwright browser automation (invisible_playwright).

Supports TurnstileTaskProxyless and TurnstileTaskProxylessM1 task types.
Visits the target page, interacts with the Turnstile widget, and extracts the token.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from playwright.async_api import Browser
from invisible_playwright.async_api import InvisiblePlaywright

from ..core.config import Config

log = logging.getLogger(__name__)


class TurnstileSolver:
    """Solves Cloudflare Turnstile tasks via invisible_playwright (patched Firefox)."""

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
        log.info("TurnstileSolver browser started (invisible_playwright Firefox)")

    async def stop(self) -> None:
        if self._ipw:
            await self._ipw.__aexit__(None, None, None)
            self._ipw = None
            self._browser = None
        log.info("TurnstileSolver stopped")

    async def solve(self, params: dict[str, Any]) -> dict[str, Any]:
        website_url = params["websiteURL"]
        website_key = params["websiteKey"]

        last_error: Exception | None = None
        for attempt in range(self._config.captcha_retries):
            try:
                token = await self._solve_once(website_url, website_key)
                return {"token": token}
            except Exception as exc:
                last_error = exc
                log.warning(
                    "Turnstile attempt %d/%d failed: %s",
                    attempt + 1,
                    self._config.captcha_retries,
                    exc,
                )
                if attempt < self._config.captcha_retries - 1:
                    await asyncio.sleep(2)

        raise RuntimeError(
            f"Turnstile failed after {self._config.captcha_retries} attempts: {last_error}"
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

            # Check if token is already present (auto-solve)
            token = await self._get_token(page)
            if token:
                log.info("Got Turnstile token immediately (len=%d)", len(token))
                return token

            # Try clicking the Turnstile checkbox if visible
            try:
                cf_frame = page.frame_locator(
                    'iframe[src*="challenges.cloudflare.com"], iframe[src*="turnstile"]'
                )
                checkbox = cf_frame.locator('[role="checkbox"], .ctp-checkbox-label, #checkbox')
                await checkbox.click(timeout=8_000)
                log.info("Clicked Turnstile checkbox")
            except Exception:
                log.info("No Turnstile checkbox found, waiting for auto-solve")

            # Wait for token with retry
            for _ in range(20):
                await asyncio.sleep(3)
                token = await self._get_token(page)
                if token:
                    log.info("Got Turnstile token (len=%d)", len(token))
                    return token

            raise RuntimeError("Turnstile token not obtained within timeout")
        finally:
            await context.close()

    async def _get_token(self, page: Any) -> str | None:
        """Extract Turnstile token using native locators (no CSP issues)."""
        # Check cf-turnstile-response input (standard widget)
        for selector in ['[name="cf-turnstile-response"]', 'input[name*="turnstile"]']:
            try:
                input_el = page.locator(selector).first
                count = await input_el.count()
                if count > 0:
                    value = await input_el.input_value(timeout=1000)
                    if value and len(value) > 20:
                        return value
            except Exception:
                pass

        # Try via turnstile API (may be blocked by CSP)
        try:
            api_value = await page.evaluate(
                "window.turnstile?.getResponse?.() || null"
            )
            if isinstance(api_value, str) and len(api_value) > 20:
                return api_value
        except Exception:
            pass

        return None
