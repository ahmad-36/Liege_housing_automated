# Logement ULiège Scraper

Scrapes the [ULiège private-housing database](https://logement.uliege.be/), filters listings by your criteria, and collects the landlord's contact details (name, phone, email) for every **new** listing. A persistent log (`processed.json`) remembers everything already handled, so you can run it daily and only see what's new.

Unlike kotaliege.be, this site has no internal messaging system — the contact info is public, so "processing" a listing means fetching the owner's phone/email for you to reach out.

No login required, and no browser automation either: the site is plain server-rendered HTML, so everything works over simple HTTP requests.

## Setup

```bash
pip install -r requirements.txt
```

## Usage

**Step 1 — collect** new matching listings and their landlord contacts:

```bash
python3 run.py --dry-run   # scrape + filter, show new listings, change nothing
python3 run.py             # also fetch contact info and update the log
```

This writes two gitignored files: `processed.json` (permanent log of every listing handled) and `new_contacts.json` (just this run's finds).

**Step 2 — outreach** to everyone collected but not yet contacted:

```bash
python3 outreach.py --dry-run   # preview who would be emailed
python3 outreach.py             # send emails + build whatsapp.html
```

- **Email**: sends `message.txt` via Gmail SMTP to every landlord with an email address, one email per landlord (owners of several listings get a single message covering all of them). Requires a gitignored `email_credentials.json`:
  ```json
  {"email": "you@gmail.com", "app_password": "xxxx xxxx xxxx xxxx", "display_name": "Your Name"}
  ```
  Create the app password at https://myaccount.google.com/apppasswords (needs 2-step verification — your normal password won't work).
- **WhatsApp**: `outreach.py` also generates `whatsapp.html` with manual click-to-chat links as a fallback.

**Step 3 — WhatsApp automation** for landlords not reachable by email (no address, or it bounced):

```bash
python3 whatsapp_send.py --dry-run   # list who would be messaged
python3 whatsapp_send.py --login     # one-time: scan wa_qr.png with your phone
python3 whatsapp_send.py             # send to all pending targets
```

Drives WhatsApp Web headless with a persistent session (`wa_profile/`). It strips the "here is my WhatsApp number" line from the message, uses one phone number per landlord, waits 20–40 s between sends, and never messages anyone already emailed or already messaged. Note: automating WhatsApp Web is against WhatsApp's ToS — keep volumes low.

Contacted landlords are marked in `processed.json`, so re-running never messages anyone twice on any channel.

## Search criteria

Edit the `SEARCH` dict at the top of `run.py`. Every field accepts `None` to mean "don't filter". Current defaults: Liège campuses, total ≤ 500 €, domiciliation explicitly allowed. New listings are displayed in price tiers (≤ 400 €, 401–450 €, 451–500 €).

| Key | Meaning | Example |
|---|---|---|
| `campus` | Campuses to search (G/L/S/A) | `["L", "S"]` |
| `max_total` | Max rent + charges in € | `500` |
| `types` | Housing types to keep | `{"Chambre", "Studio"}` |
| `occupation` | `"Indépendant"`, `"Communautaire"` or `"Chez l'habitant"` | |
| `domiciliation` | `True` → only listings explicitly allowing it | |
| `short_stay` | `True` → only short-stay-friendly listings | |
| `max_distance_center_m` | Max distance to Liège centre in meters | `3000` |

## Politeness

The scraper waits 1 second between requests (`REQUEST_DELAY`) and only fetches contact info for listings that are new and match your criteria. Keep it that way.
