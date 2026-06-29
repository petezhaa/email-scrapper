# -*- coding: utf-8 -*-
"""RocketReach-style email discovery.

Three layers, tried in order:
  1. Web search  — find publicly listed emails in papers, bios, GitHub
  2. SMTP verify — pattern-guess the address, probe the mail server to confirm
     (no email is ever sent; it is a pure RCPT handshake)

Usage from scrape.py:
    from . import emailfinder
    email = emailfinder.find_email(name, domain, client=client, model=model,
                                   affiliation=affiliation, log=log)
"""
from __future__ import annotations

import re
import smtplib
import socket
import time
from typing import Optional
from urllib.parse import urlparse

# Corporate email patterns tried in order of real-world prevalence.
_PATTERNS = [
    "{first}.{last}",    # john.doe   — most common in pharma/biotech
    "{f}{last}",          # jdoe
    "{f}.{last}",         # j.doe
    "{first}{last}",      # johndoe
    "{first}_{last}",     # john_doe
    "{last}.{first}",     # doe.john
    "{last}{f}",          # doej
    "{last}",             # doe  — small biotechs sometimes use this
    "{first}",            # john — rare
]

_WEB_SEARCH_TOOL = {"type": "web_search_20260209", "name": "web_search", "max_uses": 3}

_WEB_SEARCH_PROMPT = """\
Find the professional email address for {name} at {company}. Search in this order:

1. "{name} {company} email" — any publicly listed address.
2. "{name} site:pubmed.ncbi.nlm.nih.gov" — papers where they are corresponding author \
   (corresponding author emails are printed in the paper).
3. "{name} site:biorxiv.org OR site:arxiv.org" — preprints with contact info.
4. "{name} @{domain}" — finds addresses accidentally published anywhere on the web.

Return ONLY the email address if you find one that ends in @{domain}. \
If you cannot find a confirmed address, return exactly: NONE"""


def domain_from_url(url: str) -> str:
    """Extract the registrable domain from a URL.

    https://www.pfizer.com/people/john → pfizer.com
    """
    host = urlparse(url).netloc.lower()
    host = re.sub(r"^www\d*\.", "", host)  # strip www / www2
    parts = host.split(".")
    return ".".join(parts[-2:]) if len(parts) >= 2 else host


def _name_parts(full_name: str) -> dict:
    """Split a full name into parts needed for pattern generation."""
    tokens = [re.sub(r"[^a-z]", "", t.lower()) for t in re.split(r"[\s\-]+", full_name.strip())]
    tokens = [t for t in tokens if t]
    first = tokens[0] if tokens else ""
    last = tokens[-1] if len(tokens) > 1 else ""
    return {"first": first, "last": last, "f": first[:1]}


def candidate_emails(name: str, domain: str) -> list[str]:
    """All pattern-based candidate addresses for name@domain, deduplicated."""
    parts = _name_parts(name)
    if not parts["first"] or not parts["last"]:
        return []
    seen: set[str] = set()
    out: list[str] = []
    for pattern in _PATTERNS:
        local = pattern.format(**parts)
        if local and local not in seen:
            seen.add(local)
            out.append(f"{local}@{domain}")
    return out


def _get_mx(domain: str) -> str:
    """Return the highest-priority MX hostname for domain, or '' on failure."""
    try:
        import dns.resolver
        records = dns.resolver.resolve(domain, "MX")
        return str(min(records, key=lambda r: r.preference).exchange).rstrip(".")
    except Exception:
        return ""


def _smtp_probe(email: str, mx_host: str) -> Optional[bool]:
    """SMTP RCPT probe — True=mailbox exists, False=rejected, None=inconclusive.

    No message is ever sent. The connection is closed after the RCPT check.
    """
    try:
        with smtplib.SMTP(timeout=8) as smtp:
            smtp.connect(mx_host, 25)
            smtp.ehlo("outreach.local")
            smtp.mail("probe@outreach.local")
            code, _ = smtp.rcpt(email)
            smtp.quit()
            return code == 250
    except smtplib.SMTPRecipientsRefused:
        return False
    except (OSError, socket.timeout, smtplib.SMTPException, ConnectionRefusedError):
        return None  # port blocked or server refused — inconclusive


def _is_catch_all(mx_host: str, domain: str) -> bool:
    """Probe with a random definitely-invalid address to detect catch-all servers."""
    fake = f"zzz_nobody_xyz_99999@{domain}"
    return _smtp_probe(fake, mx_host) is True


def _web_search_email(
    client, model: str, name: str, company: str, domain: str, log
) -> str:
    """Use Claude web_search to find a publicly listed email. Returns '' if not found."""
    prompt = _WEB_SEARCH_PROMPT.format(
        name=name, company=company or domain, domain=domain
    )
    messages = [{"role": "user", "content": prompt}]
    try:
        for _ in range(4):
            resp = client.messages.create(
                model=model,
                max_tokens=200,
                tools=[_WEB_SEARCH_TOOL],
                messages=messages,
            )
            if resp.stop_reason == "pause_turn":
                messages.append({"role": "assistant", "content": resp.content})
                continue
            break
        text = "\n".join(b.text for b in resp.content if b.type == "text").strip()
    except Exception as e:
        log(f"  (email web search failed for {name}: {e})")
        return ""

    if not text or "NONE" in text.upper():
        return ""
    match = re.search(r"[a-z0-9._%+\-]+@[a-z0-9.\-]+\.[a-z]{2,}", text.lower())
    if match:
        found = match.group(0)
        if found.endswith(f"@{domain}"):
            return found
    return ""


def find_email(
    name: str,
    domain: str,
    *,
    client=None,
    model: str = "",
    affiliation: str = "",
    log=print,
) -> str:
    """Find and verify a professional email for name at domain.

    Layer 1 — web search: papers, public bios, GitHub
    Layer 2 — SMTP verify: pattern-guess then RCPT-probe each candidate

    Returns the verified email or '' if nothing confirmed.
    """
    if not name or not domain:
        return ""
    parts = _name_parts(name)
    if not parts["first"] or not parts["last"]:
        return ""

    # Layer 1: web search for a publicly listed address
    if client and model:
        email = _web_search_email(client, model, name, affiliation, domain, log)
        if email:
            log(f"  ({name}: email found via web search → {email})")
            return email

    # Layer 2: SMTP verification of pattern-guessed candidates
    mx_host = _get_mx(domain)
    if not mx_host:
        log(f"  ({name}: no MX record for {domain} — skipping SMTP verify)")
        return ""

    if _is_catch_all(mx_host, domain):
        log(f"  ({name}: {domain} accepts all addresses — SMTP verify unreliable, skipping)")
        return ""

    log(f"  ({name}: trying {len(candidate_emails(name, domain))} email patterns via SMTP…)")
    for email in candidate_emails(name, domain):
        result = _smtp_probe(email, mx_host)
        time.sleep(0.4)  # be polite to the mail server
        if result is True:
            log(f"  ({name}: SMTP verified → {email})")
            return email
        elif result is False:
            continue
        # None = server blocked RCPT probes on this pattern, keep trying

    log(f"  ({name}: no email found for {domain})")
    return ""
