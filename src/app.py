"""Local JSON API for the research outreach emailer.

Runs on http://127.0.0.1:5000 and backs the Next.js frontend (started together
by `python run_web.py`), which proxies /py/* here. Everything stays on the
user's machine; the Anthropic API key is pre-set in .env by whoever shares it.

Endpoints live under /api/* — settings, contacts, drafts, and the background
scrape / find / draft / send jobs. The pipeline itself is in scrape.py,
schools.py, draft.py, and send.py (shared with the CLI in cli.py).
"""
from __future__ import annotations

import csv
import json
import threading
import traceback
import uuid
from urllib.parse import urlsplit

from flask import Flask, jsonify, request
from werkzeug.utils import secure_filename

from . import draft, scrape, send
from .config import (
    ROOT,
    PipelineError,
    bootstrap,
    has_anthropic_credential,
    load_config,
    load_secrets,
    resolve,
    save_config,
    update_env,
)
from .scrape import TARGETS_LOCK

# Make sure the data folders exist before anything reads config.
bootstrap()

# Pure JSON API for the Next.js frontend (proxied at /py/*). No templates/static.
app = Flask(__name__, static_folder=None)

# In-memory job registry for long-running actions (scrape / draft / send).
# Single local user, so a plain dict is fine.
JOBS: dict[str, dict] = {}
_JOBS_LOCK = threading.RLock()  # guards ALL reads/writes of JOBS (workers + routes)
# targets.csv reads/writes are guarded by TARGETS_LOCK, shared with scrape.py.

PROFILE_FIELDS = resolve("data/profile_fields.json")

# Loopback names this server will answer to (it only binds 127.0.0.1).
_ALLOWED_HOSTS = ("127.0.0.1", "localhost")


@app.before_request
def _reject_non_local():
    """Refuse anything that isn't the local frontend talking to us.

    - The Host header must be loopback: blocks DNS-rebinding pages whose
      attacker domain resolves to 127.0.0.1.
    - Mutating requests carrying an Origin header must come from a localhost
      origin (any port): blocks cross-site fetch CSRF. The Next dev proxy
      usually sends no Origin at all, which passes.
    - POSTs must be JSON (except the /api/resume multipart upload): a
      cross-site HTML form can't set application/json, so form posts die here.
    """
    host = (request.host or "").partition(":")[0].strip().lower()
    if host not in _ALLOWED_HOSTS:
        return jsonify(error="Forbidden: non-local Host."), 403
    if request.method in ("POST", "DELETE"):
        origin = request.headers.get("Origin")
        if origin:
            origin_host = (urlsplit(origin).hostname or "").lower()
            if origin_host not in _ALLOWED_HOSTS:
                return jsonify(error="Forbidden: cross-site Origin."), 403
        if request.method == "POST" and request.path != "/api/resume" and not request.is_json:
            return jsonify(error="Forbidden: JSON body required."), 403
    return None


# ───────────────────────── background jobs ─────────────────────────
def _job_running(kind: str) -> bool:
    with _JOBS_LOCK:
        return any(j["kind"] == kind and j["status"] == "running" for j in JOBS.values())


def _start_job(fn, kind: str, label: str) -> tuple[str, bool]:
    """Start a background job; returns (job_id, started).

    started=False means a job of the same kind was already running — its id is
    returned and nothing new is launched.
    """
    with _JOBS_LOCK:
        # Don't start a second job of the same kind on top of a running one.
        if _job_running(kind):
            for jid, j in JOBS.items():
                if j["kind"] == kind and j["status"] == "running":
                    return jid, False
        # Prune finished jobs beyond the most recent ~20 so JOBS doesn't grow
        # forever (dicts keep insertion order, so oldest come first).
        finished = [jid for jid, j in JOBS.items() if j["status"] != "running"]
        for jid in finished[:-20]:
            del JOBS[jid]
        job_id = uuid.uuid4().hex
        JOBS[job_id] = {"status": "running", "log": [], "result": None,
                        "error": None, "kind": kind, "label": label}

    def worker() -> None:
        def log(msg) -> None:
            with _JOBS_LOCK:
                JOBS[job_id]["log"].append(str(msg))

        try:
            result = fn(log)
            with _JOBS_LOCK:
                JOBS[job_id]["result"] = result
                JOBS[job_id]["status"] = "done"
        except PipelineError as e:
            with _JOBS_LOCK:
                JOBS[job_id]["error"] = str(e)
                JOBS[job_id]["status"] = "error"
        except Exception as e:  # pragma: no cover - safety net
            with _JOBS_LOCK:
                JOBS[job_id]["log"].append(traceback.format_exc())
                JOBS[job_id]["error"] = f"Unexpected error: {e}"
                JOBS[job_id]["status"] = "error"

    threading.Thread(target=worker, daemon=True).start()
    return job_id, True


def _job_response(job_id: str, started: bool):
    """JSON for the /api/run/* endpoints — flags when a running job was reused."""
    return jsonify(job_id=job_id, already_running=not started)


# ───────────────────────── profile helpers ─────────────────────────
def _load_profile_fields() -> dict:
    if PROFILE_FIELDS.exists():
        return json.loads(PROFILE_FIELDS.read_text(encoding="utf-8"))
    return {}


def _save_resume(upload, kind: str = "academic") -> str:
    """Save an uploaded resume under its real filename and point config at it.

    kind="academic" is the default resume (paths.resume, also the fallback);
    kind="industry" is an optional second resume attached to industry drafts
    (paths.resume_industry, kept in a resume/industry subfolder so the two
    don't clobber each other). Returns the stored filename.
    """
    cfg = load_config()
    root_dir = resolve(cfg["paths"]["resume"]).parent
    is_industry = kind == "industry"
    target_dir = (root_dir / "industry") if is_industry else root_dir
    key = "resume_industry" if is_industry else "resume"
    target_dir.mkdir(parents=True, exist_ok=True)

    safe = secure_filename(upload.filename or "") or "resume.pdf"
    if not safe.lower().endswith(".pdf"):
        safe += ".pdf"

    # Only one PDF lives in this slot's folder — clear it before saving.
    for old in target_dir.glob("*.pdf"):
        try:
            old.unlink()
        except OSError:
            pass

    dest = target_dir / safe
    upload.save(str(dest))

    try:
        cfg["paths"][key] = dest.relative_to(ROOT).as_posix()
    except ValueError:
        cfg["paths"][key] = f"{target_dir.name}/{safe}"
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
        # /api/state never echoes the secret back, so a blank field means
        # "keep what's stored" — only overwrite with a non-empty value.
        pw = (vals.get("gmail_app_password") or "").strip()
        if pw:
            env_updates["GMAIL_APP_PASSWORD"] = pw
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


def job_status(job_id: str):
    with _JOBS_LOCK:
        job = JOBS.get(job_id)
        if not job:
            return jsonify(status="unknown"), 404
        return jsonify(status=job["status"], log=list(job["log"]),
                       error=job["error"], result=job["result"])


def jobs_status():
    """Status of recent jobs for the persistent status bar (newest last)."""
    out = []
    with _JOBS_LOCK:
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
    with TARGETS_LOCK:
        if targets_path.exists():
            # csv.reader (not a raw line count): quoted fields can span lines.
            with targets_path.open("r", encoding="utf-8", newline="") as fh:
                contact_count = max(0, sum(1 for _ in csv.reader(fh)) - 1)  # minus header
    return jsonify(jobs=out, draft_count=draft_count, contact_count=contact_count)


# ═══════════════════════════════════════════════════════════════════════════
# JSON API — consumed by the Next.js / shadcn frontend (proxied at /py/*).
# ═══════════════════════════════════════════════════════════════════════════
def _contacts_rows() -> list[dict]:
    cfg = load_config()
    targets_path = resolve(cfg["paths"]["targets"])
    with TARGETS_LOCK:
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
    industry_path = cfg["paths"].get("resume_industry")
    resume_industry = resolve(industry_path) if industry_path else None
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
        gmail_app_password="",  # never sent to the browser
        gmail_app_password_set=bool(secrets["gmail_app_password"]),
        schools=schools,
        resume_ok=resume_path.exists(),
        resume_name=resume_path.name,
        resume_industry_ok=bool(resume_industry and resume_industry.exists()),
        resume_industry_name=resume_industry.name if resume_industry else "",
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
    kind = "industry" if (request.form.get("kind") == "industry") else "academic"
    name = _save_resume(upload, kind=kind)
    return jsonify(ok=True, resume_name=name)


@app.route("/api/contacts")
def api_contacts():
    cfg = load_config()
    drafts_dir = resolve(cfg["paths"]["drafts_dir"])
    rows = _contacts_rows()
    # Flag which contacts already have a draft, so the UI can show it.
    for r in rows:
        slug = draft.slug_for(
            r.get("email", ""), r.get("name", ""),
            r.get("profile_url", ""), r.get("source_url", ""),
        )
        r["drafted"] = (drafts_dir / f"{slug}.md").exists()
    return jsonify(rows=rows)


@app.route("/api/sent")
def api_sent():
    cfg = load_config()
    log_path = resolve(cfg["paths"]["sent_dir"]) / "sent_log.csv"
    rows: list[dict] = []
    if log_path.exists():
        with log_path.open("r", encoding="utf-8", newline="") as fh:
            rows = list(csv.DictReader(fh))
    rows.reverse()  # newest first
    return jsonify(rows=rows)


@app.route("/api/run/follow-up", methods=["POST"])
def api_run_follow_up():
    data = request.get_json(silent=True) or {}
    to = (data.get("to") or "").strip()
    name = (data.get("name") or "").strip()
    subject = (data.get("subject") or "").strip()
    if not to:
        return jsonify(error="No recipient specified."), 400
    job_id, started = _start_job(lambda log: draft.follow_up(to, name, subject, log=log),
                                 "draft", f"Follow-up to {name or to}")
    return _job_response(job_id, started)


@app.route("/api/contacts/set-email", methods=["POST"])
def api_set_contact_email():
    data = request.get_json(silent=True) or {}
    cfg = load_config()
    targets_path = resolve(cfg["paths"]["targets"])
    profile_url = (data.get("profile_url") or "").strip()
    new_email = (data.get("email") or "").strip().lower()
    if targets_path.exists() and profile_url and new_email and "@" in new_email:
        with TARGETS_LOCK:
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

        with TARGETS_LOCK:
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
                "category": d.get("category", ""),
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
        # Preserve the draft's category (set at generation, not shown in the editor).
        category = send.parse_draft(path).get("category", "")
        draft.write_draft_file(
            path,
            to=(data.get("to") or "").strip(),
            name=(data.get("name") or "").strip(),
            subject=(data.get("subject") or "").strip(),
            body=data.get("body", ""),
            source_url=(data.get("source_url") or "").strip(),
            status=(data.get("status") or "pending").strip(),
            category=category,
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
    with TARGETS_LOCK:
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
    job_id, started = _start_job(
        lambda log: scrape.run(log=log, category=category), "scrape", "Scraping"
    )
    return _job_response(job_id, started)


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
    job_id, started = _start_job(_fn, "discover", f"{label}: {query}")
    return _job_response(job_id, started)


@app.route("/api/run/draft", methods=["POST"])
def api_run_draft():
    def _draft_fn(log):
        import time as _time

        # Drive the drafter one contact at a time so a contact whose draft
        # fails is skipped for the rest of this run instead of being retried
        # (and re-billed) on every cycle while the scraper keeps working.
        failed: set[str] = set()
        made = 0
        while True:
            cfg = load_config()
            drafts_dir = resolve(cfg["paths"]["drafts_dir"])
            pending = []
            for r in _contacts_rows():
                slug = draft.slug_for(
                    r.get("email", ""), r.get("name", ""),
                    r.get("profile_url", ""), r.get("source_url", ""),
                )
                if slug not in failed and not (drafts_dir / f"{slug}.md").exists():
                    pending.append((slug, r))
            if not pending:
                if _job_running("scrape") or _job_running("discover"):
                    log("Waiting for scraper to find more contacts…")
                    _time.sleep(15)
                    continue
                if made == 0 and not resolve(cfg["paths"]["targets"]).exists():
                    raise PipelineError(
                        "No contacts yet. Scrape some schools on the Setup page first."
                    )
                break
            for slug, r in pending:
                ident = (r.get("email") or r.get("profile_url") or r.get("name") or "").strip()
                if not ident:
                    failed.add(slug)
                    continue
                draft.run(log=log, only=ident, limit=1)
                if (drafts_dir / f"{slug}.md").exists():
                    made += 1
                else:
                    log(f"  [{r.get('name') or ident}] failed — skipping for the rest of this run.")
                    failed.add(slug)
        log(f"Done. Wrote {made} new draft(s).")
        return {"made": made}

    job_id, started = _start_job(_draft_fn, "draft", "Generating drafts")
    return _job_response(job_id, started)


@app.route("/api/run/draft-one", methods=["POST"])
def api_run_draft_one():
    data = request.get_json(silent=True) or {}
    ident = (data.get("email") or data.get("profile_url") or data.get("name") or "").strip()
    if not ident:
        return jsonify(error="No contact specified."), 400
    label = data.get("name") or ident
    job_id, started = _start_job(lambda log: draft.run(log=log, only=ident, limit=1),
                                 "draft", f"Drafting {label}")
    return _job_response(job_id, started)


@app.route("/api/run/send", methods=["POST"])
def api_run_send():
    data = request.get_json(silent=True) or {}
    html_map = data.get("html_map") or {}
    job_id, started = _start_job(
        lambda log: send.send_approved(do_send=True, log=log, html_map=html_map),
        "send", "Sending approved",
    )
    return _job_response(job_id, started)


@app.route("/api/job/<job_id>")
def api_job_status(job_id: str):
    return job_status(job_id)


@app.route("/api/jobs")
def api_jobs_status():
    return jobs_status()
