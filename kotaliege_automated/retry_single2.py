"""Retry KL 16842 — try phone button and look for hidden/dynamic forms."""

import asyncio
import json
from playwright.async_api import async_playwright

BASE_URL = "https://www.kotaliege.be"
STATE_FILE = "session_state.json"

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

        await page.goto(f"{BASE_URL}/KL/16842", wait_until="domcontentloaded", timeout=30000)
        await page.wait_for_timeout(3000)

        # 1. Try the phone button
        print("── Trying 'Contacter par téléphone' button ──")
        phone_btn = await page.query_selector('button:has-text("Contacter par téléphone")')
        if phone_btn:
            await phone_btn.click()
            await page.wait_for_timeout(2000)
            # Check if a modal or phone number appeared
            body_after = await page.inner_text("body")
            # Look for phone patterns
            import re
            phones = re.findall(r'0\d[\d\s/.-]{7,14}', body_after)
            phones += re.findall(r'\+\d[\d\s/.-]{8,15}', body_after)
            if phones:
                print(f"  Phone numbers found: {phones}")
            else:
                print("  No phone number revealed")

            # Check for modals
            modals = await page.query_selector_all('.modal.show, .modal.in, [class*="modal"][style*="display: block"]')
            print(f"  Visible modals: {len(modals)}")
            for m in modals:
                text = (await m.inner_text()).strip()
                print(f"  Modal text: {text[:300]}")

        # 2. Look at the practical-info-availability section (where contact scrolls to)
        print("\n── practical-info-availability section ──")
        section = await page.query_selector('#practical-info-availability')
        if section:
            html = await section.inner_html()
            text = await section.inner_text()
            print(f"  Text: {text[:500]}")
            # Look for forms in this section
            forms = await section.query_selector_all('form')
            print(f"  Forms in section: {len(forms)}")
            for f in forms:
                action = await f.get_attribute("action")
                method = await f.get_attribute("method")
                fhtml = await f.inner_html()
                print(f"  Form action={action} method={method}")
                print(f"  Form HTML: {fhtml[:500]}")

        # 3. Look for ALL forms on the page
        print("\n── All forms on page ──")
        forms = await page.query_selector_all('form')
        for i, f in enumerate(forms):
            action = await f.get_attribute("action") or ""
            cls = await f.get_attribute("class") or ""
            ftext = (await f.inner_text()).strip()
            if "message" in action.lower() or "contact" in action.lower() or "message" in cls.lower():
                print(f"  Form[{i}]: action={action}, class={cls}")
                print(f"  Text: {ftext[:300]}")
                fhtml = await f.inner_html()
                print(f"  HTML: {fhtml[:500]}")

        # 4. Check all forms for textareas
        print("\n── Forms with textareas ──")
        for i, f in enumerate(forms):
            tas = await f.query_selector_all("textarea")
            if tas:
                action = await f.get_attribute("action")
                print(f"  Form[{i}] action={action} has {len(tas)} textarea(s)")

        # 5. Look for the message form that other listings use
        print("\n── Looking for message form elements ──")
        all_elements = await page.evaluate("""() => {
            const results = [];
            const all = document.querySelectorAll('*');
            for (const el of all) {
                const cls = el.className || '';
                const id = el.id || '';
                if ((typeof cls === 'string' && (cls.includes('message') || cls.includes('contact-form'))) ||
                    id.includes('message') || id.includes('contact-form')) {
                    results.push({
                        tag: el.tagName,
                        cls: typeof cls === 'string' ? cls : '',
                        id: id,
                        visible: el.offsetParent !== null,
                        text: el.innerText ? el.innerText.substring(0, 100) : '',
                    });
                }
            }
            return results;
        }""")
        for el in all_elements:
            print(f"  <{el['tag']} id='{el['id']}' class='{el['cls']}'> visible={el['visible']} text='{el['text']}'")

        # 6. Try the contact overview page
        print("\n── Trying /admin/account/contact-overview ──")
        await page.goto(f"{BASE_URL}/admin/account/contact-overview", wait_until="domcontentloaded", timeout=30000)
        await page.wait_for_timeout(2000)
        print(f"  URL: {page.url}")
        body = await page.inner_text("body")
        print(f"  Content: {body[:500]}")

        await browser.close()

if __name__ == "__main__":
    asyncio.run(main())
