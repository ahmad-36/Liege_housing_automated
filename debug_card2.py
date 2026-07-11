"""Debug: find the actual listing card containers with price/size info."""

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

        # Get the full page HTML structure around listings
        # Look for common listing container patterns
        for selector in [
            ".listing-teaser", ".listing-card", ".listing", ".result",
            "[class*='listing']", "[class*='teaser']", "[class*='card']",
            "[class*='result']", ".item", ".property",
        ]:
            els = await page.query_selector_all(selector)
            if els:
                print(f"Selector '{selector}': {len(els)} elements")

        print("\n=== Trying broader selectors ===")
        # Get all classes that contain 'listing' or 'teaser'
        classes = await page.evaluate("""() => {
            const all = document.querySelectorAll('*');
            const found = new Set();
            for (const el of all) {
                for (const cls of el.classList) {
                    if (cls.includes('listing') || cls.includes('teaser') || cls.includes('kot')) {
                        found.add(cls + ' -> ' + el.tagName);
                    }
                }
            }
            return [...found].sort();
        }""")
        for c in classes:
            print(f"  {c}")

        # Now get the full card container
        print("\n=== First listing-teaser content ===")
        teasers = await page.query_selector_all("[class*='listing-teaser']")
        if teasers:
            for i, t in enumerate(teasers[:2]):
                cls = await t.get_attribute("class")
                text = (await t.inner_text()).strip()
                print(f"\nTeaser {i+1} (class={cls}):")
                print(f"TEXT: {text[:500]}")
                print(f"---")

        # Alternative: dump the first chunk of the results area
        print("\n=== Page text around listings ===")
        body_text = await page.inner_text("body")
        # Find where listing info starts
        idx = body_text.find("m²")
        if idx > 0:
            print(body_text[max(0,idx-200):idx+200])

        await browser.close()

asyncio.run(main())
