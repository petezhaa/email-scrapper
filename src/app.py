"""Local web UI for the research outreach emailer.

Each user runs this on their own machine (`python run_app.py`), which opens a
browser at http://127.0.0.1:5000. Their resume, Gmail credentials, and data
stay local. The Anthropic API key is pre-set in .env by whoever shares the tool.

Flow in the UI:
    Setup  → enter your info + schools, save, then "Scrape schools"
    Contacts → review scraped professors, then "Generate drafts"
    Drafts → edit + approve each email, then "Send approved"
"""
from __future__ import annotations

import csv
import json
import threading
import uuid

from flask import Flask, jsonify, redirect, render_template, request, url_for

from . import draft, scrape, send
from .schools import discover_orgs
from .config import (
    PipelineError,
    bootstrap,
    bundled_dir,
    has_anthropic_credential,
    load_config,
    load_secrets,
    resolve,
    save_config,
    update_env,
)

# Seed the writable data folder (no-op in dev) before anything reads config.
bootstrap()

# Explicit template/static folders so they resolve both in dev and when the app
# is packaged as a single executable (PyInstaller unpacks them elsewhere).
app = Flask(
    __name__,
    template_folder=str(bundled_dir("templates")),
    static_folder=str(bundled_dir("static")),
)

# In-memory job registry for long-running actions (scrape / draft / send).
# Single local user, so a plain dict is fine.
JOBS: dict[str, dict] = {}
_JOBS_LOCK = threading.Lock()   # guards check-and-start to prevent duplicate jobs
_CSV_LOCK = threading.Lock()    # guards all read-modify-write operations on targets.csv

PROFILE_FIELDS = resolve("data/profile_fields.json")


# ───────────────────────── background jobs ─────────────────────────
def _job_running(kind: str) -> bool:
    return any(j["kind"] == kind and j["status"] == "running" for j in JOBS.values())


def _start_job(fn, kind: str, label: str) -> str:
    with _JOBS_LOCK:
        # Don't start a second job of the same kind on top of a running one.
        if _job_running(kind):
            for jid, j in JOBS.items():
                if j["kind"] == kind and j["status"] == "running":
                    return jid
        job_id = uuid.uuid4().hex
        JOBS[job_id] = {"status": "running", "log": [], "result": None,
                        "error": None, "kind": kind, "label": label}

    def worker() -> None:
        def log(msg) -> None:
            JOBS[job_id]["log"].append(str(msg))

        try:
            JOBS[job_id]["result"] = fn(log)
            JOBS[job_id]["status"] = "done"
        except PipelineError as e:
            JOBS[job_id]["error"] = str(e)
            JOBS[job_id]["status"] = "error"
        except Exception as e:  # pragma: no cover - safety net
            JOBS[job_id]["error"] = f"Unexpected error: {e}"
            JOBS[job_id]["status"] = "error"

    threading.Thread(target=worker, daemon=True).start()
    return job_id


# ───────────────────────── profile helpers ─────────────────────────
def _load_profile_fields() -> dict:
    if PROFILE_FIELDS.exists():
        return json.loads(PROFILE_FIELDS.read_text(encoding="utf-8"))
    return {}


def _assemble_profile(fields: dict) -> str:
    """Turn the structured form fields into the profile.md the drafter reads."""
    return f"""# My profile

## Who I am & what I'm looking for
{fields.get('about', '').strip()}

## Research experience & skills
{fields.get('experience', '').strip()}

## Research interests / the kind of lab I want & why
{fields.get('interests', '').strip()}

## Writing voice (sample of my own writing)
{fields.get('writing_sample', '').strip()}

## Hard constraints (the drafter must follow)
- Keep emails under ~180 words.
- Never invent experience, publications, or skills not listed above.
- No flattery clichés.
- One specific, sincere connection to the recipient's research per email.
- Plain, direct tone.
"""


# ───────────────────────────── routes ──────────────────────────────
@app.route("/")
def setup():
    cfg = load_config()
    secrets = load_secrets()
    fields = _load_profile_fields()
    urls_path = resolve(cfg["paths"]["directory_urls"])
    schools = ""
    if urls_path.exists():
        schools = "\n".join(
            ln for ln in urls_path.read_text(encoding="utf-8").splitlines()
            if ln.strip() and not ln.strip().startswith("#")
        )
    resume_path = resolve(cfg["paths"]["resume"])
    return render_template(
        "setup.html",
        active="setup",
        fields=fields,
        name=cfg["sender"]["name"],
        phone=cfg["sender"].get("phone", ""),
        gmail_address=secrets["gmail_address"],
        gmail_app_password=secrets["gmail_app_password"],
        schools=schools,
        resume_ok=resume_path.exists(),
        resume_name=resume_path.name,
        respect_robots=bool(cfg.get("scraping", {}).get("respect_robots", True)),
        verify_persons=bool(cfg.get("scraping", {}).get("verify_persons", False)),
        filter_by_research=bool(cfg.get("scraping", {}).get("filter_by_research", False)),
        web_research=bool(cfg.get("drafting", {}).get("web_research", True)),
        quality_review=bool(cfg.get("drafting", {}).get("quality_review", True)),
        api_key_ok=has_anthropic_credential(secrets),
        saved=request.args.get("saved"),
    )


@app.route("/settings", methods=["POST"])
def save_settings():
    f = request.form
    fields = {
        "about": f.get("about", ""),
        "experience": f.get("experience", ""),
        "interests": f.get("interests", ""),
        "writing_sample": f.get("writing_sample", ""),
    }
    PROFILE_FIELDS.parent.mkdir(parents=True, exist_ok=True)
    PROFILE_FIELDS.write_text(json.dumps(fields, indent=2), encoding="utf-8")

    cfg = load_config()
    # Assemble + write profile.md the drafter reads.
    resolve(cfg["paths"]["profile"]).write_text(_assemble_profile(fields), encoding="utf-8")

    # Name + scraping toggle → config.yaml
    cfg["sender"]["name"] = f.get("name", "").strip()
    cfg["sender"]["phone"] = f.get("phone", "").strip()
    cfg.setdefault("scraping", {})["respect_robots"] = bool(f.get("respect_robots"))
    cfg.setdefault("scraping", {})["verify_persons"] = bool(f.get("verify_persons"))
    cfg.setdefault("scraping", {})["filter_by_research"] = bool(f.get("filter_by_research"))
    cfg.setdefault("drafting", {})["web_research"] = bool(f.get("web_research"))
    cfg.setdefault("drafting", {})["quality_review"] = bool(f.get("quality_review"))
    save_config(cfg)

    # Gmail creds → .env (preserves the shared ANTHROPIC_API_KEY)
    update_env(
        {
            "GMAIL_ADDRESS": f.get("gmail_address", "").strip(),
            "GMAIL_APP_PASSWORD": f.get("gmail_app_password", "").strip(),
        }
    )

    # Schools → directory_urls.txt
    schools = f.get("schools", "")
    urls_path = resolve(cfg["paths"]["directory_urls"])
    urls_path.parent.mkdir(parents=True, exist_ok=True)
    header = "# Faculty-directory URLs, one per line.\n"
    cleaned = "\n".join(ln.strip() for ln in schools.splitlines() if ln.strip())
    urls_path.write_text(header + cleaned + "\n", encoding="utf-8")

    # Resume upload (optional)
    upload = request.files.get("resume")
    if upload and upload.filename:
        resume_path = resolve(cfg["paths"]["resume"])
        resume_path.parent.mkdir(parents=True, exist_ok=True)
        upload.save(str(resume_path))

    return redirect(url_for("setup", saved=1))


@app.route("/contacts")
def contacts():
    cfg = load_config()
    targets_path = resolve(cfg["paths"]["targets"])
    rows: list[dict] = []
    if targets_path.exists():
        with targets_path.open("r", encoding="utf-8", newline="") as fh:
            rows = list(csv.DictReader(fh))
    return render_template("contacts.html", active="contacts", rows=rows)


@app.route("/contacts/set-email", methods=["POST"])
def set_contact_email():
    """Set the email for a no-email contact identified by its profile_url."""
    cfg = load_config()
    targets_path = resolve(cfg["paths"]["targets"])
    profile_url = request.form.get("profile_url", "").strip()
    new_email = request.form.get("email", "").strip().lower()
    if targets_path.exists() and profile_url and new_email and "@" in new_email:
        with _CSV_LOCK:
            with targets_path.open("r", encoding="utf-8", newline="") as fh:
                reader = csv.DictReader(fh)
                fieldnames = reader.fieldnames or scrape.CSV_FIELDS
                rows = list(reader)
            for row in rows:
                if (row.get("profile_url") or "").strip() == profile_url:
                    row["email"] = new_email
                    break
            with targets_path.open("w", encoding="utf-8", newline="") as fh:
                writer = csv.DictWriter(fh, fieldnames=fieldnames)
                writer.writeheader()
                writer.writerows(rows)
    return redirect(url_for("contacts"))


@app.route("/contacts/delete", methods=["POST"])
def delete_contact():
    cfg = load_config()
    targets_path = resolve(cfg["paths"]["targets"])
    email = request.form.get("email", "").strip().lower()
    if targets_path.exists() and email:
        with _CSV_LOCK:
            with targets_path.open("r", encoding="utf-8", newline="") as fh:
                reader = csv.DictReader(fh)
                fieldnames = reader.fieldnames or scrape.CSV_FIELDS
                rows = [r for r in reader if (r.get("email") or "").strip().lower() != email]
            with targets_path.open("w", encoding="utf-8", newline="") as fh:
                writer = csv.DictWriter(fh, fieldnames=fieldnames)
                writer.writeheader()
                writer.writerows(rows)
    return redirect(url_for("contacts"))


@app.route("/drafts")
def drafts():
    cfg = load_config()
    drafts_dir = resolve(cfg["paths"]["drafts_dir"])
    items: list[dict] = []
    if drafts_dir.exists():
        for d in send.load_drafts(drafts_dir):
            items.append(
                {
                    "slug": d["_file"].stem,
                    "to": d["to"],
                    "name": d["name"],
                    "subject": d["subject"],
                    "status": d["status"],
                    "source_url": d["source_url"],
                    "body": d["body"],
                }
            )
    counts: dict[str, int] = {}
    for it in items:
        counts[it["status"]] = counts.get(it["status"], 0) + 1
    return render_template("drafts.html", active="drafts", items=items, counts=counts)


@app.route("/drafts/save", methods=["POST"])
def save_draft():
    cfg = load_config()
    drafts_dir = resolve(cfg["paths"]["drafts_dir"])
    slug = request.form.get("slug", "")
    path = drafts_dir / f"{slug}.md"
    if path.exists():
        draft.write_draft_file(
            path,
            to=request.form.get("to", "").strip(),
            name=request.form.get("name", "").strip(),
            subject=request.form.get("subject", "").strip(),
            body=request.form.get("body", ""),
            source_url=request.form.get("source_url", "").strip(),
            status=request.form.get("status", "pending").strip(),
        )
    return redirect(url_for("drafts"))


@app.route("/reset/contacts", methods=["POST"])
def reset_contacts():
    p = resolve(load_config()["paths"]["targets"])
    if p.exists():
        p.unlink()
    return redirect(url_for("setup", saved=1))


@app.route("/reset/drafts", methods=["POST"])
def reset_drafts():
    d = resolve(load_config()["paths"]["drafts_dir"])
    if d.exists():
        for f in d.glob("*.md"):
            f.unlink()
    return redirect(url_for("setup", saved=1))


@app.route("/drafts/delete", methods=["POST"])
def delete_draft():
    cfg = load_config()
    drafts_dir = resolve(cfg["paths"]["drafts_dir"])
    path = drafts_dir / f"{request.form.get('slug', '')}.md"
    if path.exists() and path.parent == drafts_dir:
        path.unlink()
    return redirect(url_for("drafts"))


# Long-running actions → background jobs. The browser starts them and moves on;
# a persistent status bar polls /jobs, so drafting/sending keep running while you
# navigate and review.
@app.route("/run/scrape", methods=["POST"])
def run_scrape():
    return jsonify(job_id=_start_job(lambda log: scrape.run(log=log), "scrape", "Scraping"))


@app.route("/run/discover", methods=["POST"])
def run_discover():
    query = (request.form.get("query") or "chemistry research scientist").strip()
    cfg = load_config()
    model = cfg["model"]["name"]

    def _fn(log):
        from .config import build_anthropic_client
        client = build_anthropic_client()
        urls = discover_orgs(query, client, model, log=log)
        if not urls:
            raise PipelineError(
                f"No organizations found for '{query}'. Try a more specific search term."
            )
        log(f"Discovered {len(urls)} organization(s) — now scraping for contacts...")
        return scrape.run(log=log, extra_urls=urls)

    return jsonify(job_id=_start_job(_fn, "discover", f"Finding contacts: {query}"))


@app.route("/run/draft", methods=["POST"])
def run_draft():
    def _draft_fn(log):
        return draft.run(log=log, keep_going=lambda: _job_running("scrape"))
    return jsonify(job_id=_start_job(_draft_fn, "draft", "Generating drafts"))


@app.route("/run/send", methods=["POST"])
def run_send():
    return jsonify(
        job_id=_start_job(lambda log: send.send_approved(do_send=True, log=log), "send", "Sending approved")
    )


@app.route("/job/<job_id>")
def job_status(job_id: str):
    job = JOBS.get(job_id)
    if not job:
        return jsonify(status="unknown"), 404
    return jsonify(status=job["status"], log=job["log"], error=job["error"], result=job["result"])


@app.route("/jobs")
def jobs_status():
    """Status of recent jobs for the persistent status bar (newest last)."""
    out = []
    for jid, j in list(JOBS.items())[-6:]:
        out.append(
            {
                "id": jid,
                "kind": j["kind"],
                "label": j["label"],
                "status": j["status"],
                "last": j["log"][-1] if j["log"] else "",
                "steps": len(j["log"]),
                "error": j["error"],
            }
        )
    # current counts so the Contacts/Drafts pages can offer a non-destructive refresh
    cfg = load_config()
    drafts_dir = resolve(cfg["paths"]["drafts_dir"])
    draft_count = len(list(drafts_dir.glob("*.md"))) if drafts_dir.exists() else 0
    targets_path = resolve(cfg["paths"]["targets"])
    contact_count = 0
    if targets_path.exists():
        with targets_path.open("r", encoding="utf-8", newline="") as fh:
            contact_count = max(0, sum(1 for _ in fh) - 1)  # minus header row
    return jsonify(jobs=out, draft_count=draft_count, contact_count=contact_count)
