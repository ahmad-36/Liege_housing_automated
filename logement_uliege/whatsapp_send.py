"""
WhatsApp sender — messages landlords we could NOT reach by email.

Targets only listings from processed.json that:
  - have a phone number, AND
  - were never successfully emailed (no email, or the email bounced), AND
  - were not already messaged on WhatsApp (`whatsapped` timestamp)

Landlords whose phone number also appears on an emailed listing are
skipped too — nobody is ever contacted twice on any channel.

How it works: drives WhatsApp Web in a headless browser with a
persistent profile, so you scan the QR code once and stay logged in.
NOTE: automating WhatsApp Web is against WhatsApp's terms of service
and can get a number banned. Keep volumes low; the script waits
20-40 s between messages to stay human-like.

Usage:
  python3 whatsapp_send.py --dry-run   # list who would be messaged
  python3 whatsapp_send.py --login     # first-time login: scan wa_qr.png with your phone
  python3 whatsapp_send.py             # send to all pending targets
"""

import json
import random
import sys
import time
from pathlib import Path
from urllib.parse import quote

from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

from outreach import load_records, save_records, load_message, normalize_phone


def whatsapp_message() -> str:
    """message.txt minus the 'here is my WhatsApp number' line — pointless
    when the message already arrives on WhatsApp."""
    lines = [l for l in load_message().splitlines() if "whatsapp" not in l.lower()]
    text = "\n".join(lines)
    while "\n\n\n" in text:
        text = text.replace("\n\n\n", "\n\n")
    return text.strip()

SCRIPT_DIR = Path(__file__).parent
PROFILE_DIR = SCRIPT_DIR / "wa_profile"
QR_FILE = SCRIPT_DIR / "wa_qr.png"

LOGGED_IN_SEL = "#side"          # chat sidebar → session active
QR_SEL = "canvas"                # QR code canvas on the login page
COMPOSER_SEL = 'footer div[contenteditable="true"]'


def collect_targets(records: list[dict],
                    include_emailed: bool = False) -> list[tuple[str, list[dict]]]:
    emailed_phones = {
        normalize_phone(p)
        for r in records if r.get("emailed")
        for p in r.get("contact", {}).get("phones", [])
    }
    by_phone: dict[str, list[dict]] = {}
    for r in records:
        if r.get("whatsapped"):
            continue
        if r.get("emailed") and not include_emailed:
            continue
        # Only the first valid number per listing — landlords often list
        # two numbers and must not be messaged on both.
        for phone in r.get("contact", {}).get("phones", []):
            norm = normalize_phone(phone)
            if norm and (include_emailed or norm not in emailed_phones):
                by_phone.setdefault(norm, []).append(r)
                break
    return sorted(by_phone.items())


def message_for(base: str, group: list[dict]) -> str:
    """Append the listing link(s) so both sides can refer back to them."""
    if len(group) == 1:
        return f"{base}\n\nAnnonce concernée : {group[0]['url']}"
    urls = "\n".join(f"- {r['url']}" for r in group)
    return f"{base}\n\nAnnonces concernées :\n{urls}"


def ensure_logged_in(page) -> bool:
    page.goto("https://web.whatsapp.com/", timeout=60000)
    deadline = time.time() + 180
    qr_saved = False
    while time.time() < deadline:
        if page.locator(LOGGED_IN_SEL).count():
            print("WhatsApp session active.")
            return True
        if page.locator(QR_SEL).count() and not qr_saved:
            page.screenshot(path=str(QR_FILE))
            qr_saved = True
            print(f"Not logged in — QR code saved to {QR_FILE.name}.")
            print("Open it, then on your phone: WhatsApp → Settings →"
                  " Linked Devices → Link a Device → scan. Waiting up to 3 min...")
        elif qr_saved:
            page.screenshot(path=str(QR_FILE))  # QR refreshes periodically
        time.sleep(3)
    print("Timed out waiting for login.")
    return False


def send_one(page, phone: str, message: str) -> bool:
    page.goto(f"https://web.whatsapp.com/send?phone={phone}&text={quote(message)}",
              timeout=90000)
    try:
        page.wait_for_selector(COMPOSER_SEL, timeout=60000)
    except PWTimeout:
        # Usually means "phone number shared via url is invalid"
        return False
    time.sleep(3)  # let the pre-filled text land in the composer
    page.focus(COMPOSER_SEL)
    page.keyboard.press("Enter")
    time.sleep(6)  # give the message time to actually send
    return True


def main():
    dry_run = "--dry-run" in sys.argv
    login_only = "--login" in sys.argv
    include_emailed = "--include-emailed" in sys.argv

    records = load_records()
    message = whatsapp_message()
    targets = collect_targets(records, include_emailed)

    scope = "incl. already-emailed" if include_emailed else "no email contact yet"
    print(f"WhatsApp targets ({scope}): {len(targets)}")
    for phone, group in targets:
        refs = ", ".join(r["ref"] for r in group)
        name = group[0]["contact"].get("name") or "?"
        print(f"  +{phone} — {name} — refs: {refs}")

    if dry_run:
        return
    if not targets and not login_only:
        print("Nothing to send.")
        return

    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            str(PROFILE_DIR), headless=True,
            args=["--disable-blink-features=AutomationControlled"],
            user_agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                       "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            viewport={"width": 1280, "height": 900},
        )
        page = context.pages[0] if context.pages else context.new_page()

        if not ensure_logged_in(page):
            context.close()
            return
        if login_only:
            print("Login done — run without --login to send.")
            context.close()
            return

        sent = failed = 0
        for phone, group in targets:
            refs = ", ".join(r["ref"] for r in group)
            print(f"  +{phone} (refs {refs}) ...", end=" ", flush=True)
            try:
                ok = send_one(page, phone, message_for(message, group))
            except PWTimeout:
                ok = False
            stamp = time.strftime("%Y-%m-%d %H:%M")
            for r in group:
                if ok:
                    r["whatsapped"] = stamp
                    r["status"] = "whatsapped"
                else:
                    r["status"] = "whatsapp_failed"
            save_records(records)
            if ok:
                sent += 1
                print("SENT")
            else:
                failed += 1
                print("FAILED (invalid number or chat did not load)")
            time.sleep(random.uniform(15, 30))

        context.close()
        print(f"\nDONE — {sent} sent, {failed} failed. Log updated in processed.json.")


if __name__ == "__main__":
    main()
