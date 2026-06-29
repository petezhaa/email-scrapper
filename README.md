# Research Outreach Emailer

Finds professor/postdoc contacts from faculty directory pages, uses Claude to
draft a personalized research-inquiry email for each (in *your* voice, grounded
in *your* background), lets **you review and approve every email**, then sends
them from your Gmail with your resume attached.

```
Setup (your info + schools)  →  Scrape  →  Generate drafts  →  Review/approve  →  Send
```

Nothing is ever sent until you mark an email **approved**. There's rate limiting
and a per-run cap so accounts don't get flagged for spam.

Three ways to run it, same engine underneath:
- **Standalone desktop app** — a single `.exe`/`.app`. Best for friends: no Python, no folder.
- **Local web app** — `python run_app.py`, opens in your browser. Good if you have Python.
- **Command line** — same pipeline, scriptable.

---

## For your friends: the standalone desktop app

A friend double-clicks one file and an app window opens. No Python, no folder,
no setup beyond filling in the form.

### You build it once (per OS)
A Windows `.exe` runs only on Windows; build the Mac `.app` on a Mac.

```bash
pip install -r requirements.txt -r requirements-desktop.txt

# 1. Put your shared Anthropic key in .env (friends use your key):
#    copy .env.example to .env, set ANTHROPIC_API_KEY, leave the Gmail lines blank.
# 2. Build:
python build_exe.py
```
Output:
- Windows → `dist/ResearchOutreach.exe`
- macOS → `dist/ResearchOutreach.app`

Send that one file to your friends.

### What a friend does
1. Double-click `ResearchOutreach.exe` (Windows may show a SmartScreen
   "unknown publisher" warning the first time → **More info → Run anyway**).
   On Mac: right-click the `.app` → **Open** the first time.
2. Fill in the **Setup** tab: their info, their Gmail + App Password, resume, and
   the school URLs to scrape.
3. Work left to right: **Scrape** → **Contacts** → **Generate drafts** →
   review/approve → **Send**.

The app writes a `ResearchOutreach-data` folder next to itself (their config,
drafts, sent log). Their resume and Gmail password never leave their machine;
your API key is bundled in the app.

> **Keep an Anthropic spend limit on** (console → Limits). Anyone with the app
> can spend against your key, and a key bundled in an app can be extracted — the
> spend cap is your safety net. Only share with people you trust.
>
> **Windows needs the Edge WebView2 Runtime** for the window to render. It ships
> with Windows 10/11 and Edge, so almost everyone has it; if a friend's window
> is blank, they can install it free from Microsoft ("WebView2 Runtime").

---

## Local web app (if you have Python)

```bash
pip install -r requirements.txt
python run_app.py          # or double-click start.bat on Windows
```
Opens at <http://127.0.0.1:5000>. Same three-tab flow as the desktop app. Runs
only on your own machine. Want the native-window experience without building an
exe? `python desktop.py` opens it in an app window instead of a browser tab.

---

## Command line (optional)

```bash
python -m src.cli scrape            # directory URLs -> data/targets.csv
python -m src.cli draft [--limit N] # targets.csv -> drafts/
python -m src.cli status            # list drafts + statuses
python -m src.cli send              # DRY RUN (preview approved)
python -m src.cli send --send       # actually send approved
```
CLI and the UI share the same files (`drafts/*.md` with a `status:` header), so
you can mix them.

---

## Setup details & safety
- **Gmail App Password** (not your normal password): turn on 2-Step Verification,
  then create one at <https://myaccount.google.com/apppasswords>.
- **Respect each site.** The scraper checks `robots.txt` and identifies itself.
- **Don't mass-blast.** `max_per_run` and `delay_seconds` in `config.yaml` keep
  volume human. Personalized, reviewed outreach works; spam gets you flagged.
- **Personal data is gitignored** — `.env`, `data/targets.csv`,
  `data/profile_fields.json`, `drafts/`, `sent/`, your resume, and build output.
- **You are responsible** for who you contact and what you send. Review every draft.

## Layout
| Path | What |
|------|------|
| `build_exe.py` | builds the standalone `.exe` / `.app` |
| `desktop.py` | native-window entry point (what gets packaged) |
| `run_app.py` / `start.bat` | launch the web app in a browser |
| `src/app.py` | the web server + routes |
| `src/templates`, `src/static` | the UI (HTML/CSS/JS) |
| `src/scrape.py` `draft.py` `send.py` | the pipeline (shared by UI + CLI) |
| `src/cli.py` | command-line entry point |
| `config.yaml` | model, paths, rate limits |
| `.env` | shared API key + Gmail creds (gitignored; bundled into the app) |
| `data/` `drafts/` `sent/` `resume/` | profile, contacts, emails, log, attachment |
