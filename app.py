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
import secrets
import subprocess
import sys
from datetime import datetime, timedelta
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
    # Legacy access-code hash, kept so pre-email profiles still open.
    return hashlib.sha256(code.encode()).hexdigest()


def hash_password(password: str, salt_hex: str | None = None) -> tuple[str, str]:
    salt = bytes.fromhex(salt_hex) if salt_hex else os.urandom(16)
    h = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 100_000)
    return salt.hex(), h.hex()


def check_password(meta: dict, password: str) -> bool:
    if not meta.get("pw_hash"):
        return False
    _, h = hash_password(password, meta["pw_salt"])
    return h == meta["pw_hash"]


def valid_email(email: str) -> bool:
    return bool(re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email.strip()))


def find_by_email(index: dict, email: str) -> str | None:
    email = email.strip().lower()
    for pid, m in index.items():
        if m.get("email", "").lower() == email:
            return pid
    return None


def issue_login_token(index: dict, pid: str):
    """Stay-logged-in token: stored hashed with a 30-day expiry, put in the
    URL so revisiting the same address (bookmark/history) skips the login."""
    tok = secrets.token_urlsafe(24)
    today = datetime.now().strftime("%Y-%m-%d")
    meta = index[pid]
    tokens = {h: e for h, e in meta.get("tokens", {}).items() if e >= today}
    tokens[hash_code(tok)] = (datetime.now() + timedelta(days=30)).strftime("%Y-%m-%d")
    meta["tokens"] = dict(list(tokens.items())[-10:])  # keep the 10 newest
    index[pid] = meta
    save_index(index)
    persist("login")
    st.query_params["t"] = tok


def generate_message(who: str, when: str, extra: str, language: str) -> str:
    """Draft a landlord message with a free hosted model (HF Inference,
    uses the same HF_TOKEN as the cloud sync)."""
    token = os.environ.get("HF_TOKEN")
    if not token:
        raise RuntimeError("no HF_TOKEN configured in the app secrets")
    from huggingface_hub import InferenceClient
    client = InferenceClient(
        model=os.environ.get("HF_LLM_MODEL", "Qwen/Qwen2.5-7B-Instruct"), token=token)
    out = client.chat_completion(
        messages=[
            {"role": "system", "content":
                "You write short, polite messages from a student to a landlord "
                "about a housing listing. Output ONLY the message text — no "
                "subject line, no explanations, no placeholders like [Name]. "
                "80–120 words. Always ask whether the listing is still "
                "available and whether a visit (or video call) is possible."},
            {"role": "user", "content":
                f"Write the message in {language}.\n"
                f"About me: {who or 'a student looking for housing in Liège'}.\n"
                f"Rental period: {when or 'not specified'}.\n"
                f"Also mention: {extra or 'nothing else'}."},
        ],
        max_tokens=400, temperature=0.7)
    return out.choices[0].message.content.strip()


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


def create_profile(index: dict, name: str, email: str, password: str) -> str | None:
    if not name.strip():
        st.error("Please enter your name.")
        return None
    if not valid_email(email):
        st.error("That doesn't look like a valid email address.")
        return None
    if find_by_email(index, email):
        st.error("There is already a profile with this email — log in on the left.")
        return None
    if len(password) < 6:
        st.error("Please choose a password of at least 6 characters.")
        return None
    pid = re.sub(r"[^a-z0-9]+", "-", email.strip().lower().split("@")[0]).strip("-")
    while not pid or pid in index:
        pid = f"{pid or 'user'}-{os.urandom(2).hex()}"
    d = PROFILES_DIR / pid
    d.mkdir(parents=True, exist_ok=True)
    (d / "kotaliege_message.txt").write_text(DEFAULT_MESSAGE)
    (d / "uliege_message.txt").write_text(DEFAULT_MESSAGE)
    salt, pw_hash = hash_password(password)
    index[pid] = {"name": name.strip(), "email": email.strip().lower(),
                  "legacy": False, "pw_salt": salt, "pw_hash": pw_hash,
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
           if not k.startswith(("KOTALIEGE_", "GMAIL_", "EMAIL_", "SMTP_"))}
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

def url_token() -> str | None:
    t = st.query_params.get("t")
    if isinstance(t, list):  # some runtimes hand back a list
        t = t[0] if t else None
    return t


# Stay-logged-in: a valid token in the URL skips the login screen.
if "profile_id" not in st.session_state and url_token():
    _th = hash_code(url_token())
    _today = datetime.now().strftime("%Y-%m-%d")
    for _pid, _m in index.items():
        if _m.get("tokens", {}).get(_th, "") >= _today:
            st.session_state.profile_id = _pid
            break

if "profile_id" not in st.session_state or st.session_state.profile_id not in index:
    st.title("🏠 Liège Housing Finder")
    st.markdown(
        "Automated student-housing search for Liège: scrapes "
        "[kotaliege.be](https://www.kotaliege.be) and the "
        "[ULiège housing database](https://logement.uliege.be), filters by "
        "**your** criteria, and contacts landlords for you.\n\n"
        "Log in to continue — or sign up, it takes 10 seconds."
    )
    col1, col2 = st.columns(2, gap="large")

    with col1:
        st.subheader("Log in")
        with st.form("login"):
            log_email = st.text_input("Email")
            log_pw = st.text_input("Password", type="password")
            if st.form_submit_button("➡️ Log in", width="stretch"):
                lpid = find_by_email(index, log_email)
                if lpid is None:
                    st.error("No profile with this email — sign up on the right.")
                elif not check_password(index[lpid], log_pw):
                    st.error("Wrong password.")
                else:
                    st.session_state.profile_id = lpid
                    issue_login_token(index, lpid)
                    st.rerun()

    with col2:
        st.subheader("New here? Sign up")
        with st.form("signup"):
            new_name = st.text_input("Your name")
            new_email = st.text_input("Email")
            new_pw = st.text_input("Password (min. 6 characters)", type="password")
            new_pw2 = st.text_input("Repeat password", type="password")
            if st.form_submit_button("✨ Create profile", type="primary", width="stretch"):
                if new_pw != new_pw2:
                    st.error("The two passwords don't match.")
                else:
                    npid = create_profile(index, new_name, new_email, new_pw)
                    if npid:
                        st.session_state.profile_id = npid
                        issue_login_token(index, npid)  # also persists
                        st.rerun()

    # Profiles created before email login existed (no email on file yet)
    legacy_pids = [k for k, m in index.items() if not m.get("email")]
    if legacy_pids:
        with st.expander("Profile created before email login? Open it here"):
            sel = st.selectbox("Profile", options=legacy_pids,
                               format_func=lambda k: index[k]["name"])
            code_in = st.text_input("Access code (leave empty if none was set)",
                                    type="password")
            st.caption("Once inside, add your email + password in "
                       "**🔑 Credentials & account** to switch to email login.")
            if st.button("➡️ Open", width="stretch"):
                meta = index[sel]
                if meta.get("code_hash") and hash_code(code_in) != meta["code_hash"]:
                    st.error("Wrong access code.")
                else:
                    st.session_state.profile_id = sel
                    issue_login_token(index, sel)
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
    if out_col.button("Exit", help="Log out on this device"):
        tok = url_token()
        if tok:  # revoke this device's stay-logged-in token
            meta.get("tokens", {}).pop(hash_code(tok), None)
            save_index(index)
            persist("logout")
        st.query_params.clear()
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
    has_email = bool((env.get("EMAIL_ADDRESS") or env.get("GMAIL_ADDRESS"))
                     and (env.get("EMAIL_PASSWORD") or env.get("GMAIL_APP_PASSWORD")))
    if meta.get("legacy"):
        has_email = has_email or bool(
            load_json(ULIEGE_DIR / "email_credentials.json", {}).get("email"))
    if not has_email:
        st.warning("No sending email saved for this profile — add one in the "
                   "**🔑 Credentials** tab (Gmail, Outlook, Yahoo… all work). "
                   "Dry runs work without it.")

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

    with st.expander("✨ Let AI write it for you (free)"):
        c1, c2 = st.columns(2)
        ai_who = c1.text_input("Who are you?",
                               placeholder="e.g. Marie, 22, Erasmus student at ULiège")
        ai_when = c2.text_input("When / how long?",
                                placeholder="e.g. September 2026 – January 2027")
        ai_extra = st.text_input(
            "Anything else to mention?",
            placeholder="e.g. non-smoker, need domiciliation, WhatsApp +32 ...")
        ai_lang = st.selectbox("Language", ["French (what landlords expect)", "English"])
        if st.button("✨ Generate draft"):
            try:
                with st.spinner("Writing…"):
                    st.session_state.ai_draft = generate_message(
                        ai_who, ai_when, ai_extra,
                        "French" if ai_lang.startswith("French") else "English")
            except Exception as e:
                st.error(f"Generation failed ({e}). The free AI quota may be used "
                         "up for this month — edit the template below by hand instead.")
        if st.session_state.get("ai_draft"):
            draft = st.text_area("Draft — edit freely, then apply:",
                                 value=st.session_state.ai_draft, height=200,
                                 key="ai_draft_edit")
            ca, cb = st.columns(2)
            for col, btn_label, target in [(ca, "⬇️ Use for KotaLiège", "kota_message"),
                                           (cb, "⬇️ Use for ULiège", "ul_message")]:
                if col.button(btn_label):
                    p = paths[target]
                    p.write_text(draft)
                    st.session_state[f"msg_{p.parent.name}_{p.name}"] = draft
                    persist("message")
                    st.success("Applied to the template below — don't forget to "
                               "proofread names and dates.")

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

    with st.form("creds_email"):
        st.markdown(
            "**Email for contacting landlords (ULiège outreach)** — Gmail, "
            "Outlook/Hotmail, Yahoo, iCloud, GMX and web.de are auto-detected; "
            "any other provider works via the custom SMTP fields. Most providers "
            "need an **app password**, not your normal one (Gmail: "
            "[myaccount.google.com/apppasswords](https://myaccount.google.com/apppasswords), "
            "requires 2-step verification)."
        )
        e_addr = st.text_input(
            "Sender email (defaults to your login email)",
            value=env.get("EMAIL_ADDRESS") or env.get("GMAIL_ADDRESS")
                  or meta.get("email", ""))
        e_pass = st.text_input(
            "Email app password", type="password",
            placeholder="•••••• (saved)"
            if env.get("EMAIL_PASSWORD") or env.get("GMAIL_APP_PASSWORD") else "")
        e_name = st.text_input(
            "Display name (shown as email sender)",
            value=env.get("EMAIL_DISPLAY_NAME") or env.get("GMAIL_DISPLAY_NAME", ""))
        with st.expander("Custom SMTP (only for providers not auto-detected)"):
            e_host = st.text_input("SMTP host", value=env.get("SMTP_HOST", ""))
            e_port = st.text_input("SMTP port", value=env.get("SMTP_PORT", ""))
            e_tls = st.checkbox("Use STARTTLS (usually port 587; unticked = SSL, port 465)",
                                value=env.get("SMTP_STARTTLS") == "1")
        if st.form_submit_button("💾 Save email settings"):
            write_env(paths["env"], {
                "EMAIL_ADDRESS": e_addr, "EMAIL_PASSWORD": e_pass,
                "EMAIL_DISPLAY_NAME": e_name,
                "SMTP_HOST": e_host, "SMTP_PORT": e_port,
                "SMTP_STARTTLS": "1" if e_tls else "0",
            })
            persist("credentials")
            st.success("Saved.")

    st.divider()
    st.subheader("Account — email login")
    with st.form("account"):
        if meta.get("email"):
            st.markdown(f"**Profile:** {meta['name']} — logs in as `{meta['email']}` 🔒")
        else:
            st.markdown(f"**Profile:** {meta['name']} — ⚠️ no email login yet. "
                        "Add an email and password below to log in with them "
                        "next time.")
        a_email = st.text_input("Login email", value=meta.get("email", ""))
        a_pw = st.text_input("New password (min. 6 characters, empty = keep current)",
                             type="password")
        a_current = st.text_input(
            "Current password — or access code, for profiles created before "
            "email login", type="password",
            disabled=not (meta.get("pw_hash") or meta.get("code_hash")))
        if st.form_submit_button("💾 Save login details"):
            authorized = (
                (not meta.get("pw_hash") and not meta.get("code_hash"))
                or check_password(meta, a_current)
                or (meta.get("code_hash") and hash_code(a_current) == meta["code_hash"])
            )
            other = find_by_email(index, a_email)
            if not authorized:
                st.error("Current password / access code is wrong.")
            elif not valid_email(a_email):
                st.error("That doesn't look like a valid email address.")
            elif other is not None and other != pid:
                st.error("Another profile already uses this email.")
            elif not a_pw and not meta.get("pw_hash"):
                st.error("Please set a password — it's required for email login.")
            elif a_pw and len(a_pw) < 6:
                st.error("Please choose a password of at least 6 characters.")
            else:
                meta["email"] = a_email.strip().lower()
                if a_pw:
                    meta["pw_salt"], meta["pw_hash"] = hash_password(a_pw)
                meta.pop("code_hash", None)  # superseded by email login
                index[pid] = meta
                save_index(index)
                persist("account")
                st.success(f"Saved — log in as {meta['email']} from now on.")
