# Research Outreach Emailer — command runner
# Install just: winget install --id Casey.Just  (or: scoop install just)

# Windows PowerShell 5.1 has no `&&`, so recipes use `;` or separate lines
# (each line runs in its own shell; just stops on the first failing line).
set windows-shell := ["powershell.exe", "-NoProfile", "-Command"]

# List available recipes
default:
    just --list

# Install all dependencies (Python pipeline + web UI)
setup:
    pip install -r requirements.txt
    cd web; npm install

# Build the UI and start everything (Flask :5000 + Next :3000)
run:
    python run_web.py

# Iterative dev — run these in two terminals:
# API with auto-reload on :5000
dev-api:
    python -m flask --app src.app run --debug

# Next.js dev server on :3000 (proxies /py/* to the API)
dev-web:
    cd web; npm run dev

# Production build of the web UI (also type-checks)
build:
    cd web; npm run build

# Lint the web UI
lint:
    cd web; npm run lint

# Compile-check the Python pipeline and import the app
check:
    python -m py_compile src/app.py src/config.py src/schools.py src/scrape.py src/draft.py src/send.py src/emailfinder.py src/cli.py
    python -c "from src.app import app; print('backend OK')"

# Full verification: backend check + web build
verify: check build

# ── CLI pipeline (shares files with the web UI) ──────────────────────
# Scrape directory URLs -> data/targets.csv
scrape:
    python -m src.cli scrape

# Generate drafts (optionally: just draft 5)
draft limit="":
    python -m src.cli draft {{ if limit == "" { "" } else { "--limit " + limit } }}

# List drafts and their statuses
status:
    python -m src.cli status

# DRY RUN — preview what would be sent
send:
    python -m src.cli send

# Actually send approved drafts from your Gmail
send-real:
    python -m src.cli send --send
