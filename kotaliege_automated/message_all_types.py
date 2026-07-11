"""
Scrape ALL housing types including résidences and nouvelles,
then message any within budget that haven't been messaged yet.
"""

import asyncio
import json
import re
from datetime import datetime
from dataclasses import dataclass, asdict
from playwright.async_api import async_playwright, Page

BASE_URL = "https://www.kotaliege.be"
STATE_FILE = "session_state.json"

LISTING_TYPES = [
    "kots",
    "studios",
    "kots-chez-l-habitant",
    "colocations",
    "residences",
    "nouvelles",
]
MAX_PAGES = 20

# All previously messaged refs (round 1 + round 2)
ALREADY_MESSAGED = set()

MESSAGE = """Bonjour,

Je m'appelle Ahmad et je suis étudiant. Je viens à Liège pour un semestre d'échange et je cherche une chambre de septembre à janvier.

Pourriez-vous me faire savoir si la chambre est encore disponible ? Est-il également possible d'y domicilier mon adresse officielle ?

Si vous souhaitez me contacter, voici mon numéro WhatsApp : +49 1781525635.

Cordialement,
Ahmad"""


def load_already_messaged():
    """Load all refs that were already messaged from previous runs."""
    refs = set()
    for fname in ["message_results.json", "message_results_round2.json"]:
        try:
            with open(fname) as f:
                data = json.load(f)
                for r in data:
                    if r.get("sent"):
                        refs.add(r["ref"])
        except FileNotFoundError:
            pass
    return refs


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


def is_available_ok(available_text: str) -> bool:
    if not available_text.strip():
        return True
    dt = parse_date_text(available_text)
    if dt is None:
        return True
    return dt >= datetime(2026, 8, 1)


def is_domicile_ok(dom_text: str) -> bool:
    lower = dom_text.strip().lower()
    if not lower:
        return True
    refused = ["refusée", "refusee", "non", "no", "refused"]
    return not any(r in lower for r in refused)


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

    total = None
    if rent is not None:
        total = rent + (charges or 0)

    return Listing(
        ref=ref, title=title, rent=rent, charges=charges, total=total,
        size=size, neighborhood=neighborhood, duration=duration,
        domiciliation=domiciliation, available=available,
        url=f"{BASE_URL}{href}" if not href.startswith("http") else href,
        listing_type=listing_type,
    )


async def scrape_all(page: Page, listing_type: str) -> list[Listing]:
    results = []
    for page_num in range(1, MAX_PAGES + 1):
        if listing_type == "residences":
            url = f"{BASE_URL}/{listing_type}/" + (f"?page={page_num}" if page_num > 1 else "")
        else:
            url = f"{BASE_URL}/{listing_type}" + (f"/{page_num}" if page_num > 1 else "")
        print(f"  [{listing_type}] page {page_num} ...", end=" ", flush=True)
        await page.goto(url, wait_until="domcontentloaded", timeout=30000)
        await page.wait_for_timeout(1500)

        articles = await page.query_selector_all("article.listing-teaser")
        if not articles:
            # Residences page may have different structure
            articles = await page.query_selector_all("[class*='listing-teaser']")
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


async def send_message(page: Page, url: str, ref: str, message: str) -> bool:
    print(f"  Messaging {ref} ...", end=" ", flush=True)
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=30000)
        await page.wait_for_timeout(2000)

        contact_selectors = [
            'a:has-text("Contacter")', 'button:has-text("Contacter")',
            'a:has-text("Contact")', 'button:has-text("Contact")',
            '.listing-action:has-text("Contacter")', 'a[href*="contact"]',
        ]
        for sel in contact_selectors:
            contact_btn = await page.query_selector(sel)
            if contact_btn:
                await contact_btn.click()
                await page.wait_for_timeout(2000)
                break

        textarea = None
        for sel in ['textarea[name*="message"]', 'textarea[name*="content"]', 'textarea[name*="body"]', 'textarea']:
            textarea = await page.query_selector(sel)
            if textarea:
                break
        if not textarea:
            all_textareas = await page.query_selector_all("textarea")
            if all_textareas:
                textarea = all_textareas[0]
        if not textarea:
            print("NO textarea found!")
            return False

        await textarea.fill(message)

        for sel in ['button[type="submit"]:has-text("Envoyer")', 'button:has-text("Envoyer")', 'input[type="submit"]', 'button[type="submit"]']:
            send_btn = await page.query_selector(sel)
            if send_btn:
                await send_btn.click()
                await page.wait_for_timeout(2000)
                print("SENT!")
                return True

        print("NO send button found!")
        return False
    except Exception as e:
        print(f"ERROR: {e}")
        return False


async def main():
    ALREADY_MESSAGED = load_already_messaged()
    print(f"=== KotaLiege — ALL TYPES, no duration filter ===")
    print(f"Already messaged from previous runs: {len(ALREADY_MESSAGED)}\n")

    with open(STATE_FILE) as f:
        state = json.load(f)

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            storage_state=state,
            user_agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                       "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            locale="fr-FR",
        )
        page = await context.new_page()

        # Verify session
        await page.goto(f"{BASE_URL}/admin/", wait_until="domcontentloaded", timeout=30000)
        if "login" in page.url.lower():
            print("Session expired! Re-logging in...")
            with open("credentials.json") as f:
                creds = json.load(f)
            await page.goto(f"{BASE_URL}/login", wait_until="domcontentloaded")
            await page.wait_for_timeout(2000)
            await page.fill('input[name="email"]', creds["email"])
            await page.fill('input[name="password"]', creds["password"])
            await page.click('button[type="submit"]')
            await page.wait_for_timeout(3000)
        print("Logged in.\n")

        # Scrape all types
        print("── SCRAPING ALL TYPES ──")
        all_listings: list[Listing] = []
        seen_refs = set()
        for lt in LISTING_TYPES:
            listings = await scrape_all(page, lt)
            for l in listings:
                if l.ref not in seen_refs:
                    seen_refs.add(l.ref)
                    all_listings.append(l)

        print(f"\nTotal unique listings: {len(all_listings)}")

        # Filter: budget ≤500€, domicile OK, availability OK
        filtered = []
        for l in all_listings:
            if l.total is not None and l.total > 500:
                continue
            if not is_domicile_ok(l.domiciliation):
                continue
            if not is_available_ok(l.available):
                continue
            filtered.append(l)

        print(f"Within budget + domicile + availability: {len(filtered)}")

        to_message = [l for l in filtered if l.ref not in ALREADY_MESSAGED]
        print(f"Already messaged: {len(filtered) - len(to_message)}")
        print(f"NEW to message: {len(to_message)}\n")

        if not to_message:
            print("Nothing new to message!")
            await browser.close()
            return

        # Show tiers
        tier1 = [l for l in to_message if l.total is not None and l.total <= 400]
        tier2 = [l for l in to_message if l.total is not None and 400 < l.total <= 450]
        tier3 = [l for l in to_message if l.total is not None and 450 < l.total <= 500]

        for label, tier in [("≤400€", tier1), ("401-450€", tier2), ("451-500€", tier3)]:
            print(f"\n  {label} ({len(tier)}):")
            for l in tier:
                print(f"    {l.ref} — {l.total}€ — {l.listing_type} — {l.duration[:40]} — {l.neighborhood}")

        # Send messages
        print(f"\n── SENDING MESSAGES TO {len(to_message)} NEW LISTINGS ──")
        results = []
        for l in to_message:
            sent = await send_message(page, l.url, l.ref, MESSAGE)
            results.append({"ref": l.ref, "url": l.url, "total": l.total, "type": l.listing_type, "sent": sent})

        await browser.close()

    sent_count = sum(1 for r in results if r["sent"])
    fail_count = sum(1 for r in results if not r["sent"])
    print(f"\n{'='*60}")
    print(f"NEW: {sent_count} sent, {fail_count} failed")
    print(f"Previously messaged: {len(ALREADY_MESSAGED)}")
    print(f"Grand total messaged: {len(ALREADY_MESSAGED) + sent_count}")
    print(f"{'='*60}")

    for r in results:
        status = "SENT" if r["sent"] else "FAILED"
        print(f"  {r['ref']} ({r.get('total','')}€, {r['type']}): {status}")

    with open("message_results_round3.json", "w") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)


if __name__ == "__main__":
    asyncio.run(main())
