# Liège Housing Automated

Tools that automate the hunt for student housing in Liège, Belgium. Instead of refreshing listing sites every day and writing the same message over and over, these scrapers check the platforms for you, filter listings by your budget and criteria, and contact the landlords — while keeping a persistent log so nobody is ever messaged twice.

## The tools

### [`kotaliege_automated/`](kotaliege_automated/)

Bot for **[kotaliege.be](https://www.kotaliege.be)**. Uses Playwright to log in, scrape all listing types (kots, studios, colocations…), filter by budget / availability / domiciliation, and send your message through the platform's own contact form. Remembers every listing already contacted in a local log.

### [`logement_uliege/`](logement_uliege/)

Bot for the **[ULiège private-housing database](https://logement.uliege.be)**. No login needed — plain HTTP scraping of all listings, filtering by price tiers, campus, domiciliation and more. The site has no messaging system, so the tool collects each landlord's published contact details and reaches out directly:

- **Email** — automated via Gmail SMTP, one email per landlord (grouped across their listings)
- **WhatsApp** — automated via WhatsApp Web with human-like pacing, plus a click-to-chat fallback page

Every listing's status (`emailed`, `whatsapped`, `email_bounced`, …) is tracked so re-runs only touch brand-new listings.

## Design principles

- **Never contact anyone twice** — persistent JSON logs are checked before every send, on every channel.
- **Nothing personal in the repo** — credentials, messages, scraped contact data, and session files are all gitignored; each folder ships only code.
- **Be polite to the sites** — delays between requests, contacts fetched only for new matching listings.

## Quick start

Each tool has its own README with setup and usage:

- [kotaliege_automated/README.md](kotaliege_automated/README.md)
- [logement_uliege/README.md](logement_uliege/README.md)

## Disclaimer

Built for a personal housing search. If you reuse it: keep the volumes low, respect the platforms and the people behind the listings, and know that automating WhatsApp violates its terms of service and can get a number banned.
