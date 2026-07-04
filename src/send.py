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
import smtplib
import ssl
import time
from datetime import datetime, timezone
from email.message import EmailMessage
from pathlib import Path

from .config import PipelineError, load_config, load_secrets, resolve

SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 587


def parse_draft(path: Path) -> dict:
    """Parse a draft .md with simple `---` front-matter into a dict + body."""
    text = path.read_text(encoding="utf-8")
    meta: dict = {"to": "", "name": "", "subject": "", "status": "pending", "source_url": ""}
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


def _build_message(sender: str, sender_name: str, draft: dict, resume: Path | None,
                   fixed_sig: str | None) -> EmailMessage:
    msg = EmailMessage()
    msg["From"] = f"{sender_name} <{sender}>"
    msg["To"] = draft["to"]
    msg["Subject"] = draft["subject"]
    body = draft["body"]
    if fixed_sig:
        body = body.rstrip() + "\n\n" + fixed_sig
    msg.set_content(body)
    if resume and resume.exists():
        data = resume.read_bytes()
        msg.add_attachment(
            data, maintype="application", subtype="pdf", filename=resume.name
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


def _log_sent(sent_dir: Path, draft: dict) -> None:
    sent_dir.mkdir(parents=True, exist_ok=True)
    log = sent_dir / "sent_log.csv"
    write_header = not log.exists()
    with log.open("a", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        if write_header:
            w.writerow(["sent_at_utc", "to", "name", "subject"])
        w.writerow([datetime.now(timezone.utc).isoformat(), draft["to"], draft["name"], draft["subject"]])


def send_approved(do_send: bool = False, log=print) -> dict:
    """Send (or, if do_send=False, preview) all drafts marked `approved`.

    Returns a summary dict. Raises PipelineError for user-facing failures.
    Shared by the CLI (`run`) and the web UI.
    """
    cfg = load_config()
    secrets = load_secrets()

    drafts_dir = resolve(cfg["paths"]["drafts_dir"])
    sent_dir = resolve(cfg["paths"]["sent_dir"])
    resume = resolve(cfg["paths"]["resume"])
    sender = secrets["gmail_address"]
    sender_name = cfg["sender"]["name"]
    max_per_run = int(cfg["sending"]["max_per_run"])
    delay = float(cfg["sending"]["delay_seconds"])
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
                msg = _build_message(sender, sender_name, d, resume, fixed_sig)
                try:
                    server.send_message(msg)
                except Exception as e:
                    log(f"  FAILED to send to {d['to']}: {e}")
                    continue
                _log_sent(sent_dir, d)
                # Mark the draft as sent so a re-run won't resend it.
                # write_draft_file stores values as JSON strings (with quotes), so
                # we must match the quoted form first, then fall back to unquoted
                # for files that were manually edited without quotes.
                raw = d["_file"].read_text(encoding="utf-8")
                new_text = raw.replace('status: "approved"', 'status: "sent"', 1)
                if new_text == raw:  # manual edit without quotes
                    new_text = raw.replace("status: approved", "status: sent", 1)
                d["_file"].write_text(new_text, encoding="utf-8")
                sent += 1
                log(f"  sent to {d['name']} <{d['to']}>")

                if i < len(approved) - 1:
                    log(f"  waiting {delay:.0f}s …")
                    time.sleep(delay)
    except (smtplib.SMTPException, OSError) as e:
        raise PipelineError(f"Email connection failed: {e}")

    log(f"Done. Sent {sent} email(s).")
    return {"sent": sent, "approved": len(approved), "capped": capped}


def run(do_send: bool = False) -> None:
    send_approved(do_send=do_send, log=print)
