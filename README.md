# Research Outreach Emailer

Finds professor/postdoc/research-scientist contacts from faculty directory and
company team pages, uses Claude to draft a personalized research-inquiry email
for each (in *your* voice, grounded in *your* background), lets **you review
and approve every email**, then sends them from your Gmail with your resume
attached.

```
Setup (your info + schools)  →  Scrape  →  Generate drafts  →  Review/approve  →  Send
```

Nothing is ever sent until you mark an email **approved**. There's rate limiting
and a per-run cap so accounts don't get flagged for spam.

---

## Run it (local web app)

```bash
pip install -r requirements.txt
python run_app.py          # or double-click start.bat on Windows
```

Opens at <http://127.0.0.1:5000>. Everything runs only on your own machine —
your resume, Gmail credentials, and scraped data never leave it.

1. Fill in the **Setup** tab: your info, your Gmail + App Password, resume, and
   the organizations/URLs to scrape (or use the web-search discovery box).
2. Work left to right: **Scrape** → **Contacts** → **Generate drafts** →
   review/approve → **Send**.

Before first run, copy `.env.example` to `.env` and set your API credentials
(`ANTHROPIC_API_KEY`, and `ANTHROPIC_BASE_URL` if you use a gateway).

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
- **Respect each site.** The scraper can check `robots.txt` and identifies itself.
- **Don't mass-blast.** `max_per_run` and `delay_seconds` in `config.yaml` keep
  volume human. Personalized, reviewed outreach works; spam gets you flagged.
- **Personal data is gitignored** — `.env`, `data/targets.csv`,
  `data/profile_fields.json`, `drafts/`, `sent/`, and your resume.
- **You are responsible** for who you contact and what you send. Review every draft.

## Layout
| Path | What |
|------|------|
| `run_app.py` / `start.bat` | launch the web app in a browser |
| `src/app.py` | the web server + routes |
| `src/templates`, `src/static` | the UI (HTML/CSS/JS) |
| `src/scrape.py` `draft.py` `send.py` | the pipeline (shared by UI + CLI) |
| `src/cli.py` | command-line entry point |
| `config.yaml` | model, paths, rate limits |
| `.env` | API key + Gmail creds (gitignored) |
| `data/` `drafts/` `sent/` `resume/` | profile, contacts, emails, log, attachment |
