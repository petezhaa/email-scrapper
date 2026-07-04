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
from werkzeug.utils import secure_filename

from . import draft, scrape, send
from .schools import discover_orgs
from .config import (
    ROOT,
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

# Make sure the data folders exist before anything reads config.
bootstrap()

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


def _save_resume(upload) -> str:
    """Save an uploaded resume under its real filename and point config at it.

    Keeps a single PDF in the resume folder and updates paths.resume so the
    Setup page and the email attachment both use the actual file name.
    Returns the stored filename.
    """
    cfg = load_config()
    resume_dir = resolve(cfg["paths"]["resume"]).parent
    resume_dir.mkdir(parents=True, exist_ok=True)

    safe = secure_filename(upload.filename or "") or "resume.pdf"
    if not safe.lower().endswith(".pdf"):
        safe += ".pdf"

    # Only one resume lives in the folder — clear old PDFs before saving.
    for old in resume_dir.glob("*.pdf"):
        try:
            old.unlink()
        except OSError:
            pass

    dest = resume_dir / safe
    upload.save(str(dest))

    try:
        cfg["paths"]["resume"] = dest.relative_to(ROOT).as_posix()
    except ValueError:
        cfg["paths"]["resume"] = f"{resume_dir.name}/{safe}"
    save_config(cfg)
    return safe


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


def _persist_settings(vals: dict) -> None:
    """Persist Setup / Find settings (profile.md, config.yaml, .env, schools file).

    This is a PARTIAL merge: only keys present in `vals` are written, so the
    Setup page and the Find page can each save their own subset without wiping
    the other's fields. Values are normalized (strings / real bools).
    """
    cfg = load_config()

    # Profile fields — merge onto whatever is already saved.
    profile_keys = ("about", "experience", "interests", "writing_sample")
    if any(k in vals for k in profile_keys):
        fields = _load_profile_fields()
        for k in profile_keys:
            if k in vals:
                fields[k] = vals.get(k) or ""
        PROFILE_FIELDS.parent.mkdir(parents=True, exist_ok=True)
        PROFILE_FIELDS.write_text(json.dumps(fields, indent=2), encoding="utf-8")
        resolve(cfg["paths"]["profile"]).write_text(_assemble_profile(fields), encoding="utf-8")

    if "name" in vals:
        cfg["sender"]["name"] = (vals.get("name") or "").strip()
    if "phone" in vals:
        cfg["sender"]["phone"] = (vals.get("phone") or "").strip()
    if "verify_persons" in vals:
        cfg.setdefault("scraping", {})["verify_persons"] = bool(vals.get("verify_persons"))
    if "filter_by_research" in vals:
        cfg.setdefault("scraping", {})["filter_by_research"] = bool(vals.get("filter_by_research"))
    if "web_research" in vals:
        cfg.setdefault("drafting", {})["web_research"] = bool(vals.get("web_research"))
    if "quality_review" in vals:
        cfg.setdefault("drafting", {})["quality_review"] = bool(vals.get("quality_review"))
    save_config(cfg)

    # Gmail creds → .env (preserves the shared ANTHROPIC_API_KEY)
    env_updates = {}
    if "gmail_address" in vals:
        env_updates["GMAIL_ADDRESS"] = (vals.get("gmail_address") or "").strip()
    if "gmail_app_password" in vals:
        env_updates["GMAIL_APP_PASSWORD"] = (vals.get("gmail_app_password") or "").strip()
    if env_updates:
        update_env(env_updates)

    # Schools → directory_urls.txt
    if "schools" in vals:
        schools = vals.get("schools", "") or ""
        urls_path = resolve(cfg["paths"]["directory_urls"])
        urls_path.parent.mkdir(parents=True, exist_ok=True)
        header = "# Faculty-directory URLs, one per line.\n"
        cleaned = "\n".join(ln.strip() for ln in schools.splitlines() if ln.strip())
        urls_path.write_text(header + cleaned + "\n", encoding="utf-8")


@app.route("/settings", methods=["POST"])
def save_settings():
    f = request.form
    _persist_settings(
        {
            "about": f.get("about", ""),
            "experience": f.get("experience", ""),
            "interests": f.get("interests", ""),
            "writing_sample": f.get("writing_sample", ""),
            "name": f.get("name", ""),
            "phone": f.get("phone", ""),
            "gmail_address": f.get("gmail_address", ""),
            "gmail_app_password": f.get("gmail_app_password", ""),
            "schools": f.get("schools", ""),
            "verify_persons": bool(f.get("verify_persons")),
            "filter_by_research": bool(f.get("filter_by_research")),
            "web_research": bool(f.get("web_research")),
            "quality_review": bool(f.get("quality_review")),
        }
    )

    # Resume upload (optional)
    upload = request.files.get("resume")
    if upload and upload.filename:
        _save_resume(upload)

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
    profile_url = request.form.get("profile_url", "").strip()
    if targets_path.exists() and (email or profile_url):
        def keep(r: dict) -> bool:
            if email:
                return (r.get("email") or "").strip().lower() != email
            # Email-less contacts are identified by their profile URL instead.
            return (r.get("profile_url") or "").strip() != profile_url

        with _CSV_LOCK:
            with targets_path.open("r", encoding="utf-8", newline="") as fh:
                reader = csv.DictReader(fh)
                fieldnames = reader.fieldnames or scrape.CSV_FIELDS
                rows = [r for r in reader if keep(r)]
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
    if path.exists() and path.parent == drafts_dir:
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
        # Keep drafting while either kind of contact-finding job is still running.
        return draft.run(
            log=log,
            keep_going=lambda: _job_running("scrape") or _job_running("discover"),
        )
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
        # csv.reader (not a raw line count): quoted fields can span lines.
        with targets_path.open("r", encoding="utf-8", newline="") as fh:
            contact_count = max(0, sum(1 for _ in csv.reader(fh)) - 1)  # minus header
    return jsonify(jobs=out, draft_count=draft_count, contact_count=contact_count)


# ═══════════════════════════════════════════════════════════════════════════
# JSON API — consumed by the Next.js / shadcn frontend (proxied at /py/*).
# The classic server-rendered routes above still work; these just return JSON
# so the same pipeline backs both UIs.
# ═══════════════════════════════════════════════════════════════════════════
def _contacts_rows() -> list[dict]:
    cfg = load_config()
    targets_path = resolve(cfg["paths"]["targets"])
    if not targets_path.exists():
        return []
    with targets_path.open("r", encoding="utf-8", newline="") as fh:
        rows = list(csv.DictReader(fh))
    # Rows scraped before the category column default to research.
    for r in rows:
        if not (r.get("category") or "").strip():
            r["category"] = "research"
    return rows


@app.route("/api/state")
def api_state():
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
    return jsonify(
        fields={
            "about": fields.get("about", ""),
            "experience": fields.get("experience", ""),
            "interests": fields.get("interests", ""),
            "writing_sample": fields.get("writing_sample", ""),
        },
        name=cfg["sender"]["name"],
        phone=cfg["sender"].get("phone", ""),
        gmail_address=secrets["gmail_address"],
        gmail_app_password=secrets["gmail_app_password"],
        schools=schools,
        resume_ok=resume_path.exists(),
        resume_name=resume_path.name,
        verify_persons=bool(cfg.get("scraping", {}).get("verify_persons", False)),
        filter_by_research=bool(cfg.get("scraping", {}).get("filter_by_research", False)),
        web_research=bool(cfg.get("drafting", {}).get("web_research", True)),
        quality_review=bool(cfg.get("drafting", {}).get("quality_review", True)),
        api_key_ok=has_anthropic_credential(secrets),
    )


@app.route("/api/settings", methods=["POST"])
def api_save_settings():
    data = request.get_json(silent=True) or {}
    _persist_settings(data)
    return jsonify(ok=True)


@app.route("/api/resume", methods=["POST"])
def api_upload_resume():
    upload = request.files.get("resume")
    if not upload or not upload.filename:
        return jsonify(error="No file uploaded."), 400
    name = _save_resume(upload)
    return jsonify(ok=True, resume_name=name)


@app.route("/api/contacts")
def api_contacts():
    return jsonify(rows=_contacts_rows())


@app.route("/api/contacts/set-email", methods=["POST"])
def api_set_contact_email():
    data = request.get_json(silent=True) or {}
    cfg = load_config()
    targets_path = resolve(cfg["paths"]["targets"])
    profile_url = (data.get("profile_url") or "").strip()
    new_email = (data.get("email") or "").strip().lower()
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
    return jsonify(ok=True)


@app.route("/api/contacts/delete", methods=["POST"])
def api_delete_contact():
    data = request.get_json(silent=True) or {}
    cfg = load_config()
    targets_path = resolve(cfg["paths"]["targets"])
    email = (data.get("email") or "").strip().lower()
    profile_url = (data.get("profile_url") or "").strip()
    if targets_path.exists() and (email or profile_url):
        def keep(r: dict) -> bool:
            if email:
                return (r.get("email") or "").strip().lower() != email
            return (r.get("profile_url") or "").strip() != profile_url

        with _CSV_LOCK:
            with targets_path.open("r", encoding="utf-8", newline="") as fh:
                reader = csv.DictReader(fh)
                fieldnames = reader.fieldnames or scrape.CSV_FIELDS
                rows = [r for r in reader if keep(r)]
            with targets_path.open("w", encoding="utf-8", newline="") as fh:
                writer = csv.DictWriter(fh, fieldnames=fieldnames)
                writer.writeheader()
                writer.writerows(rows)
    return jsonify(ok=True)


@app.route("/api/drafts")
def api_drafts():
    cfg = load_config()
    drafts_dir = resolve(cfg["paths"]["drafts_dir"])
    items: list[dict] = []
    if drafts_dir.exists():
        for d in send.load_drafts(drafts_dir):
            items.append({
                "slug": d["_file"].stem,
                "to": d["to"],
                "name": d["name"],
                "subject": d["subject"],
                "status": d["status"],
                "source_url": d["source_url"],
                "body": d["body"],
            })
    counts: dict[str, int] = {}
    for it in items:
        counts[it["status"]] = counts.get(it["status"], 0) + 1
    return jsonify(items=items, counts=counts)


@app.route("/api/drafts/save", methods=["POST"])
def api_save_draft():
    data = request.get_json(silent=True) or {}
    cfg = load_config()
    drafts_dir = resolve(cfg["paths"]["drafts_dir"])
    slug = data.get("slug", "")
    path = drafts_dir / f"{slug}.md"
    if path.exists() and path.parent == drafts_dir:
        draft.write_draft_file(
            path,
            to=(data.get("to") or "").strip(),
            name=(data.get("name") or "").strip(),
            subject=(data.get("subject") or "").strip(),
            body=data.get("body", ""),
            source_url=(data.get("source_url") or "").strip(),
            status=(data.get("status") or "pending").strip(),
        )
        return jsonify(ok=True)
    return jsonify(error="Draft not found."), 404


@app.route("/api/drafts/delete", methods=["POST"])
def api_delete_draft():
    data = request.get_json(silent=True) or {}
    cfg = load_config()
    drafts_dir = resolve(cfg["paths"]["drafts_dir"])
    path = drafts_dir / f"{data.get('slug', '')}.md"
    if path.exists() and path.parent == drafts_dir:
        path.unlink()
    return jsonify(ok=True)


@app.route("/api/reset/contacts", methods=["POST"])
def api_reset_contacts():
    p = resolve(load_config()["paths"]["targets"])
    if p.exists():
        p.unlink()
    return jsonify(ok=True)


@app.route("/api/reset/drafts", methods=["POST"])
def api_reset_drafts():
    d = resolve(load_config()["paths"]["drafts_dir"])
    if d.exists():
        for f in d.glob("*.md"):
            f.unlink()
    return jsonify(ok=True)


def _category_kind(data: dict) -> tuple[str, str]:
    """Map the UI category to a stored value + discovery search style."""
    category = (data.get("category") or "research").strip().lower()
    if category not in ("research", "industry"):
        category = "research"
    kind = "academia" if category == "research" else "industry"
    return category, kind


@app.route("/api/run/scrape", methods=["POST"])
def api_run_scrape():
    data = request.get_json(silent=True) or {}
    category, _ = _category_kind(data)
    return jsonify(
        job_id=_start_job(lambda log: scrape.run(log=log, category=category), "scrape", "Scraping")
    )


@app.route("/api/run/discover", methods=["POST"])
def api_run_discover():
    data = request.get_json(silent=True) or {}
    query = (data.get("query") or "chemistry").strip()
    category, _ = _category_kind(data)
    find_emails = bool(data.get("find_emails"))
    cfg = load_config()
    model = cfg["model"]["name"]
    # Finders lean on web search, not deep reasoning — keep effort low so results
    # come back fast.
    effort = "low"

    # Exclude people already saved in this category so re-running "adds more"
    # instead of returning the same names.
    existing = [r for r in _contacts_rows() if (r.get("category") or "research") == category]
    exclude = [
        (f"{r.get('name','')} ({r.get('affiliation','')})".strip()
         if r.get("affiliation") else r.get("name", ""))
        for r in existing
    ]
    exclude = [e for e in exclude if e][-60:]

    def _fn(log):
        from .config import build_anthropic_client
        from .schools import find_academics, find_jobs
        client = build_anthropic_client()
        # Field search uses the web-search agent to return real individuals /
        # openings directly (robust to JS-rendered directories).
        if category == "industry":
            rows = find_jobs(query, client, model, log=log, effort=effort, exclude=exclude)
            noun = "job opening"
        else:
            rows = find_academics(query, client, model, log=log, effort=effort,
                                  exclude=exclude, enrich_emails=find_emails)
            noun = "researcher"
        if not rows:
            raise PipelineError(
                f"No {noun}s found for '{query}'. Try a broader or more specific term."
            )
        added = scrape.save_contacts(rows, category=category, log=log)
        log(f"Added {added} new contact(s) from {len(rows)} {noun}(s).")
        return {"added": added, "found": len(rows)}

    label = "Finding jobs" if category == "industry" else "Finding researchers"
    return jsonify(job_id=_start_job(_fn, "discover", f"{label}: {query}"))


@app.route("/api/run/draft", methods=["POST"])
def api_run_draft():
    def _draft_fn(log):
        return draft.run(
            log=log,
            keep_going=lambda: _job_running("scrape") or _job_running("discover"),
        )
    return jsonify(job_id=_start_job(_draft_fn, "draft", "Generating drafts"))


@app.route("/api/run/send", methods=["POST"])
def api_run_send():
    data = request.get_json(silent=True) or {}
    html_map = data.get("html_map") or {}
    return jsonify(
        job_id=_start_job(
            lambda log: send.send_approved(do_send=True, log=log, html_map=html_map),
            "send", "Sending approved",
        )
    )


@app.route("/api/job/<job_id>")
def api_job_status(job_id: str):
    return job_status(job_id)


@app.route("/api/jobs")
def api_jobs_status():
    return jobs_status()
