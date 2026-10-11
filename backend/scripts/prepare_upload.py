"""Copy exactly the files that belong on GitHub into a clean folder, in batches of at most 90 files, for uploading in the browser
(no git needed). It applies the same rules as .gitignore, refuses to continue if anything looks like a secret, and never touches
your project: it only reads, and writes to C:\\tripai-upload (next to the project folder).

    python scripts/prepare_upload.py            # copy the files into batches
    python scripts/prepare_upload.py --check    # only run the secret checks

Then drag the CONTENTS of each upload-N folder (the backend, docs, frontend folders and the loose files) onto the GitHub upload page,
one batch at a time (GitHub accepts at most 100 files per upload). See docs/GITHUB.md.
"""
from __future__ import annotations

import fnmatch
import math
import re
import shutil
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
DEST = REPO.parent / "tripai-upload"
BATCH = 90
ALWAYS_SKIP_DIRS = {"node_modules", ".venv", "venv", ".next", "__pycache__", ".git", ".pytest_cache", ".ruff_cache", ".claude", "verify-results"}
RULES = [ln.strip() for ln in (REPO / ".gitignore").read_text(encoding="utf-8").splitlines() if ln.strip() and not ln.lstrip().startswith("#")]


def ignored(rel: str) -> bool:
    parts = rel.split("/")
    out = False
    for rule in RULES:  # last matching rule wins, like git
        neg = rule.startswith("!")
        pat = rule.lstrip("!").rstrip("/")
        hit = fnmatch.fnmatch(parts[-1], pat) or fnmatch.fnmatch(rel, pat) or any(fnmatch.fnmatch(p, pat) for p in parts[:-1])
        if hit:
            out = not neg
    return out


PATTERNS = {"Google key": r"AIza[0-9A-Za-z_\-]{35}", "OpenAI/OpenRouter key": r"sk-(or-v1-)?[A-Za-z0-9]{20,}", "GitHub token": r"gh[pousr]_[A-Za-z0-9]{30,}",
            "AWS key": r"AKIA[0-9A-Z]{16}", "private key": r"-----BEGIN [A-Z ]*PRIVATE KEY-----", "Slack token": r"xox[baprs]-[A-Za-z0-9-]{10,}"}


def real_secret_values() -> dict[str, str]:
    """Values from your own .env that must never appear in an uploaded file (names only are ever printed)."""
    found: dict[str, str] = {}
    env = REPO / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8", errors="ignore").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                v = v.split(" #")[0].strip().strip("'\"")
                k = k.strip()
                if re.search(r"KEY|SECRET|TOKEN|PASSWORD|SID", k) and len(v) >= 12 and not v.startswith("dev-only"):
                    found[k] = v
                elif k == "ADMIN_EMAILS":  # your own address: it belongs in your local .env only, never in an uploaded file
                    for i, addr in enumerate(a.strip() for a in v.split(",")):
                        if len(addr) >= 6 and addr.lower() != "admin@example.com":
                            found[f"ADMIN_EMAILS #{i + 1}"] = addr
                            name = re.sub(r"\d+$", "", addr.split("@")[0])  # alice2004 -> alice: the name alone identifies you too
                            if len(name) >= 5:
                                found[f"ADMIN_EMAILS #{i + 1} (name part)"] = name
    return found


def main() -> int:
    check_only = "--check" in sys.argv
    files = []
    for p in sorted(REPO.rglob("*")):
        rel_parts = p.relative_to(REPO).parts
        if not p.is_file() or any(part in ALWAYS_SKIP_DIRS for part in rel_parts):
            continue
        rel = p.relative_to(REPO).as_posix()
        if not ignored(rel):
            files.append((rel, p))
    secrets, problems = real_secret_values(), []
    for rel, p in files:
        if p.stat().st_size > 20_000_000:
            problems.append((rel, "is over 20 MB"))
        if rel == ".env" or (re.search(r"(^|/)\.env(\.|$)", rel) and not rel.endswith(".env.example")) or rel.endswith((".db", ".dump", ".pem", ".key")):
            problems.append((rel, "is a secret or database file"))
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for name, value in secrets.items():
            if value in text:
                problems.append((rel, f"contains the value of {name} from your .env"))
        for label, rx in PATTERNS.items():
            if re.search(rx, text):
                problems.append((rel, f"looks like it contains a {label}"))
    if problems:
        print("STOPPED. Nothing was copied. These files must not be uploaded:")
        for rel, why in problems:
            print(f"  {rel}: {why}")
        return 1
    if check_only:
        print(f"CHECK ONLY: {len(files)} files would be uploaded; no .env, database, backup or key file; none of your .env secret values "
              "or your admin address; no key-shaped text. Nothing was copied.")
        return 0
    if DEST.exists():
        if DEST.name != "tripai-upload":
            sys.exit("refusing to delete an unexpected folder")
        shutil.rmtree(DEST)
    n = math.ceil(len(files) / BATCH)
    for i, (rel, p) in enumerate(files):
        target = DEST / f"upload-{i // BATCH + 1}" / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(p, target)
    print(f"{len(files)} files copied to {DEST}, in {n} batches of at most {BATCH}:")
    for b in range(1, n + 1):
        print(f"  upload-{b}: {sum(1 for _ in (DEST / f'upload-{b}').rglob('*') if _.is_file())} files")
    print("Checked: no .env, database, backup or key file; none of your .env secret values; no key-shaped text.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
