"""Get the full phone number from KL 16842."""

import asyncio
import json
from playwright.async_api import async_playwright

async def main():
    with open("session_state.json") as f:
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

        await page.goto("https://www.kotaliege.be/KL/16842", wait_until="domcontentloaded", timeout=30000)
        await page.wait_for_timeout(3000)

        # Click the phone button
        phone_btn = await page.query_selector('button:has-text("Contacter par téléphone")')
        if phone_btn:
            await phone_btn.click()
            await page.wait_for_timeout(2000)

        # Get the full area around the phone button after click
        # The phone number might be revealed in a sibling or replaced element
        phone_area = await page.evaluate("""() => {
            const btn = document.querySelector('button.btn-primary');
            if (!btn) return 'no button';
            const parent = btn.parentElement;
            return parent ? parent.innerHTML : btn.outerHTML;
        }""")
        print(f"Phone area HTML:\n{phone_area}\n")

        # Also look for any tel: links
        tel_links = await page.query_selector_all('a[href^="tel:"]')
        for t in tel_links:
            href = await t.get_attribute("href")
            text = (await t.inner_text()).strip()
            print(f"Tel link: {href} — {text}")

        # Look for phone class elements
        phone_els = await page.evaluate("""() => {
            const results = [];
            const all = document.querySelectorAll('*');
            for (const el of all) {
                const text = el.innerText || '';
                const cls = el.className || '';
                if ((typeof cls === 'string' && (cls.includes('phone') || cls.includes('tel'))) ||
                    /0[1-9][\d\s/.()-]{7,}/.test(text.trim()) ||
                    /\+\d{2}[\d\s/.()-]{7,}/.test(text.trim())) {
                    if (text.trim().length < 50) {
                        results.push({
                            tag: el.tagName,
                            cls: typeof cls === 'string' ? cls : '',
                            text: text.trim(),
                        });
                    }
                }
            }
            return results;
        }""")
        for el in phone_els:
            print(f"  <{el['tag']} class='{el['cls']}'> {el['text']}")

        await browser.close()

if __name__ == "__main__":
    asyncio.run(main())
