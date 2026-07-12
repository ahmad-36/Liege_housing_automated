"""
Logement ULiège scraper — https://logement.uliege.be/

Scrapes the ULiège private-housing database, filters by your criteria,
and collects the landlord contact info (name / phone / email) for every
NEW listing — while remembering which listings were already processed
in processed.json, so repeated runs only surface new ones.

Unlike kotaliege.be, this site has no internal messaging system: the
"contact" step here means fetching the owner's phone/email so you can
reach out yourself (or automate it later).

Usage:
  python3 run.py              # scrape, filter, collect contacts for new listings
  python3 run.py --dry-run    # scrape + filter only, don't fetch contacts or update the log
"""

import html
import json
import re
import sys
import time
from dataclasses import dataclass, asdict
from pathlib import Path

import requests

BASE_URL = "https://logement.uliege.be"
SCRIPT_DIR = Path(__file__).parent
PROCESSED_FILE = SCRIPT_DIR / "processed.json"
CONTACTS_FILE = SCRIPT_DIR / "new_contacts.json"

REQUEST_DELAY = 1.0  # seconds between HTTP requests — be polite to the server
MAX_PAGES = 60       # safety cap

# ── Search criteria — edit these ─────────────────────────────────────
# Set a value to filter, or None to accept everything.

SEARCH = {
    # Campus checkboxes sent to the site: G=Gembloux, L=Liège centre-ville,
    # S=Sart Tilman, A=Arlon
    "campus": ["L", "S"],

    # Max total cost in € (rent + charges when charges are known)
    "max_total": 500,

    # Housing types to keep: subset of {"Appartement", "Chambre", "Maison", "Studio"}
    "types": None,                # e.g. {"Chambre", "Studio"}

    # Occupation style: "Indépendant", "Communautaire" or "Chez l'habitant"
    "occupation": None,

    # Domiciliation (official address registration): True → only listings
    # that explicitly allow it; None → keep all (including unspecified)
    "domiciliation": True,

    # Short stay allowed: True → only 'oui' or 'sur demande'; None → keep all
    "short_stay": None,

    # Max distance to Liège centre-ville in meters (listings without a
    # distance are kept)
    "max_distance_center_m": None,   # e.g. 3000
}


# ── Persistent processed log ──────────────────────────────────────────

def load_processed() -> dict[str, dict]:
    if not PROCESSED_FILE.exists():
        return {}
    data = json.loads(PROCESSED_FILE.read_text())
    return {r["ref"]: r for r in data}


def save_processed(processed: dict[str, dict]):
    records = sorted(processed.values(), key=lambda r: r["ref"])
    PROCESSED_FILE.write_text(json.dumps(records, indent=2, ensure_ascii=False))


# ── Listing model ─────────────────────────────────────────────────────

@dataclass
class Listing:
    ref: str
    title: str
    address: str
    rent: int | None
    charges: int | None
    charges_included: bool
    total: int | None
    occupation: str
    size: int | None
    capacity: int | None
    short_stay: str
    domiciliation: str
    dist_center_m: int | None
    dist_sart_tilman_m: int | None
    url: str
    contact_id: str


# ── HTML parsing (the site is old server-rendered PHP — regex is fine) ─

def strip_tags(s: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", " ", s)).replace("\xa0", " ").strip()


def parse_listing_block(block: str) -> Listing | None:
    ref_m = re.search(r"ref\s*:\s*(\d+)", block)
    if not ref_m:
        return None
    ref = ref_m.group(1)

    addr_m = re.search(r"<h3>\s*(.*?)\s*</h3>", block, re.DOTALL)
    address = strip_tags(addr_m.group(1)) if addr_m else ""

    title_m = re.search(r"<h4>(.*?)<", block, re.DOTALL)
    title = strip_tags(title_m.group(1)) if title_m else ""

    rent_m = re.search(r"Loyer\s*:\s*(\d+)\s*€", block)
    rent = int(rent_m.group(1)) if rent_m else None

    charges_included = "Charges comprises" in block
    charges_m = re.search(r"Charges\s*:\s*(\d+)\s*€", block)
    charges = int(charges_m.group(1)) if charges_m else None

    total = None
    if rent is not None:
        if charges is not None:
            total = rent + charges
        elif charges_included:
            total = rent

    text = strip_tags(block)

    occupation = ""
    for occ in ("Indépendant", "Communautaire", "Chez l'habitant"):
        if occ in text:
            occupation = occ
            break

    size_m = re.search(r"Superficie totale\s*:\s*(\d+)", text)
    size = int(size_m.group(1)) if size_m else None

    cap_m = re.search(r"Capacité d'accueil\s*:\s*(\d+)", text)
    capacity = int(cap_m.group(1)) if cap_m else None

    short_m = re.search(r"Court séjour autorisé\s*:\s*(\w+)?", text)
    short_stay = (short_m.group(1) or "") if short_m else ""

    dom_m = re.search(r"Domiciliation autorisée\s*:\s*(\w+)?", text)
    domiciliation = (dom_m.group(1) or "") if dom_m else ""

    dc_m = re.search(r"Liège centre ville\s*:\s*(\d+)\s*m", text)
    dist_center = int(dc_m.group(1)) if dc_m else None

    ds_m = re.search(r"Sart Tilman\s*:\s*(\d+)\s*m", text)
    dist_sart = int(ds_m.group(1)) if ds_m else None

    contact_m = re.search(r"ContactsLogement\.php\?p=(\d+)", block)
    contact_id = contact_m.group(1) if contact_m else ""

    return Listing(
        ref=ref, title=title, address=address, rent=rent, charges=charges,
        charges_included=charges_included, total=total, occupation=occupation,
        size=size, capacity=capacity, short_stay=short_stay,
        domiciliation=domiciliation, dist_center_m=dist_center,
        dist_sart_tilman_m=dist_sart,
        url=f"{BASE_URL}/?t=d&l={ref}", contact_id=contact_id,
    )


def parse_results_page(page_html: str) -> list[Listing]:
    listings = []
    blocks = page_html.split('<div class="bUnLogement">')[1:]
    for block in blocks:
        listing = parse_listing_block(block)
        if listing:
            listings.append(listing)
    return listings


# ── Scraping ──────────────────────────────────────────────────────────

def scrape_all(session: requests.Session) -> list[Listing]:
    params = {
        "reTL": "-1", "reCH": "-1", "reLO": "-1", "reCS": "-1",
        "reDO": "-1", "reNP": "-1", "t": "r", "fiOCC": "T",
        "cmd": "Rechercher",
    }
    for c in SEARCH["campus"]:
        params[f"reCA[{c}]"] = "on"

    print("  page 1 ...", end=" ", flush=True)
    resp = session.get(BASE_URL + "/", params=params, timeout=30)
    resp.raise_for_status()
    listings = parse_results_page(resp.text)
    print(f"{len(listings)} listings")

    # Total page count from the pagination links
    nums = [int(n) for n in re.findall(r"NumP=(\d+)", resp.text)]
    last_page = min(max(nums) if nums else 1, MAX_PAGES)

    page_params = {"t": "p", "fiOCC": "T", "tr1": "A"}
    for c in SEARCH["campus"]:
        page_params[f"reCA[{c}]"] = "on"

    for num in range(2, last_page + 1):
        time.sleep(REQUEST_DELAY)
        print(f"  page {num} ...", end=" ", flush=True)
        resp = session.get(BASE_URL + "/", params={**page_params, "NumP": num},
                           timeout=30)
        resp.raise_for_status()
        page_listings = parse_results_page(resp.text)
        print(f"{len(page_listings)} listings")
        if not page_listings:
            break
        listings.extend(page_listings)

    # De-duplicate by ref
    unique = {}
    for l in listings:
        unique.setdefault(l.ref, l)
    return list(unique.values())


# ── Filtering ─────────────────────────────────────────────────────────

def matches(l: Listing) -> bool:
    s = SEARCH
    if s["max_total"] is not None and l.total is not None and l.total > s["max_total"]:
        return False
    if s["types"] is not None and not any(t.lower() in l.title.lower() for t in s["types"]):
        return False
    if s["occupation"] is not None and l.occupation != s["occupation"]:
        return False
    if s["domiciliation"] is True and l.domiciliation != "oui":
        return False
    if s["short_stay"] is True and l.short_stay not in ("oui", "sur"):
        return False
    if (s["max_distance_center_m"] is not None and l.dist_center_m is not None
            and l.dist_center_m > s["max_distance_center_m"]):
        return False
    return True


# ── Contact info ──────────────────────────────────────────────────────

def fetch_contact(session: requests.Session, contact_id: str) -> dict:
    resp = session.get(f"{BASE_URL}/ContactsLogement.php", params={"p": contact_id},
                       timeout=30)
    resp.raise_for_status()
    text = resp.text

    name_m = re.search(r'class="NomPrenom">(.*?)<', text, re.DOTALL)
    name = strip_tags(name_m.group(1)) if name_m else ""

    emails = list(dict.fromkeys(re.findall(r'mailto:([^"\'>]+)', text)))
    phones = list(dict.fromkeys(
        p.strip() for p in re.findall(r"</span>\s*&nbsp;([+0-9 ()./-]{8,})", text)
    ))

    return {"name": name, "phones": phones, "emails": emails}


# ── Main ──────────────────────────────────────────────────────────────

def main():
    dry_run = "--dry-run" in sys.argv
    now = time.strftime("%Y-%m-%d %H:%M")

    print("=" * 60)
    print(f" Logement ULiège Scraper — {now}")
    print(f" Mode: {'DRY RUN' if dry_run else 'LIVE'}")
    print("=" * 60 + "\n")

    processed = load_processed()
    print(f"Previously processed: {len(processed)} listings\n")

    session = requests.Session()
    session.headers["User-Agent"] = (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )

    print("── SCRAPING ──")
    all_listings = scrape_all(session)
    print(f"\nTotal unique listings: {len(all_listings)}")

    eligible = [l for l in all_listings if matches(l)]
    print(f"Matching criteria: {len(eligible)}")

    new_listings = [l for l in eligible if l.ref not in processed]
    print(f"Already processed: {len(eligible) - len(new_listings)}")
    print(f"NEW listings found: {len(new_listings)}\n")

    if not new_listings:
        print("Nothing new. All caught up!")
        return

    tiers = [
        ("≤400€", [l for l in new_listings if l.total is not None and l.total <= 400]),
        ("401-450€", [l for l in new_listings if l.total is not None and 400 < l.total <= 450]),
        ("451-500€", [l for l in new_listings if l.total is not None and 450 < l.total <= 500]),
        ("unknown total", [l for l in new_listings if l.total is None]),
    ]
    for label, tier in tiers:
        if not tier:
            continue
        print(f"  {label} ({len(tier)}):")
        for l in sorted(tier, key=lambda x: x.total or 0):
            total_s = f"{l.total}€" if l.total is not None else "?€"
            print(f"    {l.ref} — {total_s} — {l.title} — {l.occupation}"
                  f" — dom: {l.domiciliation or '?'} — {l.address}")
        print()

    if dry_run:
        print("\nDRY RUN — contacts not fetched, log not updated.")
        return

    print(f"\n── FETCHING CONTACTS FOR {len(new_listings)} NEW LISTINGS ──")
    new_contacts = []
    for l in new_listings:
        time.sleep(REQUEST_DELAY)
        print(f"  {l.ref} ...", end=" ", flush=True)
        try:
            contact = fetch_contact(session, l.contact_id) if l.contact_id else {}
            status = "collected" if contact.get("emails") or contact.get("phones") else "no_contact_info"
            print(contact.get("name") or "(no info)")
        except requests.RequestException as e:
            contact = {}
            status = "error"
            print(f"ERROR: {e}")

        record = {
            "ref": l.ref,
            "url": l.url,
            "total": l.total,
            "title": l.title,
            "address": l.address,
            "contact": contact,
            "status": status,
            "date": now,
        }
        processed[l.ref] = record
        new_contacts.append(record)
        save_processed(processed)

    CONTACTS_FILE.write_text(json.dumps(new_contacts, indent=2, ensure_ascii=False))

    print("\n" + "=" * 60)
    print(f" DONE — {len(new_contacts)} new contacts collected")
    print(f" Saved to {CONTACTS_FILE.name}; full log in {PROCESSED_FILE.name}")
    print("=" * 60)


if __name__ == "__main__":
    main()
