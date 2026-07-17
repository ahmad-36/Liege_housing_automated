# Liège Automated Housing Finder & Scraper 🇧🇪🏠

An automated tool designed to scrape, filter, and track apartment and student housing listings in Liège, Belgium — and contact landlords on your behalf.

Finding accommodation or a "kot" (student room) in Liège is incredibly competitive. Listings disappear within hours. This open-source project automates the exhausting process of constantly refreshing real estate websites, filtering results by your exact criteria, and reaching out to landlords the instant a matching property becomes available.

## ✨ Key Features

- **No-Code Web UI:** A [Streamlit](https://streamlit.io) app (`app.py`) to set your search criteria (budget range, campus, domiciliation…), save credentials, run the scrapers and browse results — all from the browser, no code editing needed.
- **Multi-User Profiles:** Everyone signs up with their email + a password (verified locally — no external auth service). Each profile has its own criteria, message templates, credentials and contact history under `profiles/<id>/`, so several people can share one running app without seeing each other's data.
- **Automated Scraping:** Regularly scrapes top Belgian student housing platforms — [KotaLiège](https://www.kotaliege.be), the [ULiège housing database](https://logement.uliege.be), and [KotHouse](http://www.kothouse.be) — for every listing in the Liège region.
- **Automated Outreach:** Sends your message to matching landlords via the platform's contact form, email (Gmail SMTP), or WhatsApp — so you never have to copy-paste again.
- **Custom Filters:** Filter by total price range (rent + charges), lease duration, domiciliation policy, availability date, and housing type (kots, studios, colocations).
- **Three Price Tiers:** Results are categorized into ≤ 400 €, 401–450 €, and 451–500 € for quick decision-making.
- **Duplicate Prevention:** A persistent JSON log tracks every listing already contacted — re-runs only message new listings, never the same landlord twice.
- **Multi-Platform:** Three independent bots, each tailored to its platform's structure and contact method.

## 🛠️ Tech Stack & Requirements

- **Language:** Python 3.10+
- **Browser Automation:** [Playwright](https://playwright.dev/python/) (headless Chromium)
- **HTTP Scraping:** Requests + BeautifulSoup (for the ULiège bot)
- **Email:** Gmail SMTP (App Password)
- **WhatsApp:** Playwright driving WhatsApp Web
- **No database required** — all state is stored in local JSON files.

## 📁 Project Structure

```
liege_housing_automated/
├── app.py                  # Streamlit web UI — run everything from the browser
├── requirements.txt        # All dependencies (UI + scrapers)
├── .env.example            # Template for credentials (copy to .env)
├── search_config.json      # Your saved search criteria (written by the UI)
├── kotaliege_automated/    # Bot for kotaliege.be
│   ├── run.py              # Main entry point — scrape, filter, message
│   ├── scraper.py          # Listing scraper
│   ├── contacted.json      # Persistent log of contacted listings
│   └── README.md           # Setup & usage
├── kothouse_automated/     # Bot for kothouse.be (single-landlord site)
│   ├── run.py              # Main entry point — scrape, filter, message
│   ├── contacted.json      # Persistent log of contacted listings
│   └── README.md           # Setup & usage
├── logement_uliege/        # Bot for logement.uliege.be
│   ├── run.py              # Scrape & collect contacts
│   ├── outreach.py         # Email sender (Gmail SMTP)
│   ├── whatsapp_send.py    # WhatsApp sender
│   ├── processed.json      # Persistent log of contacted landlords
│   └── README.md           # Setup & usage
└── README.md               # This file
```

## 🚀 Getting Started

### Prerequisites

- Python 3.10 or higher
- A KotaLiège account (free, register at [kotaliege.be](https://www.kotaliege.be/_register_))
- (Optional) A Gmail App Password for automated emails
- (Optional) WhatsApp Web access for automated messages

### Installation

1. Clone this repository and install dependencies:
   ```bash
   git clone https://github.com/ahmad-36/liege_housing_automated.git
   cd liege_housing_automated
   pip install -r requirements.txt
   playwright install chromium
   ```

### Option A — Web UI (recommended, no code editing)

```bash
streamlit run app.py
```

Then in the browser page that opens:

1. **Sign up** with your name, email and a password (or log in). Profiles created before email login existed can be opened via the expander on the welcome screen and upgraded to email login in the account tab. Note: there is no password-reset email on the free setup — the app owner can clear a forgotten password by editing `profiles/index.json` in the data repo.
2. **🔑 Credentials tab** — enter your KotaLiège login and (optionally) your Gmail App Password. They're stored only for your profile, never committed to git.
3. **Sidebar** — set your budget range, campuses, domiciliation and availability date, hit **Save criteria**.
4. **✉️ Message templates tab** — adjust the message sent to landlords.
5. **🛏️ KotaLiège / 🎓 ULiège tabs** — click **Preview (dry run)** to see what matches, then run for real. Results and collected contacts appear as tables below the buttons.

The repo owner's original single-user files (state in each bot's folder, `.env` at the root) show up as the **“Owner (original data)”** profile, so CLI runs and the UI stay in sync.

### Option B — Command line

1. Configure credentials — either copy `.env.example` to `.env` and fill it in, or use the per-bot JSON files (`kotaliege_automated/credentials.json`, `logement_uliege/email_credentials.json`).

2. Run the scrapers:
   ```bash
   # KotaLiège — scrape + message new listings
   python kotaliege_automated/run.py

   # Preview only (no messages sent)
   python kotaliege_automated/run.py --dry-run

   # ULiège — scrape + collect contacts
   python logement_uliege/run.py

   # ULiège — send emails to new contacts
   python logement_uliege/outreach.py
   ```

## ☁️ Free Hosting on Streamlit Community Cloud

> Hugging Face Spaces' Docker SDK went paid-only in July 2026, so the free path is [Streamlit Community Cloud](https://share.streamlit.io) for the app itself + a **private Hugging Face dataset** for persistent storage (free hosts wipe local files on every restart). `packages.txt` (Chromium system libraries) and the startup hook in `app.py` (installs the Chromium build, bridges `st.secrets` → env) make the repo deploy-ready. The `Dockerfile` still works for any Docker host, paid or self-hosted.

1. Push this repo to a **public** GitHub repository.
2. On [share.streamlit.io](https://share.streamlit.io) → **Create app** → *Deploy a public app from GitHub* → pick the repo, branch `main`, main file `app.py`.
3. In the app's **⋮ → Settings → Secrets**, paste:
   ```toml
   HF_TOKEN = "hf_xxx"                        # Hugging Face WRITE token
   HF_DATA_REPO = "<hf-username>/liege-housing-data"
   ```
   The app auto-creates that dataset repo **private**, restores all profiles from it on startup, and backs them up after every change. Never make the dataset public — it holds credentials and contact history.
4. (Optional) Seed the cloud with your existing local data before the first visit:
   ```bash
   HF_TOKEN=hf_xxx HF_DATA_REPO=<you>/liege-housing-data python hf_sync.py backup
   ```

Notes: free apps go to sleep after ~12 h without visitors — anyone can wake them with one click (state is safe in the dataset). The app URL is public: anyone who finds it can create a profile, but they'd need their own KotaLiège/Gmail credentials to send anything.

## 🗺️ Targeted Locations

The scraper covers all major residential and student hubs in Liège:

| Area | Zip Code | Notes |
|------|----------|-------|
| Liège Centre / Cathédrale | 4000 | City center, close to HEC & downtown campus |
| Outremeuse | 4020 | Popular student quarter across the Meuse |
| Angleur / Sart-Tilman | 4031 | Closest to ULiège main campus |
| Guillemins / Avroy | 4000 | Near the TGV station |
| Botanique / Saint-Gilles | 4000 | Dense student housing area |
| Fragnée / Val Benoît | 4000 | Near the converted Val Benoît campus |

## 🔍 How It Works

1. **Scrape** — Playwright opens a headless browser, navigates through every page of every listing type (kots, studios, kots-chez-l'habitant, colocations), and extracts rent, charges, size, neighborhood, duration, domiciliation policy, and availability.
2. **Filter** — Listings are filtered against your criteria: total budget ≤ 500 €, domiciliation not refused, available from your target date.
3. **Deduplicate** — The persistent `contacted.json` log is checked. Any listing already messaged is skipped.
4. **Contact** — For new matches, the bot navigates to each listing's detail page and submits your message through the platform's contact form. On ULiège, it sends personalized emails and WhatsApp messages instead.
5. **Log** — Every action is recorded with timestamp, status, and listing details. Re-runs pick up exactly where you left off.

## ⚙️ Configuration

The easiest way is the sidebar of the web UI (**Save criteria** writes `search_config.json`, which both bots pick up on their next run). The same file can be edited by hand:

```json
{
  "max_total": 500,
  "domiciliation_required": true,
  "available_from": "2026-08-01",
  "campus": ["L", "S"],
  "types": null,
  "occupation": null,
  "max_distance_center_m": null
}
```

If `search_config.json` doesn't exist, each bot falls back to the defaults at the top of its `run.py`.

## 🤝 Contributing

Contributions, issues, and feature requests are welcome! If you want to add support for a new Belgian housing website (Immoweb, Brukot, Kotanamur…), feel free to open a Pull Request.

## 📄 License

Distributed under the MIT License. See `LICENSE` for more information.

## ⚠️ Disclaimer

Built for a personal housing search during an exchange semester. If you reuse it:

- Keep request volumes low and add delays between requests.
- Respect the platforms and the people behind the listings.
- Automating WhatsApp violates its Terms of Service and can get your number banned.
- Always check a platform's Terms of Service before running automated tools against it.
