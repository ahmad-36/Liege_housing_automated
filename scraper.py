"""
KotaLiege scraper — scrapes all listings and filters by criteria:
  - Total cost (rent + charges) in 3 tiers: ≤400€, 400-450€, 450-500€
  - Duration: 5-6 months
  - Domicile: exclude only if explicitly refused
  - Available: August or September 2026+, or unspecified
"""

import asyncio
import json
import re
from dataclasses import dataclass, asdict, field
from datetime import datetime
from playwright.async_api import async_playwright, Page

BASE_URL = "https://www.kotaliege.be"

LISTING_TYPES = ["kots", "studios", "kots-chez-l-habitant", "colocations"]
MAX_PAGES = 20  # safety cap per type


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


def parse_date_text(text: str) -> datetime | None:
    """Try to parse an availability date from text like '1 sept.', '15 août'."""
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
    """Available from August 2026 or later, or unspecified → OK."""
    if not available_text.strip():
        return True
    dt = parse_date_text(available_text)
    if dt is None:
        return True
    return dt >= datetime(2026, 8, 1)


def is_domicile_ok(dom_text: str) -> bool:
    """Exclude only if explicitly refused."""
    lower = dom_text.strip().lower()
    if not lower:
        return True
    refused = ["refusée", "refusee", "non", "no", "refused"]
    return not any(r in lower for r in refused)


def is_duration_ok(dur_text: str) -> bool:
    """Accept if 5-6 month (or shorter) is listed as an option, or unspecified."""
    lower = dur_text.strip().lower()
    if not lower:
        return True
    # Multi-value durations: "12 mois, 10 mois, 5-6 mois, au mois"
    # Check if ANY listed option is ≤ 6 months
    all_months = re.findall(r"(\d+)\s*(?:mois|month)", lower)
    for m in all_months:
        if int(m) <= 6:
            return True
    # "5-6 mois" pattern
    if re.search(r"5\s*-\s*6", lower):
        return True
    short_terms = ["semaine", "week", "jour", "day", "mensuel", "monthly", "au mois",
                   "vacances", "mois par mois"]
    if any(t in lower for t in short_terms):
        return True
    return False


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
    available = ""

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
        url = f"{BASE_URL}/{listing_type}" + (f"/{page_num}" if page_num > 1 else "")
        print(f"  [{listing_type}] page {page_num} ...", end=" ", flush=True)
        await page.goto(url, wait_until="domcontentloaded", timeout=30000)
        await page.wait_for_timeout(1500)

        articles = await page.query_selector_all("article.listing-teaser")
        if not articles:
            print("no results, done.")
            break

        count = 0
        for article in articles:
            listing = await extract_listing(article, listing_type)
            if listing:
                results.append(listing)
                count += 1
        print(f"{count} listings")
    return results


async def main():
    print("=== KotaLiege Full Scraper ===")
    print("Criteria: total ≤500€, 5-6 months, domicile OK, available Aug/Sep 2026+\n")

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                       "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            locale="fr-FR",
        )
        page = await context.new_page()

        all_listings: list[Listing] = []
        for lt in LISTING_TYPES:
            print(f"\n── {lt} ──")
            all_listings.extend(await scrape_all(page, lt))

        await browser.close()

    print(f"\n{'='*60}")
    print(f"Total scraped: {len(all_listings)}")

    # Apply filters
    filtered = []
    for l in all_listings:
        if l.total is not None and l.total > 500:
            continue
        if not is_domicile_ok(l.domiciliation):
            continue
        if not is_duration_ok(l.duration):
            continue
        if not is_available_ok(l.available):
            continue
        filtered.append(l)

    print(f"After filters: {len(filtered)}")

    # Categorize into 3 tiers
    tier1 = [l for l in filtered if l.total is not None and l.total <= 400]
    tier2 = [l for l in filtered if l.total is not None and 400 < l.total <= 450]
    tier3 = [l for l in filtered if l.total is not None and 450 < l.total <= 500]
    unknown = [l for l in filtered if l.total is None]

    def print_table(listings: list[Listing], label: str):
        print(f"\n{'='*90}")
        print(f" {label} ({len(listings)} listings)")
        print(f"{'='*90}")
        if not listings:
            print("  (none)")
            return
        print(f"  {'#':<4} {'Ref':<10} {'Type':<8} {'Rent':>6} {'Chrg':>6} {'Total':>7} {'Size':>6} {'Duration':<10} {'Domicile':<16} {'Avail':<12} {'Neighborhood'}")
        print(f"  {'─'*4} {'─'*10} {'─'*8} {'─'*6} {'─'*6} {'─'*7} {'─'*6} {'─'*10} {'─'*16} {'─'*12} {'─'*25}")
        for i, l in enumerate(listings, 1):
            rent_s = f"{l.rent}€" if l.rent else "?"
            chrg_s = f"{l.charges}€" if l.charges else "—"
            total_s = f"{l.total}€" if l.total else "?"
            size_s = f"{l.size}m²" if l.size else "?"
            dom_s = l.domiciliation if l.domiciliation else "—"
            avail_s = l.available if l.available else "—"
            dur_s = l.duration if l.duration else "—"
            lt = l.listing_type[:8]
            print(f"  {i:<4} {l.ref:<10} {lt:<8} {rent_s:>6} {chrg_s:>6} {total_s:>7} {size_s:>6} {dur_s:<10} {dom_s:<16} {avail_s:<12} {l.neighborhood}")

    print_table(tier1, "TIER 1: Total ≤ 400€")
    print_table(tier2, "TIER 2: Total 401–450€")
    print_table(tier3, "TIER 3: Total 451–500€")
    if unknown:
        print_table(unknown, "UNKNOWN TOTAL (charges not listed)")

    # Print URLs for easy access
    for label, tier in [("TIER 1", tier1), ("TIER 2", tier2), ("TIER 3", tier3)]:
        if tier:
            print(f"\n{label} URLs:")
            for l in tier:
                print(f"  {l.ref}: {l.url}")

    # Save full results
    all_results = {
        "tier1_up_to_400": [asdict(l) for l in tier1],
        "tier2_401_to_450": [asdict(l) for l in tier2],
        "tier3_451_to_500": [asdict(l) for l in tier3],
        "unknown_total": [asdict(l) for l in unknown],
    }
    with open("results.json", "w") as f:
        json.dump(all_results, f, indent=2, ensure_ascii=False)
    print(f"\nResults saved to results.json")


if __name__ == "__main__":
    asyncio.run(main())
