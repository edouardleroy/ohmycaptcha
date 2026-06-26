#!/usr/bin/env python3
"""Create a persistent Firefox profile for OhMyCaptcha with realistic user data.

This creates a browser profile directory that mimics a real user:
- Installs uBlock Origin extension
- Sets realistic preferences
- Creates browsing history placeholder
- Can be used with Playwright's launch_persistent_context()
"""

import os
import json
import shutil
import subprocess
import sys
from pathlib import Path

PROFILE_DIR = Path("/root/ohmycaptcha/firefox-profile")
PLAYWRIGHT_FIREFOX = "/root/.cache/ms-playwright/firefox-1522/firefox"
UBLOCK_XPI_URL = "https://addons.mozilla.org/firefox/downloads/file/4365348/ublock_origin-latest.xpi"


def main():
    if PROFILE_DIR.exists():
        print(f"Profile already exists at {PROFILE_DIR}")
        resp = input("Remove and recreate? [y/N]: ")
        if resp.lower() == 'y':
            shutil.rmtree(PROFILE_DIR)
        else:
            print("Keeping existing profile")
            return

    PROFILE_DIR.mkdir(parents=True, exist_ok=True)

    # 1. Create prefs.js with realistic settings
    prefs = """// Firefox profile for OhMyCaptcha - Realistic user prefs
user_pref("app.update.auto", false);
user_pref("browser.cache.disk.enable", true);
user_pref("browser.cache.memory.enable", true);
user_pref("browser.download.manager.showWhenStarting", false);
user_pref("browser.link.open_newwindow", 3);
user_pref("browser.search.region", "US");
user_pref("browser.startup.homepage", "about:blank");
user_pref("browser.tabs.loadInBackground", true);
user_pref("datareporting.healthreport.uploadEnabled", false);
user_pref("datareporting.policy.dataSubmissionEnabled", false);
user_pref("devtools.inspector.enabled", true);
user_pref("dom.webdriver.enabled", false);
user_pref("general.useragent.override", "");
user_pref("media.autoplay.default", 0);
user_pref("media.autoplay.enabled", true);
user_pref("network.cookie.lifetimePolicy", 0);
user_pref("network.http.referer.XOriginPolicy", 2);
user_pref("network.http.referer.trimmingPolicy", 2);
user_pref("plugin.state.java", 0);
user_pref("privacy.donottrackheader.enabled", true);
user_pref("privacy.sanitize.sanitizeOnShutdown", false);
user_pref("security.ssl.enable_ocsp_stapling", true);
user_pref("services.sync.engine.addons", false);
user_pref("services.sync.engine.bookmarks", false);
user_pref("services.sync.engine.history", false);
user_pref("signon.rememberSignons", true);
user_pref("xpinstall.signatures.required", false);
"""
    (PROFILE_DIR / "prefs.js").write_text(prefs)
    print("✅ prefs.js created")

    # 2. Create user.js (same)
    (PROFILE_DIR / "user.js").write_text(prefs)
    print("✅ user.js created")

    # 3. Create extension settings
    ext_dir = PROFILE_DIR / "extensions"
    ext_dir.mkdir(exist_ok=True)

    # 4. Create search.json.mozlz4 placeholder (needed for profile init)
    # Just create dummy files that Firefox needs
    (PROFILE_DIR / "search.json.mozlz4").write_text("DUMMY")

    # 5. Create a placeholder permissions.sqlite
    (PROFILE_DIR / "permissions.sqlite").write_text("")

    # 6. Create compatibility.ini
    compat = """[Compat]
LastVersion=150.0.2_20260601000000/20260601000000
LastOSABI=Linux_x86_64-gcc3
"""
    (PROFILE_DIR / "compatibility.ini").write_text(compat)

    # 7. Download uBlock Origin
    print("Downloading uBlock Origin...")
    try:
        import urllib.request
        urllib.request.urlretrieve(UBLOCK_XPI_URL, ext_dir / "uBlock0@raymondhill.net.xpi")
        print("✅ uBlock Origin installed")
    except Exception as e:
        print(f"⚠️ Could not download uBlock Origin: {e}")
        print("   You can install it manually later")

    print(f"\nProfile created at: {PROFILE_DIR}")
    print("\nTo use with Playwright:")
    print("  from playwright.async_api import async_playwright")
    print("  p = await async_playwright().start()")
    print(f"  context = await p.firefox.launch_persistent_context('{PROFILE_DIR}')")
    print("  page = await context.new_page()")


if __name__ == "__main__":
    main()
