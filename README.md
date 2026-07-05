# Research Outreach Emailer

Finds professor/postdoc/research-scientist contacts from faculty directory and
company team pages, uses Claude to draft a personalized research-inquiry email
for each (in *your* voice, grounded in *your* background), lets **you review
and approve every email**, then sends them from your Gmail with your resume
attached.

```
Setup (you + Gmail + resume)  →  Find (Academia / Industry)  →  Review contacts  →  Generate drafts  →  Approve  →  Send
```

Nothing is ever sent until you mark an email **approved**. There's rate limiting
and a per-run cap so accounts don't get flagged for spam.

---

## Run it (local web app)

A Next.js + shadcn/ui frontend (Tailwind + React Email) over a local Python
pipeline. The "compose" preview renders the **real email HTML** with React
Email, and mail still sends from your own Gmail.

```bash
pip install -r requirements.txt   # Python pipeline
cd web && npm install && cd ..    # UI dependencies (first time only)
python run_web.py                 # builds the UI, starts both processes
```

With [`just`](https://github.com/casey/just) installed
(`winget install --id Casey.Just`), that's `just setup` once, then `just run`.
`just` alone lists every recipe (build, lint, check, the CLI pipeline, …).

Opens at <http://127.0.0.1:3000>. It runs two local processes — the Flask
pipeline **API** on `:5000` and the Next.js UI on `:3000`, which proxies `/py/*`
to Flask (no CORS to configure). For iterative UI work, run `just dev-web`
alongside `just dev-api` (or the underlying `cd web && npm run dev` +
`python -m flask --app src.app run --debug`) instead.

Everything runs only on your own machine — your resume, Gmail credentials, and
scraped data never leave it.

1. Fill in the **Setup** tab: your info, your Gmail + App Password, and resume.
   This isn't optional — drafting refuses to run on an empty profile/resume so
   the AI never has to invent credentials for you.
2. On the **Find** tab, search **Academia** or **Industry** for a field (or paste
   specific URLs/org names). Contacts are found and tagged by category.
3. Work left to right: **Setup** → **Find** → **Contacts** (review/filter by
   Academia/Industry) → **Generate drafts** → review/approve → **Send**.

Before first run, copy `.env.example` to `.env` and set your API credentials
(`ANTHROPIC_API_KEY`, and `ANTHROPIC_BASE_URL` if you use a gateway).

---

## Command line (optional)

```bash
python -m src.cli scrape            # directory URLs -> data/targets.csv    (just scrape)
python -m src.cli draft [--limit N] # targets.csv -> drafts/                (just draft [N])
python -m src.cli status            # list drafts + statuses                (just status)
python -m src.cli send              # DRY RUN (preview approved)            (just send)
python -m src.cli send --send       # actually send approved                (just send-real)
```
CLI and the UI share the same files (`drafts/*.md` with a `status:` header), so
you can mix them.

---

## Setup details & safety
- **Gmail App Password** (not your normal password): turn on 2-Step Verification,
  then create one at <https://myaccount.google.com/apppasswords>.
- **Respect each site.** The scraper can check `robots.txt` and identifies itself.
- **Don't mass-blast.** `config.yaml` keeps volume human: `daily_cap` (40/day),
  `max_per_run`, and a jittered delay between sends (`delay_seconds`..3× —
  30–90s by default, because 50 identical-attachment emails at a fixed 5s
  interval is a classic bulk-sender signature). Personalized, reviewed outreach
  works; spam gets you flagged.
- **Sends are crash-safe.** A draft is marked `sent` the moment Gmail accepts
  it, so an interrupted run can't re-email the same person. Follow-ups thread
  under the original email (`In-Reply-To`) and skip the resume re-attach.
- **Personal data is gitignored** — `.env`, `data/targets.csv`,
  `data/profile_fields.json`, `drafts/`, `sent/`, and your resume.
- **You are responsible** for who you contact and what you send. Review every draft.

## Layout
| Path | What |
|------|------|
| `justfile` | `just` recipes for the commands in this README (`just` to list) |
| `run_web.py` | launch the Next.js UI + Python API together |
| `src/app.py` | Flask `/api/*` JSON API for the frontend (localhost-only, Host/Origin-guarded) |
| `web/` | the Next.js + shadcn/ui frontend |
| `web/src/app/{setup,find,contacts,drafts,sent}` | the pages (Find = Academia/Industry search) |
| `web/emails/outreach.tsx` | the React Email template (real send HTML) |
| `web/next.config.ts` | proxies `/py/*` → Flask `:5000/api/*` |
| `src/scrape.py` `schools.py` `draft.py` `send.py` | the pipeline (shared with the CLI) |
| `src/cli.py` | command-line entry point |
| `config.yaml` | model, paths, rate limits |
| `.env` | API key + Gmail creds (gitignored) |
| `data/` `drafts/` `sent/` `resume/` | profile, contacts, emails, log, attachment |
