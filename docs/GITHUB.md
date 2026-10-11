# Putting the project on GitHub (private) and running the verification

Why: Docker Desktop won't install on your laptop, so GitHub runs the checks instead (`.github/workflows/verify.yml`: PostgreSQL 16, Redis 7,
gunicorn, on a GitHub machine). You need a GitHub account and a browser. **Nothing from your machine is needed in the workflow**: it has no
secrets, no `.env`, and cannot reach any real provider.

## Before the first upload: what was checked

- **Git history.** There is none: the project folder is not a git repository (and git is not installed on this laptop), so no old commit can
  hold a secret. The first upload is the first history.
- **`.gitignore`** now covers, before any push: `.env` and `.env.*` (except `.env.example`), `*.db`, `*.db-wal`, `*.db-shm`, `*.db-journal`,
  `*.sqlite`, `*.dump`, `*.bak`, `backups/`, `pgbackups/`, `*.pem`, `*.key`, `.claude/` (it holds local permission settings), `verify-results/`,
  `node_modules/`, `.venv/`, `.next/`, `__pycache__/`, `*.log`. (The old file missed `backend/tripai.db-wal` and `-shm`.)
- **Secret scan** of the 292 files that would be uploaded: none of the key values in your `.env` (OpenRouter, Google, RapidAPI, RailRadar,
  JWT) appears in any of them, and nothing is shaped like a key. One thing to know: your `.env` uses the default development `JWT_SECRET`
  (it is also in `.env.example` and `config.py`, by design). That is fine for development, and production refuses to start with it.
- `.env.example` has no real value in it.

## Path A (recommended if you can): git

Git for Windows is a small installer (about 60 MB) and the only way `.gitignore` is enforced for you. If you would rather install nothing, use Path B.

1. On github.com: **+** (top right) → **New repository** → name `yatro` → **Private** → leave "Add a README", ".gitignore" and "license" **unticked** → **Create repository**.
2. In PowerShell:

```powershell
cd C:\tripai
git init
git add .
git status                       # read it: .env, *.db, .claude must NOT be listed
git ls-files | Select-String -Pattern '\.env$|\.db|\.dump|\.claude'     # must print nothing
git commit -m "Yatro: first commit"
git branch -M main
git remote add origin https://github.com/<your-username>/yatro.git
git push -u origin main
```

## Path B: nothing to install, upload in the browser

A plain upload doesn't read `.gitignore`, so **do not drag your project folder**: it would include `.env`, the database, `.venv` and thousands of files.
Instead run the helper, which copies only the allowed files and refuses to continue if anything looks like a secret:

```powershell
cd C:\tripai\backend
.\.venv\Scripts\python.exe scripts\prepare_upload.py
```

It makes `C:\tripai-upload\upload-1` … `upload-4` (at most 90 files each; GitHub accepts at most 100 per upload).

1. On github.com: **+** → **New repository** → name `yatro` → **Private** → leave README/.gitignore/license **unticked** → **Create repository**.
2. On the empty repository page click **uploading an existing file**.
3. Open `C:\tripai-upload\upload-1`, select **everything inside it** (`.github`, `backend`, `.env.example`, `.gitattributes`, `.gitignore`, …; turn on "Hidden items" in File Explorer's View menu so the dot-files show) and drag it onto the page. Wait until the file list is complete.
4. Under "Commit changes" keep **Commit directly to the main branch** and click **Commit changes**.
5. Repeat for `upload-2`, `upload-3` and `upload-4`: on the repository page choose **Add file → Upload files**, drag the contents, commit.
6. Check the repository page: you should **not** see `.env`, any `.db` file, or `.claude`. (`.env.example` is meant to be there.)

Every commit starts a verification run (the workflow runs on every push, and a newer run cancels an older one still going). The last batch's run is the one that matters, because it has all the files.

## Running and reading the verification

- It starts by itself on every push. To start it by hand: **Actions** tab → **verify** (left) → **Run workflow** → **Run workflow**.
- A run takes roughly 30 to 45 minutes. Open it: the **summary** page lists every check as PASS / FAIL / SKIP, grouped by VERIFY.md step.
- Every step in the log also prints one `PASS` / `FAIL` / `SKIP` line per check. A failing step is red, and the later steps still run.
- Download the results at the bottom of the run page, under **Artifacts** → **verify-results** (a zip): `results.md`, `results.json`, the server logs, `junit.xml`, the load report.
- Paste `results.md` (or the summary) back to me. I will then update the "Not verified" list in ADR 0008.

## Costs and limits

- A private repository on the free plan has **2,000 Actions minutes a month** on Linux machines. A run is about 30 to 45 minutes, so roughly 45 runs a month. Four upload commits in a row will start four runs (the earlier ones are cancelled by the next push, so in practice you pay for about one).
- It costs nothing else. There are no secrets to add. **Never put a real API key into `verify.yml` or into GitHub "Secrets" for this workflow**: the workflow is meant to run without any.

## If something looks wrong

- **The run can't start** (no Actions tab, or "workflows disabled"): Settings → Actions → General → Allow all actions.
- **A step fails**: that is the point of the exercise; paste me the failing lines and `results.md`. A failure on PostgreSQL that passes locally is a real difference between PostgreSQL and SQLite.
- **The safety step fails**: stop and tell me; nothing after it runs, by design.
