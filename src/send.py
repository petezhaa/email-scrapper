"""Send approved drafts via Gmail SMTP, attaching your resume.

Only drafts with `status: approved` are sent. By default this is a DRY RUN
(previews what would be sent). Pass --send to actually send.

Usage:
    python -m src.cli status          # list all drafts + their status
    python -m src.cli send            # dry run (preview approved drafts)
    python -m src.cli send --send     # actually send approved drafts
"""
from __future__ import annotations

import csv
import json
import random
import re
import smtplib
import ssl
import time
from datetime import datetime, timezone
from email.message import EmailMessage
from email.utils import formatdate, make_msgid
from html import escape, unescape
from pathlib import Path

from .config import PipelineError, load_config, load_secrets, resolve

SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 587


def parse_draft(path: Path) -> dict:
    """Parse a draft .md with simple `---` front-matter into a dict + body."""
    text = path.read_text(encoding="utf-8")
    meta: dict = {"to": "", "name": "", "subject": "", "status": "pending",
                  "source_url": "", "category": ""}
    body = text
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            header = text[3:end]
            body = text[end + 4 :].lstrip("\n")
            for line in header.splitlines():
                if ":" in line:
                    key, _, val = line.partition(":")
                    key = key.strip()
                    # Strip inline "# ..." comments and surrounding whitespace.
                    val = val.split("#", 1)[0].strip() if key == "status" else val.strip()
                    # write_draft_file wraps values with json.dumps() (which escapes
                    # non-ASCII: an em dash becomes "—"). Decode it back rather
                    # than just stripping quotes, or those escapes leak into the UI
                    # and the sent email. Fall back to the raw text for files that
                    # were hand-edited without JSON quoting.
                    if len(val) >= 2 and val[0] == '"' and val[-1] == '"':
                        try:
                            val = json.loads(val)
                        except (ValueError, json.JSONDecodeError):
                            val = val[1:-1]
                    if key in meta:
                        meta[key] = val
    meta["body"] = body.strip()
    meta["_file"] = path
    return meta


def load_drafts(drafts_dir: Path) -> list[dict]:
    return sorted(
        (parse_draft(p) for p in drafts_dir.glob("*.md")),
        key=lambda d: d["_file"].name,
    )


def status() -> None:
    cfg = load_config()
    drafts_dir = resolve(cfg["paths"]["drafts_dir"])
    if not drafts_dir.exists():
        raise SystemExit(f"No drafts dir: {drafts_dir}. Run `python -m src.cli draft` first.")
    drafts = load_drafts(drafts_dir)
    if not drafts:
        print("No drafts yet. Run `python -m src.cli draft`.")
        return
    counts: dict[str, int] = {}
    print(f"{'STATUS':10} {'TO':35} SUBJECT")
    print("-" * 90)
    for d in drafts:
        counts[d["status"]] = counts.get(d["status"], 0) + 1
        print(f"{d['status']:10} {d['to']:35} {d['subject'][:42]}")
    print("-" * 90)
    print("  " + "  ".join(f"{k}: {v}" for k, v in sorted(counts.items())))


def _attachment_name(sender_name: str) -> str:
    """A recipient-friendly attachment name ("Jane Doe - Resume.pdf") instead of
    whatever the file happens to be called on disk."""
    safe = re.sub(r"[^A-Za-z0-9 ._-]+", "", sender_name).strip()
    return f"{safe or 'Applicant'} - Resume.pdf"


def _append_sig_html(html: str, fixed_sig: str) -> str:
    """Mirror the plain-text fixed signature into the HTML alternative."""
    sig = "<br /><br />" + "<br />".join(escape(line) for line in fixed_sig.splitlines())
    # Keep it inside <body> when the rendered document has one.
    m = re.search(r"</body>", html, re.IGNORECASE)
    if m:
        return html[: m.start()] + sig + html[m.start():]
    return html + sig


def _html_matches_body(html: str, body: str) -> bool:
    """Cheap divergence guard: strip the tags out of the HTML and check it shares
    words with the plain-text body. A stale/mismatched html_map entry (clearly a
    different email) would otherwise show someone else's text as the visible part."""
    text = re.sub(r"(?is)<(?:style|script)[^>]*>.*?</(?:style|script)>", " ", html)
    text = unescape(re.sub(r"<[^>]+>", " ", text))
    body_words = {w for w in re.findall(r"[a-z0-9']+", body.lower()) if len(w) > 3}
    if not body_words:
        return True
    html_words = set(re.findall(r"[a-z0-9']+", text.lower()))
    return len(body_words & html_words) / len(body_words) >= 0.3


def _build_message(sender: str, sender_name: str, draft: dict, resume: Path | None,
                   fixed_sig: str | None, html: str | None = None,
                   in_reply_to: str | None = None, log=print) -> EmailMessage:
    msg = EmailMessage()
    msg["From"] = f"{sender_name} <{sender}>"
    msg["To"] = draft["to"]
    msg["Subject"] = draft["subject"]
    # A real Message-ID + Date help deliverability, and let follow-ups thread
    # onto the original via In-Reply-To/References.
    msg["Message-ID"] = make_msgid()
    msg["Date"] = formatdate(localtime=True)
    if in_reply_to:
        msg["In-Reply-To"] = in_reply_to
        msg["References"] = in_reply_to
    body = draft["body"]
    if fixed_sig:
        body = body.rstrip() + "\n\n" + fixed_sig
    # Plain text is the always-present fallback; the React Email HTML (rendered
    # by the Next app) is added as the richer alternative when provided.
    msg.set_content(body)
    if html:
        if fixed_sig:
            html = _append_sig_html(html, fixed_sig)
        if _html_matches_body(html, draft["body"]):
            msg.add_alternative(html, subtype="html")
        else:
            log(f"  WARNING: HTML part for {draft['to']} doesn't match the plain-text "
                "body — sending plain text only.")
    if resume and resume.exists():
        data = resume.read_bytes()
        if len(data) > 10 * 1024 * 1024:
            log(f"  WARNING: resume is {len(data) / (1024 * 1024):.1f} MB — Gmail may "
                "reject attachments this large.")
        msg.add_attachment(
            data, maintype="application", subtype="pdf",
            filename=_attachment_name(sender_name),
        )
    return msg


def _signature(cfg: dict) -> str | None:
    if not cfg["email"].get("append_fixed_signature"):
        return None
    s = cfg["sender"]
    lines = [s["name"]]
    if s.get("phone"):
        lines.append(s["phone"])
    lines.extend(s.get("links", []) or [])
    return "\n".join(lines)


SENT_LOG_COLUMNS = ["sent_at_utc", "to", "name", "subject", "message_id", "slug"]


def _log_sent(sent_dir: Path, draft: dict, message_id: str, slug: str) -> None:
    sent_dir.mkdir(parents=True, exist_ok=True)
    log = sent_dir / "sent_log.csv"
    if log.exists():
        # Logs written before the message_id/slug columns get their header
        # upgraded in place (old rows padded), so DictReader sees every column.
        with log.open("r", encoding="utf-8", newline="") as f:
            rows = list(csv.reader(f))
        if rows and rows[0] != SENT_LOG_COLUMNS:
            with log.open("w", encoding="utf-8", newline="") as f:
                w = csv.writer(f)
                w.writerow(SENT_LOG_COLUMNS)
                for r in rows[1:]:
                    w.writerow((r + [""] * len(SENT_LOG_COLUMNS))[: len(SENT_LOG_COLUMNS)])
    write_header = not log.exists()
    with log.open("a", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        if write_header:
            w.writerow(SENT_LOG_COLUMNS)
        w.writerow([datetime.now(timezone.utc).isoformat(), draft["to"], draft["name"],
                    draft["subject"], message_id, slug])


def _read_sent_rows(sent_dir: Path) -> list[dict]:
    """Rows from sent_log.csv, oldest first. Tolerates logs from before the
    message_id/slug columns existed (missing values come back as "")."""
    log = sent_dir / "sent_log.csv"
    if not log.exists():
        return []
    with log.open("r", encoding="utf-8", newline="") as f:
        return [{k: (r.get(k) or "").strip() for k in SENT_LOG_COLUMNS}
                for r in csv.DictReader(f)]


def _last_message_id_for(rows: list[dict], to: str) -> str | None:
    """Message-ID of the most recent email we sent to this address, if logged."""
    want = to.strip().lower()
    for r in reversed(rows):
        if r["to"].lower() == want and r["message_id"]:
            return r["message_id"]
    return None


def send_approved(do_send: bool = False, log=print, html_map: dict | None = None) -> dict:
    """Send (or, if do_send=False, preview) all drafts marked `approved`.

    Returns a summary dict. Raises PipelineError for user-facing failures.
    Shared by the CLI (`run`) and the web UI.

    html_map: optional {slug: html} of React Email-rendered bodies, keyed by the
    draft's filename stem. When present, each email carries that HTML as its
    rich alternative part (plain text stays as the fallback).
    """
    html_map = html_map or {}
    cfg = load_config()
    secrets = load_secrets()

    drafts_dir = resolve(cfg["paths"]["drafts_dir"])
    sent_dir = resolve(cfg["paths"]["sent_dir"])
    # The default/academic resume, plus an optional industry-specific one.
    resume = resolve(cfg["paths"]["resume"])
    industry_path = cfg["paths"].get("resume_industry")
    resume_industry = resolve(industry_path) if industry_path else None

    def resume_for(draft: dict):
        if (draft.get("category") == "industry" and resume_industry
                and resume_industry.exists()):
            return resume_industry
        return resume

    sender = secrets["gmail_address"]
    sender_name = cfg["sender"]["name"]
    max_per_run = int(cfg["sending"]["max_per_run"])
    delay = float(cfg["sending"]["delay_seconds"])
    daily_cap = int(cfg["sending"].get("daily_cap", 40))
    fixed_sig = _signature(cfg)

    if not sender_name:
        raise PipelineError("Sender name is not set. Fill in your name on the Setup page and save.")

    if not drafts_dir.exists():
        raise PipelineError("No drafts yet. Generate drafts first.")

    approved = [d for d in load_drafts(drafts_dir) if d["status"] == "approved"]
    if not approved:
        log("No drafts marked 'approved'. Approve a draft first.")
        return {"sent": 0, "approved": 0}

    capped = False
    if len(approved) > max_per_run:
        log(f"{len(approved)} approved, but the per-run cap is {max_per_run}. Sending the first {max_per_run}.")
        approved = approved[:max_per_run]
        capped = True

    # Daily cap (deliverability): count what already went out today before
    # adding more, so repeated runs can't burn the sender's reputation.
    sent_rows = _read_sent_rows(sent_dir)
    today = datetime.now(timezone.utc).date().isoformat()
    sent_today = sum(1 for r in sent_rows if r["sent_at_utc"].startswith(today))
    if sent_today >= daily_cap:
        log(f"Daily cap reached: {sent_today}/{daily_cap} emails already sent today. Try again tomorrow.")
        return {"sent": 0, "approved": len(approved), "capped": True}
    if len(approved) > daily_cap - sent_today:
        remaining = daily_cap - sent_today
        log(f"Daily cap is {daily_cap} and {sent_today} already went out today — sending only the next {remaining}.")
        approved = approved[:remaining]
        capped = True

    if not resume.exists():
        log(f"WARNING: resume not found at {resume} — emails will send WITHOUT an attachment.")

    if not do_send:
        log(f"DRY RUN — {len(approved)} email(s) would be sent:")
        for d in approved:
            log(f"  To: {d['name']} <{d['to']}>  |  Subject: {d['subject']}")
        return {"sent": 0, "approved": len(approved), "dry_run": True, "capped": capped}

    if not sender or not secrets["gmail_app_password"]:
        raise PipelineError("Your Gmail address / app password aren't set. Fill them in on the Setup page.")

    log(f"Connecting to Gmail as {sender} …")
    context = ssl.create_default_context()
    sent = 0

    def pause(i: int) -> None:
        # Jittered gap between sends (success or failure) so the traffic
        # doesn't look machine-generated.
        if i < len(approved) - 1:
            wait = random.uniform(delay, delay * 3)
            log(f"  waiting {wait:.0f}s …")
            time.sleep(wait)

    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=30) as server:
            server.starttls(context=context)
            try:
                server.login(sender, secrets["gmail_app_password"])
            except smtplib.SMTPAuthenticationError:
                raise PipelineError(
                    "Gmail rejected your login. Make sure you used a Gmail App Password "
                    "(not your normal password) and that 2-Step Verification is on."
                )

            for i, d in enumerate(approved):
                slug = d["_file"].stem
                is_followup = slug.endswith("_followup")
                # Follow-ups thread onto the original email (when its Message-ID
                # was logged) and go out without the resume re-attached.
                in_reply_to = _last_message_id_for(sent_rows, d["to"]) if is_followup else None
                html = html_map.get(slug)
                msg = _build_message(sender, sender_name, d,
                                     None if is_followup else resume_for(d),
                                     fixed_sig, html=html, in_reply_to=in_reply_to, log=log)
                try:
                    server.send_message(msg)
                except smtplib.SMTPAuthenticationError as e:
                    raise PipelineError(
                        f"Gmail rejected the login mid-run after {sent} email(s): {e}. "
                        "Check your Gmail App Password on the Setup page."
                    )
                except (smtplib.SMTPServerDisconnected, ConnectionError) as e:
                    raise PipelineError(
                        f"Lost the connection to Gmail after {sent} email(s): {e}. "
                        "Run send again to deliver the rest."
                    )
                except smtplib.SMTPRecipientsRefused as e:
                    log(f"  FAILED — Gmail refused recipient {d['to']}: {e.recipients}")
                    pause(i)
                    continue
                except smtplib.SMTPResponseException as e:
                    if 500 <= e.smtp_code < 600:
                        log(f"  FAILED to send to {d['to']} ({e.smtp_code}): {e.smtp_error}")
                        pause(i)
                        continue
                    raise  # 4xx etc. → the outer handler turns it into a PipelineError

                # Mark the draft as sent FIRST — if the run died between the SMTP
                # send and this flip, the next run would email the same person again.
                # write_draft_file stores values as JSON strings (with quotes), so
                # we must match the quoted form first, then fall back to unquoted
                # for files that were manually edited without quotes.
                try:
                    raw = d["_file"].read_text(encoding="utf-8")
                    new_text = raw.replace('status: "approved"', 'status: "sent"', 1)
                    if new_text == raw:  # manual edit without quotes
                        new_text = raw.replace("status: approved", "status: sent", 1)
                    d["_file"].write_text(new_text, encoding="utf-8")
                except Exception as e:
                    log(f"  ERROR: email to {d['to']} WAS sent but the draft could not "
                        f"be marked as sent ({e}). Mark it manually or it will be "
                        "re-sent on the next run!")
                message_id = str(msg["Message-ID"] or "")
                try:
                    _log_sent(sent_dir, d, message_id, slug)
                except OSError as e:
                    log(f"  WARNING: could not append to sent_log.csv ({e}) — is it open "
                        "in Excel? The email itself was sent; continuing.")
                # Remember the id so a follow-up later in this same run threads too.
                sent_rows.append({"to": d["to"], "message_id": message_id})
                sent += 1
                log(f"  sent to {d['name']} <{d['to']}>")
                pause(i)
    except (smtplib.SMTPException, OSError) as e:
        raise PipelineError(f"Email connection failed: {e}")

    log(f"Done. Sent {sent} email(s).")
    return {"sent": sent, "approved": len(approved), "capped": capped}


def run(do_send: bool = False) -> None:
    send_approved(do_send=do_send, log=print)
