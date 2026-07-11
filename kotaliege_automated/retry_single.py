"""Retry messaging a single listing with detailed debugging."""

import asyncio
import json
from playwright.async_api import async_playwright

BASE_URL = "https://www.kotaliege.be"
STATE_FILE = "session_state.json"
REF = "KL 16842"
URL = f"{BASE_URL}/KL/16842"

MESSAGE = """Bonjour,

Je m'appelle Ahmad et je suis étudiant. Je viens à Liège pour un semestre d'échange et je cherche une chambre de septembre à janvier.

Pourriez-vous me faire savoir si la chambre est encore disponible ? Est-il également possible d'y domicilier mon adresse officielle ?

Si vous souhaitez me contacter, voici mon numéro WhatsApp : +49 1781525635.

Cordialement,
Ahmad"""


async def main():
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

        print(f"Loading {URL} ...")
        await page.goto(URL, wait_until="domcontentloaded", timeout=30000)
        await page.wait_for_timeout(3000)

        # Check what the page shows
        body_text = await page.inner_text("body")
        print(f"\nPage URL: {page.url}")

        # Look for all links/buttons with "contact" in them
        print("\n── All contact-related elements ──")
        elements = await page.evaluate("""() => {
            const results = [];
            const all = document.querySelectorAll('a, button, input[type="submit"]');
            for (const el of all) {
                const text = el.innerText || el.value || '';
                const href = el.getAttribute('href') || '';
                const cls = el.className || '';
                if (text.toLowerCase().includes('contact') ||
                    href.toLowerCase().includes('contact') ||
                    cls.toLowerCase().includes('contact')) {
                    results.push({
                        tag: el.tagName,
                        text: text.trim().substring(0, 100),
                        href: href,
                        cls: cls,
                        id: el.id,
                    });
                }
            }
            return results;
        }""")
        for el in elements:
            print(f"  <{el['tag']} class='{el['cls']}' href='{el['href']}'> {el['text']}")

        # Look for all textareas
        print("\n── All textareas ──")
        textareas = await page.query_selector_all("textarea")
        print(f"  Found {len(textareas)} textarea(s)")
        for i, ta in enumerate(textareas):
            name = await ta.get_attribute("name")
            placeholder = await ta.get_attribute("placeholder")
            print(f"  [{i}] name={name}, placeholder={placeholder}")

        # Try clicking the contact button
        print("\n── Trying to click contact button ──")
        for sel in [
            'a:has-text("Contacter le propriétaire")',
            'a:has-text("Contacter")',
            'button:has-text("Contacter")',
            'a.listing-action',
            '.listing-actions a',
            'a.listing-card-action',
        ]:
            btn = await page.query_selector(sel)
            if btn:
                href = await btn.get_attribute("href")
                text = (await btn.inner_text()).strip()
                print(f"  Found: {sel} → text='{text}', href='{href}'")

                if href and href.startswith("/"):
                    print(f"  Navigating to {BASE_URL}{href}")
                    await page.goto(f"{BASE_URL}{href}", wait_until="domcontentloaded", timeout=30000)
                    await page.wait_for_timeout(3000)
                else:
                    await btn.click()
                    await page.wait_for_timeout(3000)

                print(f"  After click, URL: {page.url}")

                # Check for textarea now
                textareas = await page.query_selector_all("textarea")
                print(f"  Textareas after click: {len(textareas)}")
                if textareas:
                    await textareas[0].fill(MESSAGE)
                    send_btn = await page.query_selector('button[type="submit"]')
                    if send_btn:
                        await send_btn.click()
                        await page.wait_for_timeout(3000)
                        print("  MESSAGE SENT!")
                    else:
                        print("  No submit button found after filling textarea")
                break

        # If still no luck, check if there's a specific contact page pattern
        print("\n── Checking contact URL patterns ──")
        for pattern in [f"/KL/16842/contact", f"/contact/16842", f"/message/16842"]:
            full_url = f"{BASE_URL}{pattern}"
            resp = await page.goto(full_url, wait_until="domcontentloaded", timeout=15000)
            status = resp.status if resp else "?"
            print(f"  {full_url} → {status}")
            if status == 200:
                textareas = await page.query_selector_all("textarea")
                if textareas:
                    print(f"  Found textarea on contact page!")
                    await textareas[0].fill(MESSAGE)
                    send_btn = await page.query_selector('button[type="submit"]')
                    if send_btn:
                        await send_btn.click()
                        await page.wait_for_timeout(3000)
                        print("  MESSAGE SENT!")
                        break

        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
