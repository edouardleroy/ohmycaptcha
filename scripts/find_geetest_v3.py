"""Find a real GeeTest v3 integration by probing known public demos."""
from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.services.geetest import GeetestSolver
from src.core.config import load_config

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("find_geetest")

# Sites known to potentially use GeeTest v3
TARGETS = [
    # Capsolver GeeTest demo
    {
        "name": "capsolver-geetest",
        "url": "https://docs.capsolver.com/guide/recognition/GeetestTaskProxyless.html",
        "trigger": "geetest",
    },
    # Anti-captcha GeeTest demo
    {
        "name": "anticaptcha-geetest",
        "url": "https://anti-captcha.com/apidoc/task-types/GeeTestTaskProxyless",
        "trigger": "geetest",
    },
    # Some Chinese travel sites that historically used GeeTest v3
    {
        "name": "ctrip",
        "url": "https://passport.ctrip.com/user/login",
        "trigger": ".geetest",
    },
    # Official but older demo
    {
        "name": "geetest-old-demo",
        "url": "https://www.geetest.com/en/demo/gt",
        "trigger": "geetest",
    },
]


async def probe(solver: GeetestSolver, target: dict) -> dict:
    """Probe a single site for GeeTest v3."""
    result = {"name": target["name"], "url": target["url"], "status": "unknown", "details": {}}
    browser = solver._browser
    if not browser:
        result["status"] = "error"
        result["details"]["error"] = "No browser"
        return result

    ctx = await browser.new_context(viewport={"width": 1920, "height": 1080})
    page = await ctx.new_page()

    try:
        log.info("Probing %s: %s", target["name"], target["url"])
        await page.goto(target["url"], wait_until="domcontentloaded", timeout=30000)
        await asyncio.sleep(3)

        info = await page.evaluate("""() => {
            const i = {};
            // Check for v3 indicators
            i.has_initGeetest = typeof window.initGeetest === 'function';
            i.captchaObj = typeof window.captchaObj;
            i.geetest_elements = [...document.querySelectorAll('[class*=\"geetest\"]')].length;
            i.gt_elements = [...document.querySelectorAll('[class*=\"gt_\"]')].length;
            i.iframes = document.querySelectorAll('iframe').length;
            i.gt3_scripts = [...document.scripts]
                .map(s => s.src)
                .filter(s => s.includes('geetest') && !s.includes('gt4'));
            i.gt4_scripts = [...document.scripts]
                .map(s => s.src)
                .filter(s => s.includes('gt4'));
            i.has_geetest_v3_api = typeof window.geetest !== 'undefined' 
                || typeof window.GeeTest !== 'undefined';
            // Check for GeeTest canvas area
            i.canvas_geetest = [...document.querySelectorAll('canvas')]
                .filter(c => c.className.includes('geetest')).length;
            return i;
        }""")

        result["details"] = info
        if info["gt3_scripts"] or info["has_initGeetest"] or info["geetest_elements"] > 5:
            result["status"] = "geetest_v3_found"
        elif info["gt4_scripts"]:
            result["status"] = "geetest_v4_found"
        else:
            result["status"] = "no_geetest"

        log.info("  → %s: %s", result["status"], info)
        await page.screenshot(full_page=False, path=str(Path("test_screenshots") / f"probe_{target['name']}.png"))

    except Exception as e:
        result["status"] = "error"
        result["details"]["error"] = str(e)[:200]
        log.info("  → Error: %s", e)

    finally:
        await ctx.close()

    return result


async def main():
    cfg = load_config()
    solver = GeetestSolver(cfg)
    await solver.start()

    try:
        results = await asyncio.gather(*[probe(solver, t) for t in TARGETS])

        print("\n" + "=" * 60)
        print("GeeTest v3 Site Probe Results")
        print("=" * 60)
        for r in results:
            status_icon = {"geetest_v3_found": "✅ v3", "geetest_v4_found": "✅ v4", 
                          "no_geetest": "❌ none", "unknown": "❓", "error": "⚠️ error"}.get(r["status"], "❓")
            print(f"\n{status_icon} {r['name']}: {r['url']}")
            d = r["details"]
            if isinstance(d, dict):
                print(f"   initGeetest: {d.get('has_initGeetest')}, captchaObj: {d.get('captchaObj')}")
                print(f"   geetest_elements: {d.get('geetest_elements')}, gt_elements: {d.get('gt_elements')}")
                print(f"   v3 scripts: {d.get('gt3_scripts', [])[:2]}")
                print(f"   v4 scripts: {d.get('gt4_scripts', [])[:2]}")
                print(f"   iframes: {d.get('iframes')}, canvas: {d.get('canvas_geetest')}")

        v3_sites = [r for r in results if r["status"] == "geetest_v3_found"]
        if v3_sites:
            print(f"\n🎯 Found {len(v3_sites)} GeeTest v3 site(s) to test against!")
        else:
            print("\n⚠️ No GeeTest v3 sites found via these probes")

    finally:
        await solver.stop()


if __name__ == "__main__":
    asyncio.run(main())
