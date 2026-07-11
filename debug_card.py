"""Debug: dump the raw text and HTML of the first few listing cards."""

import asyncio
from playwright.async_api import async_playwright

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(
            user_agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                       "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            locale="fr-FR",
        )
        await page.goto("https://www.kotaliege.be/kots", wait_until="domcontentloaded")
        await page.wait_for_timeout(3000)

        # Try different selectors to find the listing containers
        cards = await page.query_selector_all("a[href*='/KL/']")
        print(f"Found {len(cards)} card links\n")

        for i, card in enumerate(cards[:3]):
            href = await card.get_attribute("href")
            text = await card.inner_text()
            html = await card.inner_html()
            print(f"=== Card {i+1} (href={href}) ===")
            print(f"TEXT:\n{text}\n")
            print(f"HTML (first 1000 chars):\n{html[:1000]}\n")
            print("-" * 60)

        # Also look at the parent containers of those links
        print("\n\n=== PARENT ANALYSIS ===")
        first_card = cards[0]
        parent = await first_card.evaluate_handle("el => el.closest('div, article, li, section')")
        if parent:
            parent_html = await parent.inner_html()
            parent_text = await parent.inner_text()
            print(f"PARENT TEXT:\n{parent_text}\n")
            print(f"PARENT HTML (first 2000):\n{parent_html[:2000]}\n")

        # Try to find price/size elements directly on the page
        print("\n=== LOOKING FOR PRICE ELEMENTS ===")
        price_els = await page.query_selector_all("*:has-text('€')")
        for el in price_els[:10]:
            tag = await el.evaluate("el => el.tagName")
            cls = await el.get_attribute("class") or ""
            text = (await el.inner_text()).strip()
            if len(text) < 100 and "€" in text:
                print(f"  <{tag} class='{cls}'> {text}")

        await browser.close()

asyncio.run(main())
