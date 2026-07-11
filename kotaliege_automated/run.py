"""
KotaLiege Auto-Messenger — run this anytime to catch & message new listings.

How it works:
  1. Loads contacted.json (persistent log of every listing already handled)
  2. Scrapes all listing types across all pages
  3. Filters by criteria (budget ≤500€, domicile OK, available Aug/Sep 2026+)
  4. Skips any listing already in contacted.json
  5. Messages new listings via the platform
  6. Updates contacted.json with results

Usage:
  python3 run.py              # scrape + message new listings
  python3 run.py --dry-run    # scrape only, show what would be messaged
"""

import asyncio
import json
import re
import sys
from datetime import datetime
from dataclasses import dataclass, asdict
from pathlib import Path
from playwright.async_api import async_playwright, Page

BASE_URL = "https://www.kotaliege.be"
SCRIPT_DIR = Path(__file__).parent
STATE_FILE = SCRIPT_DIR / "session_state.json"
CONTACTED_FILE = SCRIPT_DIR / "contacted.json"
CREDS_FILE = SCRIPT_DIR / "credentials.json"

LISTING_TYPES = ["kots", "studios", "kots-chez-l-habitant", "colocations", "nouvelles"]
MAX_PAGES = 20

MESSAGE_FILE = SCRIPT_DIR / "message.txt"

if MESSAGE_FILE.exists():
    MESSAGE = MESSAGE_FILE.read_text().strip()
else:
    MESSAGE = """Bonjour,

Je suis étudiant et je cherche une chambre à Liège. Pourriez-vous me faire savoir si la chambre est encore disponible ?

Cordialement"""


# ── Persistent contacted log ──────────────────────────────────────────

def load_contacted() -> dict[str, dict]:
    """Load contacted.json → {ref: {ref, status, date, ...}}"""
    if not CONTACTED_FILE.exists():
        return {}
    data = json.loads(CONTACTED_FILE.read_text())
    return {r["ref"]: r for r in data}


def save_contacted(contacted: dict[str, dict]):
    records = sorted(contacted.values(), key=lambda r: r["ref"])
    CONTACTED_FILE.write_text(json.dumps(records, indent=2, ensure_ascii=False))


# ── Filters ───────────────────────────────────────────────────────────

def parse_date_text(text: str) -> datetime | None:
    text = text.strip().lower().replace(".", "")
    month_map = {
        "janv": 1, "jan": 1, "fév": 2, "feb": 2, "févr": 2,
        "mars": 3, "mar": 3, "avr": 4, "apr": 4, "avril": 4,
        "mai": 5, "may": 5, "juin": 6, "jun": 6,
        "juil": 7, "jul": 7, "août": 8, "aug": 8, "aout": 8,
        "sept": 9, "sep": 9, "oct": 10, "nov": 11, "déc": 12, "dec": 12,
    }
    for name, num in month_map.items():
        if name in text:
            day_match = re.search(r"(\d{1,2})", text)
            day = int(day_match.group(1)) if day_match else 1
            year_match = re.search(r"(202\d)", text)
            year = int(year_match.group(1)) if year_match else 2026
            try:
                return datetime(year, num, day)
            except ValueError:
                return None
    return None


def is_available_ok(text: str) -> bool:
    if not text.strip():
        return True
    dt = parse_date_text(text)
    if dt is None:
        return True
    return dt >= datetime(2026, 8, 1)


def is_domicile_ok(text: str) -> bool:
    lower = text.strip().lower()
    if not lower:
        return True
    return not any(r in lower for r in ["refusée", "refusee", "non", "no", "refused"])


# ── Listing model ─────────────────────────────────────────────────────

@dataclass
class Listing:
    ref: str
    title: str
    rent: int | None
    charges: int | None
    total: int | None
    size: int | None
    neighborhood: str
    duration: str
    domiciliation: str
    available: str
    url: str
    listing_type: str


async def extract_listing(article, listing_type: str) -> Listing | None:
    link = await article.query_selector("a.link-to-detail")
    if not link:
        return None
    href = await link.get_attribute("href") or ""

    type_el = await article.query_selector(".listing-teaser-type")
    title = (await type_el.inner_text()).strip() if type_el else ""

    size = None
    if "m²" in title:
        try:
            size = int("".join(c for c in title.split("m²")[0].split()[-1] if c.isdigit()))
        except (ValueError, IndexError):
            pass

    hood_el = await article.query_selector(".listing-teaser-neighborhood")
    if not hood_el:
        hood_el = await article.query_selector(".listing-teaser-residence")
    neighborhood = (await hood_el.inner_text()).strip() if hood_el else ""

    rent_el = await article.query_selector(".listing-rent--rent-wo-charges")
    rent = None
    if rent_el:
        rent_text = (await rent_el.inner_text()).strip()
        try:
            rent = int("".join(c for c in rent_text.split("€")[0] if c.isdigit()))
        except ValueError:
            pass

    ref_el = await article.query_selector(".listing-teaser-reference")
    ref = (await ref_el.inner_text()).strip() if ref_el else ""

    text = (await article.inner_text()).strip()
    charges = None
    duration = ""
    domiciliation = ""
    for line in text.split("\n"):
        line = line.strip()
        if line.startswith("Charges:"):
            try:
                charges = int("".join(c for c in line.split("€")[0].split(":")[-1] if c.isdigit()))
            except ValueError:
                pass
        elif line.startswith("Durée:"):
            duration = line.split(":", 1)[-1].strip()
        elif line.startswith("Domiciliation:"):
            domiciliation = line.split(":", 1)[-1].strip()

    available = ""
    tag_els = await article.query_selector_all(".listing-tag-available")
    if tag_els:
        available = (await tag_els[0].inner_text()).strip()

    total = rent + (charges or 0) if rent is not None else None

    return Listing(
        ref=ref, title=title, rent=rent, charges=charges, total=total,
        size=size, neighborhood=neighborhood, duration=duration,
        domiciliation=domiciliation, available=available,
        url=f"{BASE_URL}{href}" if not href.startswith("http") else href,
        listing_type=listing_type,
    )


# ── Scraping ──────────────────────────────────────────────────────────

async def scrape_type(page: Page, listing_type: str) -> list[Listing]:
    results = []
    for page_num in range(1, MAX_PAGES + 1):
        url = f"{BASE_URL}/{listing_type}" + (f"/{page_num}" if page_num > 1 else "")
        print(f"  [{listing_type}] page {page_num} ...", end=" ", flush=True)
        await page.goto(url, wait_until="domcontentloaded", timeout=30000)
        await page.wait_for_timeout(1500)

        articles = await page.query_selector_all("article.listing-teaser")
        if not articles:
            print("done.")
            break

        count = 0
        for article in articles:
            listing = await extract_listing(article, listing_type)
            if listing and listing.ref:
                results.append(listing)
                count += 1
        print(f"{count}")
        if count == 0:
            break
    return results


# ── Login ─────────────────────────────────────────────────────────────

async def ensure_logged_in(page: Page, context) -> bool:
    # Try saved session first
    await page.goto(f"{BASE_URL}/admin/", wait_until="domcontentloaded", timeout=30000)
    await page.wait_for_timeout(2000)
    body = await page.inner_text("body")

    if "login" not in page.url.lower() or "déconnexion" in body.lower():
        print("Session valid.\n")
        return True

    print("Session expired, logging in...")
    if not CREDS_FILE.exists():
        print("ERROR: No credentials.json found. Fill in email/password and retry.")
        return False

    creds = json.loads(CREDS_FILE.read_text())
    if not creds.get("email") or not creds.get("password"):
        print("ERROR: credentials.json is empty. Fill in email/password and retry.")
        return False

    await page.goto(f"{BASE_URL}/login", wait_until="domcontentloaded", timeout=30000)
    await page.wait_for_timeout(2000)
    await page.fill('input[name="email"]', creds["email"])
    await page.fill('input[name="password"]', creds["password"])
    await page.click('button[type="submit"]')
    await page.wait_for_timeout(3000)

    # Save new session
    state = await context.storage_state()
    STATE_FILE.write_text(json.dumps(state))
    print("Logged in, session saved.\n")
    return True


# ── Messaging ─────────────────────────────────────────────────────────

async def send_message(page: Page, url: str, ref: str) -> bool:
    print(f"  {ref} ...", end=" ", flush=True)
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=30000)
        await page.wait_for_timeout(2000)

        for sel in ['a:has-text("Contacter")', 'button:has-text("Contacter")',
                     'a:has-text("Contact")', '.listing-action:has-text("Contacter")']:
            btn = await page.query_selector(sel)
            if btn:
                await btn.click()
                await page.wait_for_timeout(2000)
                break

        textarea = None
        for sel in ['textarea[name*="message"]', 'textarea[name*="content"]',
                     'textarea[name*="body"]', 'textarea']:
            textarea = await page.query_selector(sel)
            if textarea:
                break
        if not textarea:
            all_ta = await page.query_selector_all("textarea")
            textarea = all_ta[0] if all_ta else None
        if not textarea:
            print("FAILED (no contact form)")
            return False

        await textarea.fill(MESSAGE)

        for sel in ['button[type="submit"]:has-text("Envoyer")', 'button:has-text("Envoyer")',
                     'input[type="submit"]', 'button[type="submit"]']:
            btn = await page.query_selector(sel)
            if btn:
                await btn.click()
                await page.wait_for_timeout(2000)
                print("SENT!")
                return True

        print("FAILED (no send button)")
        return False
    except Exception as e:
        print(f"ERROR: {e}")
        return False


# ── Main ──────────────────────────────────────────────────────────────

async def main():
    dry_run = "--dry-run" in sys.argv
    now = datetime.now().strftime("%Y-%m-%d %H:%M")

    print(f"{'='*60}")
    print(f" KotaLiege Auto-Messenger — {now}")
    print(f" Mode: {'DRY RUN' if dry_run else 'LIVE'}")
    print(f"{'='*60}\n")

    contacted = load_contacted()
    print(f"Previously contacted: {len(contacted)} listings\n")

    # Load session state if available
    storage_state = None
    if STATE_FILE.exists():
        storage_state = json.loads(STATE_FILE.read_text())

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            storage_state=storage_state,
            user_agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                       "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            locale="fr-FR",
        )
        page = await context.new_page()

        if not await ensure_logged_in(page, context):
            await browser.close()
            return

        # Scrape
        print("── SCRAPING ──")
        all_listings: list[Listing] = []
        seen = set()
        for lt in LISTING_TYPES:
            for l in await scrape_type(page, lt):
                if l.ref not in seen:
                    seen.add(l.ref)
                    all_listings.append(l)

        print(f"\nTotal unique listings: {len(all_listings)}")

        # Filter
        eligible = []
        for l in all_listings:
            if l.total is not None and l.total > 500:
                continue
            if not is_domicile_ok(l.domiciliation):
                continue
            if not is_available_ok(l.available):
                continue
            eligible.append(l)

        print(f"Matching criteria: {len(eligible)}")

        new_listings = [l for l in eligible if l.ref not in contacted]
        print(f"Already contacted: {len(eligible) - len(new_listings)}")
        print(f"NEW listings found: {len(new_listings)}\n")

        if not new_listings:
            print("Nothing new to message. All caught up!")
            await browser.close()
            save_contacted(contacted)
            return

        # Show new listings by tier
        tier1 = [l for l in new_listings if l.total is not None and l.total <= 400]
        tier2 = [l for l in new_listings if l.total is not None and 400 < l.total <= 450]
        tier3 = [l for l in new_listings if l.total is not None and 450 < l.total <= 500]

        for label, tier in [("≤400€", tier1), ("401-450€", tier2), ("451-500€", tier3)]:
            if tier:
                print(f"  {label} ({len(tier)}):")
                for l in tier:
                    print(f"    {l.ref} — {l.total}€ — {l.listing_type} — {l.neighborhood}")
                print()

        if dry_run:
            print("DRY RUN — no messages sent.")
            await browser.close()
            return

        # Send messages
        print(f"── MESSAGING {len(new_listings)} NEW LISTINGS ──")
        sent_count = 0
        fail_count = 0
        for l in new_listings:
            success = await send_message(page, l.url, l.ref)
            contacted[l.ref] = {
                "ref": l.ref,
                "url": l.url,
                "total": l.total,
                "neighborhood": l.neighborhood,
                "type": l.listing_type,
                "status": "sent" if success else "failed",
                "date": now,
            }
            if success:
                sent_count += 1
            else:
                fail_count += 1
            save_contacted(contacted)

        await browser.close()

    # Summary
    print(f"\n{'='*60}")
    print(f" DONE — {sent_count} new sent, {fail_count} failed")
    print(f" Total contacted overall: {sum(1 for c in contacted.values() if c['status'] == 'sent')}")
    print(f"{'='*60}")


if __name__ == "__main__":
    asyncio.run(main())
