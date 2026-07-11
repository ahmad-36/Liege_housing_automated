# KotaLiege Scraper & Auto-Messenger

A Playwright bot that helps students find housing in Liège on [kotaliege.be](https://www.kotaliege.be). It scrapes all listings (kots, studios, kots chez l'habitant, colocations), filters them by your criteria, and automatically sends your contact message to new listings — while remembering which ones it has already contacted, so you can run it repeatedly without spamming anyone.

## How it works

1. Loads `contacted.json`, a persistent log of every listing already handled
2. Scrapes all listing types across all pages
3. Filters by criteria (total cost ≤ 500 €, domiciliation not refused, available from August 2026)
4. Skips listings already contacted
5. Messages the new ones through the platform's contact form
6. Updates `contacted.json` with the results

## Setup

Requires Python 3.10+.

```bash
pip install -r requirements.txt
playwright install chromium
```

Create two local files in the project folder (both are gitignored — never commit them):

**`credentials.json`** — your kotaliege.be login:

```json
{
  "email": "you@example.com",
  "password": "your-password"
}
```

**`message.txt`** — the message sent to each listing (French recommended). If this file is missing, a generic placeholder message is used, so you'll want to write your own with your name and contact details.

## Usage

```bash
python3 run.py --dry-run   # scrape + filter only, show what would be messaged
python3 run.py             # scrape and message all new matching listings
```

Always start with `--dry-run` to check the results before sending anything.

The first run logs in with your credentials and saves the session to `session_state.json`, so later runs skip the login. Results are grouped into price tiers (≤ 400 €, 401–450 €, 451–500 €) in the output.

## Adjusting the filters

The criteria are currently hardcoded in `run.py` — edit them to fit your search:

- **Budget**: the `l.total > 500` check in `main()`
- **Availability date**: the `datetime(2026, 8, 1)` threshold in `is_available_ok()`
- **Listing types**: the `LISTING_TYPES` list

## Files

| File | Purpose |
|---|---|
| `run.py` | Main script — scrape, filter, message, log |
| `scraper.py` | Standalone scraper (no messaging), prints a tiered table and saves `results.json` |
| `scrape_details.py`, `login_and_message.py`, `message_*.py`, `debug_*.py` | One-off development/debug scripts kept for reference |

## Data files (local only, gitignored)

- `credentials.json` — your login
- `session_state.json` — saved browser session
- `contacted.json` — log of every listing messaged, with status and date
- `results.json` — output of `scraper.py`

## Disclaimer

For personal use in your own housing search. Be respectful: the built-in contacted-log prevents duplicate messages — don't circumvent it, and don't hammer the site.
