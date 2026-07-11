"""
1. Log in to KotaLiege
2. Save session state
3. Scrape phone numbers from detail pages (now visible after login)
4. Send contact messages to all filtered listing owners
"""

import asyncio
import json
import re
from playwright.async_api import async_playwright, Page, BrowserContext

BASE_URL = "https://www.kotaliege.be"
STATE_FILE = "session_state.json"

MESSAGE = """Bonjour,

Je m'appelle Ahmad et je suis étudiant. Je viens à Liège pour un semestre d'échange et je cherche une chambre de septembre à janvier.

Pourriez-vous me faire savoir si la chambre est encore disponible ? Est-il également possible d'y domicilier mon adresse officielle ?

Si vous souhaitez me contacter, voici mon numéro WhatsApp : +49 1781525635.

Cordialement,
Ahmad"""


def extract_phones(text: str) -> list[str]:
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
            if 8 <= len(digits_only) <= 15 and num != "+49 1781525635":
                phones.add(num)
    return list(phones)


def extract_emails(text: str) -> list[str]:
    emails = re.findall(r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}', text)
    return [e for e in emails if e != "ahmadabdullahbhatti@gmail.com"]


async def login(page: Page, email: str, password: str) -> bool:
    """Log in to KotaLiege."""
    print("Navigating to login page...")
    await page.goto(f"{BASE_URL}/login", wait_until="domcontentloaded", timeout=30000)
    await page.wait_for_timeout(2000)

    # Debug: dump form structure
    form_html = await page.evaluate("""() => {
        const forms = document.querySelectorAll('form');
        return Array.from(forms).map(f => f.outerHTML.substring(0, 500)).join('\\n---\\n');
    }""")
    print(f"Found forms:\n{form_html}\n")

    # Try common selectors for email/password fields
    email_selectors = [
        'input[name="email"]', 'input[name="_username"]', 'input[name="username"]',
        'input[type="email"]', 'input[name="login"]', '#email', '#username',
        'input[name="_email"]',
    ]
    pass_selectors = [
        'input[name="password"]', 'input[name="_password"]',
        'input[type="password"]', '#password',
    ]

    email_input = None
    for sel in email_selectors:
        email_input = await page.query_selector(sel)
        if email_input:
            print(f"  Email field: {sel}")
            break

    pass_input = None
    for sel in pass_selectors:
        pass_input = await page.query_selector(sel)
        if pass_input:
            print(f"  Password field: {sel}")
            break

    if not email_input or not pass_input:
        print("ERROR: Could not find login form fields!")
        # Dump all inputs for debugging
        inputs = await page.evaluate("""() => {
            return Array.from(document.querySelectorAll('input')).map(i =>
                `<input name="${i.name}" type="${i.type}" id="${i.id}" class="${i.className}">`
            );
        }""")
        print("All inputs on page:", inputs)
        return False

    await email_input.fill(email)
    await pass_input.fill(password)

    # Find and click submit button
    submit_selectors = [
        'button[type="submit"]', 'input[type="submit"]',
        'button:has-text("Login")', 'button:has-text("Connexion")',
        'button:has-text("Se connecter")',
    ]
    for sel in submit_selectors:
        submit = await page.query_selector(sel)
        if submit:
            print(f"  Submit button: {sel}")
            await submit.click()
            break

    await page.wait_for_timeout(3000)

    # Check if login succeeded
    current_url = page.url
    body_text = await page.inner_text("body")
    if "déconnexion" in body_text.lower() or "logout" in body_text.lower() or "mon compte" in body_text.lower():
        print("LOGIN SUCCESS!")
        return True
    elif "/login" in current_url:
        print(f"LOGIN FAILED — still on login page. URL: {current_url}")
        error_el = await page.query_selector(".alert-danger, .error, .flash-error")
        if error_el:
            print(f"  Error: {await error_el.inner_text()}")
        return False
    else:
        print(f"Login status unclear. Current URL: {current_url}")
        return True


async def scrape_detail_logged_in(page: Page, url: str, ref: str) -> dict:
    """Visit detail page while logged in to get phone/email."""
    print(f"  {ref} ...", end=" ", flush=True)
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=30000)
        await page.wait_for_timeout(1500)

        body_text = await page.inner_text("body")
        phones = extract_phones(body_text)
        emails = extract_emails(body_text)

        print(f"phones={phones}, emails={emails}")
        return {"ref": ref, "url": url, "phones": phones, "emails": emails}
    except Exception as e:
        print(f"ERROR: {e}")
        return {"ref": ref, "url": url, "phones": [], "emails": [], "error": str(e)}


async def send_message(page: Page, url: str, ref: str, message: str) -> bool:
    """Send a contact message on a listing's detail page."""
    print(f"  Messaging {ref} ...", end=" ", flush=True)
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=30000)
        await page.wait_for_timeout(2000)

        # Look for contact button
        contact_selectors = [
            'a:has-text("Contacter")', 'button:has-text("Contacter")',
            'a:has-text("Contact")', 'button:has-text("Contact")',
            '.listing-action:has-text("Contacter")',
            'a[href*="contact"]',
        ]
        contact_btn = None
        for sel in contact_selectors:
            contact_btn = await page.query_selector(sel)
            if contact_btn:
                await contact_btn.click()
                await page.wait_for_timeout(2000)
                break

        # Look for message textarea
        textarea_selectors = [
            'textarea[name*="message"]', 'textarea[name*="content"]',
            'textarea[name*="body"]', 'textarea',
        ]
        textarea = None
        for sel in textarea_selectors:
            textarea = await page.query_selector(sel)
            if textarea:
                break

        if not textarea:
            # Maybe there's a modal or separate page
            all_textareas = await page.query_selector_all("textarea")
            if all_textareas:
                textarea = all_textareas[0]

        if not textarea:
            print("NO textarea found!")
            # Debug
            page_html = await page.evaluate("() => document.body.innerHTML.substring(0, 3000)")
            print(f"  Page snippet: {page_html[:500]}")
            return False

        await textarea.fill(message)

        # Find send button
        send_selectors = [
            'button[type="submit"]:has-text("Envoyer")',
            'button:has-text("Envoyer")',
            'input[type="submit"]',
            'button[type="submit"]',
        ]
        for sel in send_selectors:
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
    # Load credentials
    with open("credentials.json") as f:
        creds = json.load(f)

    # Load filtered listings
    with open("results.json") as f:
        data = json.load(f)

    all_listings = []
    for tier_name, tier in data.items():
        for l in tier:
            l["tier"] = tier_name
            all_listings.append(l)

    print(f"=== KotaLiege Login + Message Sender ===")
    print(f"Listings to process: {len(all_listings)}\n")

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                       "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            locale="fr-FR",
        )
        page = await context.new_page()

        # Step 1: Login
        print("── STEP 1: LOGIN ──")
        success = await login(page, creds["email"], creds["password"])
        if not success:
            print("Cannot proceed without login. Exiting.")
            await browser.close()
            return

        # Save session state
        state = await context.storage_state()
        with open(STATE_FILE, "w") as f:
            json.dump(state, f)
        print(f"Session saved to {STATE_FILE}\n")

        # Step 2: Scrape phone numbers
        print("── STEP 2: SCRAPING CONTACT INFO ──")
        contacts = []
        for l in all_listings:
            info = await scrape_detail_logged_in(page, l["url"], l["ref"])
            info["total"] = l.get("total")
            info["neighborhood"] = l.get("neighborhood")
            info["tier"] = l.get("tier")
            contacts.append(info)

        # Phone number summary
        print(f"\n{'='*70}")
        print("PHONE NUMBER LIST:")
        print(f"{'='*70}")
        any_phones = False
        for c in contacts:
            if c["phones"]:
                any_phones = True
                for ph in c["phones"]:
                    print(f"  {c['ref']} ({c.get('total','')}€, {c.get('neighborhood','')}): {ph}")
        if not any_phones:
            print("  No phone numbers found (site may not show them).")

        with open("contacts.json", "w") as f:
            json.dump(contacts, f, indent=2, ensure_ascii=False)

        # Step 3: Send messages
        print(f"\n── STEP 3: SENDING MESSAGES ──")
        results = []
        for l in all_listings:
            sent = await send_message(page, l["url"], l["ref"], MESSAGE)
            results.append({"ref": l["ref"], "url": l["url"], "sent": sent})

        await browser.close()

    # Final summary
    print(f"\n{'='*70}")
    print("MESSAGE SENDING SUMMARY:")
    print(f"{'='*70}")
    sent_count = sum(1 for r in results if r["sent"])
    fail_count = sum(1 for r in results if not r["sent"])
    print(f"  Sent: {sent_count}")
    print(f"  Failed: {fail_count}")
    for r in results:
        status = "SENT" if r["sent"] else "FAILED"
        print(f"  {r['ref']}: {status}")

    with open("message_results.json", "w") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)
    print(f"\nResults saved to message_results.json")


if __name__ == "__main__":
    asyncio.run(main())
