"""
Optional cloud persistence for hosted deployments (Hugging Face Spaces).

Free hosts wipe local files on every restart/rebuild, which would erase
all profiles, credentials and contact history. When these env vars are
set (Space settings → Variables and secrets):

  HF_TOKEN      — a Hugging Face WRITE token (secret!)
  HF_DATA_REPO  — dataset repo for the state, e.g. "yourname/liege-housing-data"

app.py restores all state from that dataset on startup and backs it up
after every change. The dataset is created PRIVATE automatically — it
contains credentials and contact history, never make it public.

Without the env vars everything is a no-op and the app is purely local.

CLI (run on your own machine to seed or pull the cloud state):
  HF_TOKEN=hf_xxx HF_DATA_REPO=you/liege-housing-data python hf_sync.py backup
  HF_TOKEN=hf_xxx HF_DATA_REPO=you/liege-housing-data python hf_sync.py restore
"""

import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).parent

# Everything that must survive a restart: per-user profiles plus the
# owner's original single-user files (kept at their legacy locations).
TRACKED = [
    "profiles",
    "search_config.json",
    ".env",
    "kotaliege_automated/contacted.json",
    "kotaliege_automated/session_state.json",
    "kotaliege_automated/message.txt",
    "logement_uliege/processed.json",
    "logement_uliege/new_contacts.json",
    "logement_uliege/message.txt",
    "logement_uliege/whatsapp.html",
]


def enabled() -> bool:
    return bool(os.environ.get("HF_TOKEN") and os.environ.get("HF_DATA_REPO"))


def _repo_and_api():
    from huggingface_hub import HfApi
    repo = os.environ["HF_DATA_REPO"]
    api = HfApi(token=os.environ["HF_TOKEN"])
    api.create_repo(repo, repo_type="dataset", private=True, exist_ok=True)
    return repo, api


def restore() -> str:
    """Pull the dataset state into the local files (overwrites them)."""
    from huggingface_hub import snapshot_download
    repo, _ = _repo_and_api()
    snap = Path(snapshot_download(repo_id=repo, repo_type="dataset",
                                  token=os.environ["HF_TOKEN"]))
    n = 0
    for f in snap.rglob("*"):
        if f.is_file() and f.name != ".gitattributes":
            dest = ROOT / f.relative_to(snap)
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(f, dest)
            n += 1
    return f"{n} files restored"


def backup() -> str:
    """Push the current local state to the dataset (one commit)."""
    repo, api = _repo_and_api()
    with tempfile.TemporaryDirectory() as td:
        stage = Path(td)
        n = 0
        for rel in TRACKED:
            src = ROOT / rel
            files = ([f for f in src.rglob("*")
                      if f.is_file() and "__pycache__" not in f.parts]
                     if src.is_dir() else [src] if src.is_file() else [])
            for f in files:
                dest = stage / f.relative_to(ROOT)
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(f, dest)
                n += 1
        if n:
            api.upload_folder(folder_path=stage, repo_id=repo, repo_type="dataset",
                              commit_message="state backup")
    return f"{n} files backed up"


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd not in ("backup", "restore"):
        sys.exit(__doc__)
    if not enabled():
        sys.exit("Set HF_TOKEN and HF_DATA_REPO first (see header of this file).")
    print(restore() if cmd == "restore" else backup())
