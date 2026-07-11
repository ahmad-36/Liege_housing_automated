"""
Scrape detail pages of filtered listings to extract phone numbers and contact info.
Then send contact messages via the KotaLiege platform.
"""

import asyncio
import json
import re
from playwright.async_api import async_playwright, Page

MESSAGE_FR = """Bonjour,

Je m'appelle Ahmad et je suis étudiant. Je viens à Liège pour un semestre d'échange et je cherche une chambre de septembre à janvier.

Pourriez-vous me faire savoir si la chambre est encore disponible ? Est-il également possible d'y domicilier mon adresse officielle ?

Si vous souhaitez me contacter, voici mon numéro WhatsApp : +49 1781525635.

Cordialement,
Ahmad"""

MESSAGE_EN = """Hello,

My name is Ahmad and I am a student. I am coming to Liège for an exchange semester and am looking for a room from September to January.

Could you let me know if the room is still available? Is it also possible to register my official address there?

Also, if you would like to contact me, here is my WhatsApp number: +49 1781525635.

Best regards,
Ahmad"""


def extract_phones(text: str) -> list[str]:
    """Extract phone numbers from text."""
    patterns = [
        r'(?:\+\d{1,3}[\s.-]?)?\(?\d{2,4}\)?[\s.-]?\d{2,3}[\s.-]?\d{2,3}[\s.-]?\d{2,4}',
        r'\+\d{10,15}',
        r'0\d{1,3}[/.\s-]\d{2,3}[/.\s-]\d{2,3}[/.\s-]?\d{0,4}',
        r'0\d{8,10}',
    ]
    phones = set()
    for p in patterns:
        for match in re.finditer(p, text):
            num = match.group().strip()
            digits_only = re.sub(r'\D', '', num)
            if 8 <= len(digits_only) <= 15:
                phones.add(num)
    return list(phones)


def extract_emails(text: str) -> list[str]:
    """Extract email addresses from text."""
    return re.findall(r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}', text)


async def scrape_detail(page: Page, url: str, ref: str) -> dict:
    """Visit a listing detail page and extract contact info."""
    print(f"  {ref}: {url} ...", end=" ", flush=True)
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=30000)
        await page.wait_for_timeout(2000)

        body_text = await page.inner_text("body")
        phones = extract_phones(body_text)
        emails = extract_emails(body_text)

        # Extract owner/agency name if visible
        owner = ""
        owner_el = await page.query_selector(".listing-owner-name, .owner-name, [class*='owner'], [class*='contact-name']")
        if owner_el:
            owner = (await owner_el.inner_text()).strip()

        print(f"phones={phones}, emails={emails}")
        return {
            "ref": ref,
            "url": url,
            "phones": phones,
            "emails": emails,
            "owner": owner,
        }
    except Exception as e:
        print(f"ERROR: {e}")
        return {"ref": ref, "url": url, "phones": [], "emails": [], "owner": "", "error": str(e)}


async def main():
    with open("results.json") as f:
        data = json.load(f)

    all_listings = []
    for tier in data.values():
        all_listings.extend(tier)

    print(f"=== Scraping detail pages for {len(all_listings)} listings ===\n")

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                       "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            locale="fr-FR",
        )
        page = await context.new_page()

        contacts = []
        for listing in all_listings:
            info = await scrape_detail(page, listing["url"], listing["ref"])
            info["rent"] = listing.get("rent")
            info["charges"] = listing.get("charges")
            info["total"] = listing.get("total")
            info["neighborhood"] = listing.get("neighborhood")
            contacts.append(info)

        await browser.close()

    # Summary
    print(f"\n{'='*70}")
    print(f"{'Ref':<12} {'Total':>7} {'Neighborhood':<30} {'Phones':<25} {'Emails'}")
    print(f"{'─'*12} {'─'*7} {'─'*30} {'─'*25} {'─'*30}")
    for c in contacts:
        total_s = f"{c['total']}€" if c.get('total') else "?"
        phones_s = ", ".join(c["phones"]) if c["phones"] else "—"
        emails_s = ", ".join(c["emails"]) if c["emails"] else "—"
        print(f"{c['ref']:<12} {total_s:>7} {c.get('neighborhood',''):<30} {phones_s:<25} {emails_s}")

    with open("contacts.json", "w") as f:
        json.dump(contacts, f, indent=2, ensure_ascii=False)
    print(f"\nSaved to contacts.json")

    # Phone list
    all_phones = []
    for c in contacts:
        for ph in c["phones"]:
            all_phones.append({"ref": c["ref"], "phone": ph, "url": c["url"]})

    if all_phones:
        print(f"\n=== PHONE LIST ({len(all_phones)} numbers) ===")
        for p in all_phones:
            print(f"  {p['ref']}: {p['phone']}")
    else:
        print("\nNo phone numbers found on listing pages (they may require login to view).")


if __name__ == "__main__":
    asyncio.run(main())
