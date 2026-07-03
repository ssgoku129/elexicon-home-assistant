#!/usr/bin/env python3
"""
OPTIONAL best-effort auto-downloader for Elexicon Green Button data (Playwright).

It talks DIRECTLY to the Green Button connector (greenbuttonconnector.com), which
is plain IIS (no Cloudflare), so unattended downloads can run headless. No
my.elexicon portal password is needed — only your account number + postal code
(in config.toml) plus a ONE-TIME human CAPTCHA solve to seed the connector session.

The connector keeps its own session cookie in the persistent browser profile.
Once seeded, downloads reuse it until it expires, at which point you re-seed.

  * Seed / re-seed the connector session (opens a real browser; solve the CAPTCHA):
        python elexicon_download.py --login
    Account number + postal code are pre-filled; just complete the CAPTCHA and
    click Submit. When it reaches the data page, the session is saved.

  * Unattended download (headless), optionally import straight into HA:
        python elexicon_download.py
        python elexicon_download.py --import

If a download run finds the session expired, it exits asking you to run --login.

Requires:  pip install playwright  &&  playwright install chromium
Selectors are grouped in SEL below; tweak there if the connector changes.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
import tomllib

HERE = os.path.dirname(os.path.abspath(__file__))
COOKIE_FILE = os.path.join(HERE, "connector_session.json")

SEL = {
    "account": 'input[name="account_number"]',
    "postal": 'input[name="postal_code"]',
    "captcha": ".g-recaptcha",
    "electric_checkbox": "#electricMeterInfo",   # "Electric Usage" data type
    "timespan_select": "#timeDuration",          # 30 Days / 60 Days / 90 Days / 2 Years / Custom
    "download_my_data": re.compile(r"download my data", re.I),
}


def log(*a):
    print("[elexicon_download]", *a, flush=True)


def load_cfg(path: str) -> dict:
    with open(path, "rb") as f:
        return tomllib.load(f)


def urls(cfg: dict):
    base = cfg["browser"]["connector_base"].rstrip("/")
    return (
        f"{base}/greenbutton/validate/?next=/greenbutton/",
        f"{base}/greenbutton/download/",
    )


def on_validate(page) -> bool:
    """True if we're sitting on the one-time-access (CAPTCHA) page = not authenticated."""
    return "/validate/" in page.url or page.locator(SEL["captcha"]).count() > 0


def save_session(context) -> None:
    """Persist cookies (incl. the connector's session cookie, which otherwise dies
    on browser close) so unattended runs can reuse the seeded session."""
    cookies = context.cookies()
    for c in cookies:
        if not c.get("expires") or c["expires"] < 0:   # session cookie -> give it 30 days
            c["expires"] = time.time() + 30 * 86400
    json.dump(cookies, open(COOKIE_FILE, "w"))
    log(f"Saved connector session ({len(cookies)} cookies).")


def load_session(context) -> bool:
    if not os.path.exists(COOKIE_FILE):
        return False
    try:
        context.add_cookies(json.load(open(COOKIE_FILE)))
        return True
    except Exception as e:
        log("warn: could not load saved session:", repr(e)[:80])
        return False


# --------------------------------------------------------------------------- #
def seed(page, cfg) -> None:
    validate_url, download_url = urls(cfg)
    page.goto(validate_url, wait_until="domcontentloaded")
    page.wait_for_timeout(2000)
    try:
        page.fill(SEL["account"], str(cfg["browser"]["account_number"]))
        page.fill(SEL["postal"], str(cfg["browser"]["postal_code"]))
        log("Account number + postal code pre-filled.")
    except Exception:
        log("Could not pre-fill (form may differ) — enter them manually.")
    log(">> In the browser window: solve the CAPTCHA and click Submit. Waiting...")
    for _ in range(120):  # ~6 minutes
        page.wait_for_timeout(3000)
        if not on_validate(page):
            log("Connector session established.")
            break
    else:
        sys.exit("Timed out waiting for the one-time access to complete.")

    # Show the data page structure so selectors can be confirmed/tuned.
    page.goto(download_url, wait_until="domcontentloaded")
    page.wait_for_timeout(2500)
    if on_validate(page):
        sys.exit("Reached /validate/ again — session not established. Try once more.")
    log(f"Data page: {page.url}")
    dump = page.evaluate("""() => {
        const q = s => [...document.querySelectorAll(s)];
        return JSON.stringify({
          checkboxes: q('input[type=checkbox]').map(e=>({name:e.name,id:e.id,checked:e.checked})),
          selects: q('select').map(e=>({name:e.name,id:e.id,options:[...e.options].map(o=>o.text)})),
          buttons: q('button,input[type=submit],a.btn').map(e=>(e.value||e.textContent||'').trim()).filter(Boolean),
        });
    }""")
    log("Download form structure:")
    log(dump)
    log("Session seeded. Unattended downloads should now work.")


def download(context, page, cfg) -> str:
    _, download_url = urls(cfg)
    page.goto(download_url, wait_until="domcontentloaded")
    page.wait_for_timeout(3000)
    if on_validate(page):
        sys.exit("Connector session expired. Re-seed with:  python elexicon_download.py --login")
    log(f"On data page: {page.url}")

    # Open the per-account "Download My Data" modal via its "Download Data" button.
    page.get_by_role("button", name=re.compile(r"download data", re.I)).first.click()
    page.wait_for_selector(SEL["electric_checkbox"], state="visible", timeout=20000)
    log("Download modal open.")

    # Select the "Electric Usage" data type + time span inside the modal.
    page.check(SEL["electric_checkbox"])
    try:
        page.select_option(SEL["timespan_select"], label=cfg["browser"].get("time_span", "30 Days"))
    except Exception as e:
        log("warn: could not set time span:", repr(e)[:80])

    target_dir = cfg["browser"]["download_dir"]
    os.makedirs(target_dir, exist_ok=True)
    # The modal's submit (a visible button/link reading "Download My Data").
    submit = page.locator(
        "button:visible, a.btn:visible, input[type=submit]:visible",
        has_text=SEL["download_my_data"],
    ).last
    with page.expect_download(timeout=120000) as dl:
        submit.click()
    d = dl.value
    name = d.suggested_filename or "Elexicon_Electricity_Interval.xml"
    dest = os.path.join(target_dir, name)
    d.save_as(dest)
    log(f"Downloaded: {dest}")
    return dest


# --------------------------------------------------------------------------- #
def main() -> None:
    ap = argparse.ArgumentParser(description="Auto-download Elexicon Green Button data via the connector.")
    ap.add_argument("--config", default=os.path.join(HERE, "config.toml"))
    ap.add_argument("--login", action="store_true", help="Headed: seed/refresh the connector session (solve CAPTCHA).")
    ap.add_argument("--headed", action="store_true", help="Run the download with a visible browser (debugging).")
    ap.add_argument("--import", dest="do_import", action="store_true", help="Run elexicon_to_ha.py after download.")
    args = ap.parse_args()

    cfg = load_cfg(args.config)
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        sys.exit("Playwright not installed. Run:  pip install playwright  &&  playwright install chromium")

    profile = os.path.join(HERE, cfg["browser"].get("profile_dir", "browser_profile"))
    headless = False if (args.login or args.headed) else bool(cfg["browser"].get("headless", True))
    dest = None

    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            profile, headless=headless, accept_downloads=True,
            args=["--disable-blink-features=AutomationControlled"],
        )
        page = context.pages[0] if context.pages else context.new_page()
        try:
            if args.login:
                seed(page, cfg)
                save_session(context)
            else:
                load_session(context)
                dest = download(context, page, cfg)
        finally:
            context.close()

    if args.do_import and dest:
        log("Importing into Home Assistant...")
        subprocess.run(
            [sys.executable, os.path.join(HERE, "elexicon_to_ha.py"), "--file", os.path.normpath(dest)],
            check=False,
        )


if __name__ == "__main__":
    main()
