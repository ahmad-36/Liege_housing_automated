"""
Outreach for collected ULiège listings — email + WhatsApp.

Works through processed.json (written by run.py):
  - Records with an email address and no email sent yet → sends your
    message.txt via Gmail SMTP and marks them "emailed".
  - Records with a phone number → generates whatsapp.html, a page of
    click-to-chat links with your message pre-filled. WhatsApp has no
    official API for personal accounts (and bot-driving WhatsApp Web
    gets numbers banned), so the last click stays manual: one click per
    landlord opens the chat with the text ready to send.

Setup:
  message.txt            — the message to send (used for both channels)
  email_credentials.json — {"email": "you@gmail.com", "app_password": "..."}
                           Create an app password at
                           https://myaccount.google.com/apppasswords
                           (requires 2-step verification; your normal
                           password will NOT work)

Usage:
  python3 outreach.py --dry-run   # show who would be emailed, send nothing
  python3 outreach.py             # send emails + regenerate whatsapp.html
"""

import html
import json
import os
import re
import smtplib
import sys
import time
from email.message import EmailMessage
from email.utils import formataddr
from pathlib import Path
from urllib.parse import quote

SCRIPT_DIR = Path(__file__).parent

# Per-user profiles: when the Streamlit UI (app.py) runs this script for a
# profile, LIEGE_PROFILE_DIR points at that user's folder.
PROFILE_DIR = (Path(os.environ["LIEGE_PROFILE_DIR"])
               if os.environ.get("LIEGE_PROFILE_DIR") else None)

PROCESSED_FILE = PROFILE_DIR / "uliege_processed.json" if PROFILE_DIR else SCRIPT_DIR / "processed.json"
MESSAGE_FILE = PROFILE_DIR / "uliege_message.txt" if PROFILE_DIR else SCRIPT_DIR / "message.txt"
WHATSAPP_FILE = PROFILE_DIR / "whatsapp.html" if PROFILE_DIR else SCRIPT_DIR / "whatsapp.html"
EMAIL_CREDS_FILE = SCRIPT_DIR / "email_credentials.json"

# Credentials can also come from a .env file at the repo root:
#   GMAIL_ADDRESS=... / GMAIL_APP_PASSWORD=... / GMAIL_DISPLAY_NAME=...
# Profile runs get their credentials injected by app.py instead — never
# fall back to the owner's .env / email_credentials.json for a profile.
if PROFILE_DIR is None:
    try:
        from dotenv import load_dotenv
        load_dotenv(SCRIPT_DIR.parent / ".env")
    except ImportError:
        pass


def load_email_creds() -> dict:
    creds = {}
    if PROFILE_DIR is None and EMAIL_CREDS_FILE.exists():
        creds = json.loads(EMAIL_CREDS_FILE.read_text())
    return {
        "email": os.environ.get("GMAIL_ADDRESS") or creds.get("email"),
        "app_password": os.environ.get("GMAIL_APP_PASSWORD") or creds.get("app_password"),
        "display_name": os.environ.get("GMAIL_DISPLAY_NAME") or creds.get("display_name", ""),
    }

EMAIL_SUBJECT = "Recherche de logement étudiant — votre annonce ULiège (ref {ref})"
EMAIL_DELAY = 5  # seconds between emails — don't look like a spammer


def load_records() -> list[dict]:
    if not PROCESSED_FILE.exists():
        sys.exit("No processed.json found — run `python3 run.py` first.")
    return json.loads(PROCESSED_FILE.read_text())


def save_records(records: list[dict]):
    PROCESSED_FILE.write_text(json.dumps(records, indent=2, ensure_ascii=False))


def load_message() -> str:
    if not MESSAGE_FILE.exists():
        sys.exit("No message.txt found — write your message there first.")
    return MESSAGE_FILE.read_text().strip()


def normalize_phone(phone: str) -> str | None:
    """Convert a phone string to international digits for wa.me links."""
    digits = re.sub(r"[^\d+]", "", phone)
    if digits.startswith("00"):
        digits = "+" + digits[2:]
    if digits.startswith("0") and len(digits) in (9, 10):
        digits = "+32" + digits[1:]  # assume Belgian number
    if not digits.startswith("+"):
        return None
    # "+32 0485..." — drop the redundant trunk zero after the country code
    if digits.startswith("+320"):
        digits = "+32" + digits[4:]
    return digits[1:]


# ── Email ─────────────────────────────────────────────────────────────

def send_emails(records: list[dict], message: str, dry_run: bool):
    # One email per landlord — group listings by email address so owners
    # of several listings get a single message covering all of them.
    by_addr: dict[str, list[dict]] = {}
    for r in records:
        if (r.get("contact", {}).get("emails") and not r.get("emailed")
                and r.get("status") != "email_bounced"):
            by_addr.setdefault(r["contact"]["emails"][0].lower(), []).append(r)

    print(f"── EMAIL: {sum(len(v) for v in by_addr.values())} listings, "
          f"{len(by_addr)} landlords to contact ──")
    if not by_addr:
        return

    if dry_run:
        for addr, group in by_addr.items():
            refs = ", ".join(r["ref"] for r in group)
            print(f"  would email {addr}"
                  f" ({group[0]['contact'].get('name', '?')}) — refs: {refs}")
        return

    creds = load_email_creds()
    if not creds.get("email") or not creds.get("app_password"):
        print("  SKIPPED: no Gmail credentials. Set GMAIL_ADDRESS / GMAIL_APP_PASSWORD"
              " in the .env file (or fill in email_credentials.json).")
        return

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
        smtp.login(creds["email"], creds["app_password"])
        for addr, group in by_addr.items():
            refs = ", ".join(r["ref"] for r in group)
            urls = "\n".join(f"  - {r['url']}" for r in group)
            plural = "s" if len(group) > 1 else ""
            msg = EmailMessage()
            msg["From"] = formataddr((creds.get("display_name", ""), creds["email"]))
            msg["To"] = addr
            msg["Subject"] = EMAIL_SUBJECT.format(ref=refs)
            msg.set_content(f"{message}\n\nAnnonce{plural} concernée{plural} :\n{urls}")
            print(f"  {refs} → {addr} ...", end=" ", flush=True)
            try:
                smtp.send_message(msg)
                stamp = time.strftime("%Y-%m-%d %H:%M")
                for r in group:
                    r["emailed"] = stamp
                    r["status"] = "emailed"
                print("SENT")
            except smtplib.SMTPException as e:
                print(f"FAILED: {e}")
            save_records(records)
            time.sleep(EMAIL_DELAY)


# ── WhatsApp click-to-chat page ───────────────────────────────────────

def build_whatsapp_page(records: list[dict], message: str):
    # One link per phone number — landlords with several listings appear once
    by_phone: dict[str, list[dict]] = {}
    for r in records:
        for phone in r.get("contact", {}).get("phones", []):
            norm = normalize_phone(phone)
            if norm:
                by_phone.setdefault(norm, []).append(r)

    rows = []
    for norm, group in by_phone.items():
        link = f"https://wa.me/{norm}?text={quote(message)}"
        refs = ", ".join(
            f"<a href='{r['url']}' target='_blank'>{r['ref']}</a> ({r['total']}€)"
            for r in group
        )
        rows.append(
            f"<li><a href='{link}' target='_blank'>"
            f"{html.escape(group[0]['contact'].get('name') or '?')} — +{norm}</a>"
            f" — annonces : {refs}</li>"
        )
    WHATSAPP_FILE.write_text(
        "<!doctype html><meta charset='utf-8'><title>WhatsApp outreach</title>"
        "<h2>Click-to-chat — one click per landlord, message pre-filled</h2>"
        f"<ol>{''.join(rows)}</ol>"
    )
    print(f"── WHATSAPP: {len(rows)} click-to-chat links → {WHATSAPP_FILE.name} ──")


def main():
    dry_run = "--dry-run" in sys.argv
    records = load_records()
    message = load_message()

    print(f"Loaded {len(records)} processed listings, "
          f"mode: {'DRY RUN' if dry_run else 'LIVE'}\n")

    send_emails(records, message, dry_run)
    print()
    if not dry_run:
        build_whatsapp_page(records, message)
        print("\nOpen whatsapp.html in a browser to send the WhatsApp messages.")


if __name__ == "__main__":
    main()
