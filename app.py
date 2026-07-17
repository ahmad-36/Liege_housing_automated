"""
Liège Housing Finder — no-code web UI for the scrapers in this repo.

Multi-user: everyone creates their own profile (name + optional access
code). Each profile has its own search criteria, message templates,
credentials and contact history, stored under profiles/<id>/. The special
"owner" profile keeps using the original single-user files, so CLI runs
and the UI stay in sync for the repo owner.

Storage is plain local files behind small helper functions — swap them
for a cloud database (Supabase / Google Sheets) when hosting, since free
hosts wipe local files on restart.

Usage:
  pip install -r requirements.txt
  playwright install chromium
  streamlit run app.py
"""

import hashlib
import json
import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd
import streamlit as st

import hf_sync

ROOT = Path(__file__).parent
KOTALIEGE_DIR = ROOT / "kotaliege_automated"
ULIEGE_DIR = ROOT / "logement_uliege"
PROFILES_DIR = ROOT / "profiles"
INDEX_FILE = PROFILES_DIR / "index.json"

DEFAULT_MESSAGE = """Bonjour,

Je suis étudiant(e) et je cherche un logement à Liège. Votre annonce \
m'intéresse beaucoup — est-elle toujours disponible ?

Serait-il possible d'organiser une visite (ou une visite vidéo) ? \
Est-il également possible d'y domicilier mon adresse officielle ?

Cordialement"""

st.set_page_config(page_title="Liège Housing Finder", page_icon="🏠", layout="wide")

# Streamlit Community Cloud provides secrets via st.secrets, not env vars —
# bridge the ones hf_sync.py needs. No-op when there's no secrets file.
for _k in ("HF_TOKEN", "HF_DATA_REPO"):
    try:
        if not os.environ.get(_k) and _k in st.secrets:
            os.environ[_k] = str(st.secrets[_k])
    except Exception:  # no secrets.toml → plain local run
        break


@st.cache_resource
def ensure_chromium() -> str:
    """Hosted platforms (e.g. Streamlit Community Cloud) don't ship
    Playwright's Chromium — install it once per server process. Instant
    no-op when the browser is already there (local dev, Docker image)."""
    r = subprocess.run([sys.executable, "-m", "playwright", "install", "chromium"],
                       capture_output=True, text=True)
    return "ok" if r.returncode == 0 else f"failed: {(r.stderr or r.stdout)[-300:]}"


chromium_status = ensure_chromium()


# ── Small helpers ─────────────────────────────────────────────────────

def load_json(path: Path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return default


def read_env(path: Path) -> dict:
    env = {}
    if path.exists():
        for line in path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip()
    return env


def write_env(path: Path, updates: dict):
    env = read_env(path)
    env.update({k: v for k, v in updates.items() if v})  # blank field → keep old value
    path.write_text("".join(f"{k}={v}\n" for k, v in env.items()))


# ── Profiles ──────────────────────────────────────────────────────────

def hash_code(code: str) -> str:
    return hashlib.sha256(code.encode()).hexdigest()


def load_index() -> dict:
    index = load_json(INDEX_FILE, {})
    if not index:
        # First run: register the repo owner's original single-user files
        # as a profile, so existing history shows up in the UI.
        index = {"owner": {"name": "Owner (original data)", "legacy": True,
                           "code_hash": None,
                           "created": datetime.now().strftime("%Y-%m-%d")}}
        save_index(index)
    return index


def save_index(index: dict):
    PROFILES_DIR.mkdir(exist_ok=True)
    INDEX_FILE.write_text(json.dumps(index, indent=2, ensure_ascii=False))


def profile_paths(pid: str, meta: dict) -> dict:
    """Where this profile's files live. The legacy 'owner' profile maps to
    the original single-user locations; everyone else gets profiles/<id>/."""
    if meta.get("legacy"):
        return {
            "dir": None,  # scrapers use their original paths (no LIEGE_PROFILE_DIR)
            "config": ROOT / "search_config.json",
            "env": ROOT / ".env",
            "kota_contacted": KOTALIEGE_DIR / "contacted.json",
            "kota_message": KOTALIEGE_DIR / "message.txt",
            "ul_processed": ULIEGE_DIR / "processed.json",
            "ul_message": ULIEGE_DIR / "message.txt",
            "whatsapp": ULIEGE_DIR / "whatsapp.html",
        }
    d = PROFILES_DIR / pid
    return {
        "dir": d,
        "config": d / "search_config.json",
        "env": d / ".env",
        "kota_contacted": d / "kotaliege_contacted.json",
        "kota_message": d / "kotaliege_message.txt",
        "ul_processed": d / "uliege_processed.json",
        "ul_message": d / "uliege_message.txt",
        "whatsapp": d / "whatsapp.html",
    }


def create_profile(index: dict, name: str, code: str) -> str | None:
    pid = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    if not pid:
        st.error("Please enter a name.")
        return None
    if pid in index:
        st.error(f"A profile named “{index[pid]['name']}” already exists — "
                 "pick another name or open it on the left.")
        return None
    d = PROFILES_DIR / pid
    d.mkdir(parents=True, exist_ok=True)
    (d / "kotaliege_message.txt").write_text(DEFAULT_MESSAGE)
    (d / "uliege_message.txt").write_text(DEFAULT_MESSAGE)
    index[pid] = {"name": name.strip(), "legacy": False,
                  "code_hash": hash_code(code) if code else None,
                  "created": datetime.now().strftime("%Y-%m-%d")}
    save_index(index)
    return pid


# ── Running the scrapers ──────────────────────────────────────────────

def run_script(script: Path, args: list[str], env: dict, log_placeholder) -> int:
    proc = subprocess.Popen(
        [sys.executable, "-u", str(script), *args],
        cwd=script.parent,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        env=env,
    )
    lines = []
    for line in proc.stdout:
        lines.append(line.rstrip())
        log_placeholder.code("\n".join(lines[-30:]), language=None)
    proc.wait()
    log_placeholder.code("\n".join(lines) or "(no output)", language=None)
    return proc.returncode


def run_with_status(label: str, script: Path, args: list[str], paths: dict):
    # Base env stripped of any owner credentials from the shell; the
    # profile's own .env is layered on top.
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("KOTALIEGE_", "GMAIL_"))}
    env["PYTHONUNBUFFERED"] = "1"
    env.update(read_env(paths["env"]))
    if paths["dir"] is not None:
        env["LIEGE_PROFILE_DIR"] = str(paths["dir"])
    with st.status(label, expanded=True) as status:
        placeholder = st.empty()
        code = run_script(script, args, env, placeholder)
        if code == 0:
            status.update(label=f"{label} — done ✅", state="complete", expanded=True)
        else:
            status.update(label=f"{label} — failed (exit {code}) ❌", state="error", expanded=True)
    persist(label)


def records_table(records: list[dict], columns: dict):
    if not records:
        st.caption("Nothing here yet.")
        return
    df = pd.DataFrame(records)
    df = df[[c for c in columns if c in df.columns]]
    st.dataframe(
        df,
        width="stretch",
        hide_index=True,
        column_config={
            "url": st.column_config.LinkColumn("listing", display_text="open ↗"),
            **{k: v for k, v in columns.items() if v},
        },
    )


# ── Cloud persistence (hosted deployments only, see hf_sync.py) ──────

@st.cache_resource
def restore_cloud_state() -> str:
    """Runs once per server process: pull state from the HF dataset."""
    try:
        return hf_sync.restore()
    except Exception as e:
        return f"restore failed: {e}"


def persist(context: str):
    """Back the current state up to the HF dataset (no-op when local)."""
    if not hf_sync.enabled():
        return
    try:
        hf_sync.backup()
    except Exception as e:
        st.warning(f"⚠️ Cloud backup failed ({context}): {e} — "
                   "changes may be lost when the server restarts.")


cloud_status = restore_cloud_state() if hf_sync.enabled() else None


# ── Welcome / profile gate ────────────────────────────────────────────

index = load_index()

if "profile_id" not in st.session_state or st.session_state.profile_id not in index:
    st.title("🏠 Liège Housing Finder")
    st.markdown(
        "Automated student-housing search for Liège: scrapes "
        "[kotaliege.be](https://www.kotaliege.be) and the "
        "[ULiège housing database](https://logement.uliege.be), filters by "
        "**your** criteria, and contacts landlords for you.\n\n"
        "Pick your profile to continue — or create one, it takes 10 seconds."
    )
    col1, col2 = st.columns(2, gap="large")

    with col1:
        st.subheader("Open your profile")
        sel = st.selectbox("Profile", options=list(index),
                           format_func=lambda k: index[k]["name"])
        code_in = st.text_input("Access code", type="password",
                                help="Leave empty if this profile has no code.")
        if st.button("➡️ Open", width="stretch"):
            meta = index[sel]
            if meta.get("code_hash") and hash_code(code_in) != meta["code_hash"]:
                st.error("Wrong access code.")
            else:
                st.session_state.profile_id = sel
                st.rerun()

    with col2:
        st.subheader("New here? Create a profile")
        new_name = st.text_input("Your name")
        new_code = st.text_input("Choose an access code (optional)", type="password",
                                 help="Protects your profile from others using this app. "
                                      "Leave empty for no code.")
        new_code2 = st.text_input("Repeat access code", type="password")
        if st.button("✨ Create profile", type="primary", width="stretch"):
            if new_code != new_code2:
                st.error("The two access codes don't match.")
            else:
                pid = create_profile(index, new_name, new_code)
                if pid:
                    persist("profile created")
                    st.session_state.profile_id = pid
                    st.rerun()
    st.stop()

pid = st.session_state.profile_id
meta = index[pid]
paths = profile_paths(pid, meta)
env = read_env(paths["env"])
cfg = load_json(paths["config"], {})


# ── Sidebar: profile + search criteria ────────────────────────────────

with st.sidebar:
    st.title("🏠 Liège Housing Finder")
    prof_col, out_col = st.columns([3, 1])
    prof_col.markdown(f"👤 **{meta['name']}**")
    if out_col.button("Exit", help="Switch profile"):
        del st.session_state.profile_id
        st.rerun()

    with st.form("criteria"):
        st.subheader("Search criteria")

        budget = st.slider(
            "Budget (€ / month, rent + charges)",
            min_value=0, max_value=1500, step=25,
            value=(int(cfg.get("min_total") or 0), int(cfg.get("max_total") or 500)),
            help="Listings whose total cost falls outside this range are skipped. "
                 "Listings with an unknown price are kept.",
        )
        domiciliation = st.checkbox(
            "Domiciliation must be allowed", value=cfg.get("domiciliation_required", True),
            help="Needed to register your official address at the property "
                 "(required for a Belgian residence permit).",
        )
        available_from = st.date_input(
            "Available from (KotaLiège)",
            value=datetime.strptime(cfg.get("available_from", "2026-08-01"), "%Y-%m-%d").date(),
            help="Listings that become available before this date are skipped.",
        )

        st.divider()
        st.caption("ULiège database only:")

        campus_labels = {"L": "Liège centre-ville", "S": "Sart Tilman",
                         "G": "Gembloux", "A": "Arlon"}
        campus = st.multiselect(
            "Campus", options=list(campus_labels),
            format_func=campus_labels.get,
            default=[c for c in (cfg.get("campus") or ["L", "S"]) if c in campus_labels],
        )
        types = st.multiselect(
            "Housing types (empty = all)",
            options=["Chambre", "Studio", "Appartement", "Maison"],
            default=cfg.get("types") or [],
        )
        occupation = st.selectbox(
            "Occupation style",
            options=["Any", "Indépendant", "Communautaire", "Chez l'habitant"],
            index=(["Any", "Indépendant", "Communautaire", "Chez l'habitant"]
                   .index(cfg.get("occupation") or "Any")),
        )
        max_dist = st.number_input(
            "Max distance to centre (m, 0 = no limit)", min_value=0, max_value=20000,
            value=int(cfg.get("max_distance_center_m") or 0), step=500,
        )

        if st.form_submit_button("💾 Save criteria", width="stretch"):
            paths["config"].write_text(json.dumps({
                "min_total": budget[0] or None,
                "max_total": budget[1],
                "domiciliation_required": bool(domiciliation),
                "available_from": available_from.strftime("%Y-%m-%d"),
                "campus": campus or ["L", "S"],
                "types": types or None,
                "occupation": None if occupation == "Any" else occupation,
                "max_distance_center_m": int(max_dist) or None,
            }, indent=2, ensure_ascii=False))
            persist("criteria")
            st.success("Saved — used by the next run.")

    if paths["config"].exists():
        st.caption("Criteria saved ✓")
    else:
        st.caption("No saved criteria yet — using defaults (≤500 €, domiciliation, Aug 2026).")
    if cloud_status is not None:
        st.caption(f"☁️ Cloud sync: {os.environ['HF_DATA_REPO']} ({cloud_status})")
    if chromium_status != "ok":
        st.warning(f"Browser setup problem — KotaLiège scraping won't work: "
                   f"{chromium_status}")


# ── Main tabs ─────────────────────────────────────────────────────────

tab_kota, tab_uliege, tab_msg, tab_creds = st.tabs(
    ["🛏️ KotaLiège", "🎓 ULiège database", "✉️ Message templates", "🔑 Credentials & account"]
)


with tab_kota:
    st.subheader("kotaliege.be — scrape & auto-message")
    st.markdown(
        "Scrapes every listing type, filters by your criteria, and sends your "
        "message through the site's contact form **from your KotaLiège "
        "account**. Already-contacted listings are never messaged twice."
    )

    has_kota_creds = bool(env.get("KOTALIEGE_EMAIL") and env.get("KOTALIEGE_PASSWORD"))
    if meta.get("legacy"):
        has_kota_creds = has_kota_creds or bool(
            load_json(KOTALIEGE_DIR / "credentials.json", {}).get("email"))
    if not has_kota_creds:
        st.warning("No KotaLiège login saved for this profile — add it in the "
                   "**🔑 Credentials** tab (register free at kotaliege.be). "
                   "Dry runs work without it.")

    col1, col2 = st.columns(2)
    with col1:
        if st.button("🔍 Preview new listings (dry run)", width="stretch"):
            run_with_status("Dry run — kotaliege.be", KOTALIEGE_DIR / "run.py",
                            ["--dry-run"], paths)
    with col2:
        confirm = st.checkbox("I understand this sends real messages to landlords")
        if st.button("🚀 Scrape & send messages", type="primary",
                     width="stretch", disabled=not confirm):
            run_with_status("Live run — kotaliege.be", KOTALIEGE_DIR / "run.py", [], paths)

    st.divider()
    contacted = load_json(paths["kota_contacted"], [])
    sent = sum(1 for r in contacted if r.get("status") == "sent")
    st.subheader(f"Contacted so far: {sent} sent / {len(contacted)} total")
    records_table(
        sorted(contacted, key=lambda r: r.get("date", ""), reverse=True),
        {"ref": None, "total": st.column_config.NumberColumn("total €"),
         "type": None, "neighborhood": None, "status": None, "date": None, "url": None},
    )


with tab_uliege:
    st.subheader("logement.uliege.be — scrape & collect landlord contacts")
    st.markdown(
        "This site has no internal messaging: the scraper collects each new "
        "landlord's **name, phone and email** so you (or the outreach step "
        "below) can contact them."
    )

    col1, col2 = st.columns(2)
    with col1:
        if st.button("🔍 Preview new listings (dry run)", key="ul_dry", width="stretch"):
            run_with_status("Dry run — logement.uliege.be", ULIEGE_DIR / "run.py",
                            ["--dry-run"], paths)
    with col2:
        if st.button("📇 Scrape & collect contacts", type="primary", width="stretch"):
            run_with_status("Collecting contacts — logement.uliege.be",
                            ULIEGE_DIR / "run.py", [], paths)

    st.divider()
    st.subheader("Email outreach")
    has_gmail = bool(env.get("GMAIL_ADDRESS") and env.get("GMAIL_APP_PASSWORD"))
    if meta.get("legacy"):
        has_gmail = has_gmail or bool(
            load_json(ULIEGE_DIR / "email_credentials.json", {}).get("email"))
    if not has_gmail:
        st.warning("No Gmail credentials saved for this profile — add them in the "
                   "**🔑 Credentials** tab. Dry runs work without them.")

    col3, col4 = st.columns(2)
    with col3:
        if st.button("🔍 Preview outreach (dry run)", key="or_dry", width="stretch"):
            run_with_status("Dry run — outreach", ULIEGE_DIR / "outreach.py",
                            ["--dry-run"], paths)
    with col4:
        confirm_mail = st.checkbox("I understand this sends real emails to landlords")
        if st.button("✉️ Send emails + build WhatsApp page", type="primary",
                     width="stretch", disabled=not confirm_mail):
            run_with_status("Sending emails — outreach", ULIEGE_DIR / "outreach.py",
                            [], paths)
    if paths["whatsapp"].exists():
        st.download_button("⬇️ Download WhatsApp click-to-chat page",
                           data=paths["whatsapp"].read_text(),
                           file_name="whatsapp.html", mime="text/html",
                           help="Open it in a browser — each link opens a WhatsApp "
                                "chat with your message pre-filled.")

    st.divider()
    processed = load_json(paths["ul_processed"], [])
    flat = [{**r, "name": r.get("contact", {}).get("name", ""),
             "emails": ", ".join(r.get("contact", {}).get("emails", [])),
             "phones": ", ".join(r.get("contact", {}).get("phones", []))}
            for r in processed]
    st.subheader(f"Collected contacts: {len(processed)}")
    records_table(
        sorted(flat, key=lambda r: r.get("date", ""), reverse=True),
        {"ref": None, "total": st.column_config.NumberColumn("total €"),
         "title": None, "address": None, "name": None, "emails": None,
         "phones": None, "status": None, "date": None, "url": None},
    )


with tab_msg:
    st.subheader("The message sent to landlords")
    st.caption("Written in French — that's what Liège landlords expect. "
               "Keep it short, add your name and dates, and ask about "
               "domiciliation if you need it.")
    for label, path in [("KotaLiège contact form", paths["kota_message"]),
                        ("ULiège email / WhatsApp", paths["ul_message"])]:
        text = path.read_text() if path.exists() else DEFAULT_MESSAGE
        new_text = st.text_area(label, value=text, height=220,
                                key=f"msg_{path.parent.name}_{path.name}")
        if st.button(f"💾 Save {label} message", key=f"save_{path.parent.name}_{path.name}"):
            path.write_text(new_text)
            persist("message")
            st.success("Saved.")


with tab_creds:
    st.subheader("Credentials")
    st.caption("Stored on this server, only for your profile, never committed to "
               "git. Use a dedicated App Password (never your main password) for "
               "Gmail — you can revoke it anytime. Leave a field blank to keep "
               "its saved value.")

    with st.form("creds_kota"):
        st.markdown("**KotaLiège account** — free, register at "
                    "[kotaliege.be](https://www.kotaliege.be/_register_). "
                    "Needed to send messages through the site.")
        k_email = st.text_input("KotaLiège email", value=env.get("KOTALIEGE_EMAIL", ""))
        k_pass = st.text_input("KotaLiège password", type="password",
                               placeholder="•••••• (saved)" if env.get("KOTALIEGE_PASSWORD") else "")
        if st.form_submit_button("💾 Save KotaLiège login"):
            write_env(paths["env"], {"KOTALIEGE_EMAIL": k_email, "KOTALIEGE_PASSWORD": k_pass})
            persist("credentials")
            st.success("Saved.")

    with st.form("creds_gmail"):
        st.markdown("**Gmail (for ULiège outreach)** — create an *App Password* at "
                    "[myaccount.google.com/apppasswords](https://myaccount.google.com/apppasswords) "
                    "(requires 2-step verification; your normal password will NOT work).")
        g_addr = st.text_input("Gmail address", value=env.get("GMAIL_ADDRESS", ""))
        g_pass = st.text_input("Gmail app password", type="password",
                               placeholder="•••••• (saved)" if env.get("GMAIL_APP_PASSWORD") else "")
        g_name = st.text_input("Display name (shown as email sender)",
                               value=env.get("GMAIL_DISPLAY_NAME", ""))
        if st.form_submit_button("💾 Save Gmail credentials"):
            write_env(paths["env"], {"GMAIL_ADDRESS": g_addr, "GMAIL_APP_PASSWORD": g_pass,
                                     "GMAIL_DISPLAY_NAME": g_name})
            persist("credentials")
            st.success("Saved.")

    st.divider()
    st.subheader("Account")
    with st.form("access_code"):
        st.markdown(f"**Profile:** {meta['name']} — "
                    + ("🔒 protected by an access code"
                       if meta.get("code_hash") else "🔓 no access code set"))
        old_code = st.text_input("Current access code", type="password",
                                 disabled=not meta.get("code_hash"))
        new_code = st.text_input("New access code (empty = remove the code)",
                                 type="password")
        if st.form_submit_button("💾 Change access code"):
            if meta.get("code_hash") and hash_code(old_code) != meta["code_hash"]:
                st.error("Current access code is wrong.")
            else:
                meta["code_hash"] = hash_code(new_code) if new_code else None
                index[pid] = meta
                save_index(index)
                persist("access code")
                st.success("Access code updated.")
