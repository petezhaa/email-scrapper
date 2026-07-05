# -*- coding: utf-8 -*-
"""RocketReach-style email discovery.

Two layers, tried in order:
  1. SMTP verify — pattern-guess the address, probe the mail server to confirm
     (no email is ever sent; it is a pure RCPT handshake) — free
  2. Web search  — find publicly listed emails in papers, bios, GitHub — paid

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
import unicodedata
from typing import Optional
from urllib.parse import urlparse

from .config import PipelineError

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

_WEB_SEARCH_TOOL = {"type": "web_search_20260209", "name": "web_search", "max_uses": 4}

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


# Honorifics/suffixes that must never end up in an email local part.
# Dots are already stripped by tokenization, so "Dr." and "Ph.D." match too.
_NAME_NOISE = {"dr", "prof", "professor", "jr", "sr", "ii", "iii", "phd", "md"}


def _name_parts(full_name: str) -> dict:
    """Split a full name into parts needed for pattern generation."""
    # Transliterate accents (José → jose) instead of deleting the characters.
    ascii_name = unicodedata.normalize("NFKD", full_name).encode("ascii", "ignore").decode("ascii")
    tokens = [re.sub(r"[^a-z]", "", t.lower()) for t in re.split(r"[\s\-]+", ascii_name.strip())]
    tokens = [t for t in tokens if t and t not in _NAME_NOISE]
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


# Per-domain caches — jobs call find_email in a loop over contacts that share
# domains, so MX lookups and catch-all probes are memoized for the run.
_MX_CACHE: dict[str, str] = {}
_CATCH_ALL_CACHE: dict[str, bool] = {}

# Port-25 reachability. Consumer ISPs often block outbound port 25 entirely;
# every SMTP connect then times out. After two domains in a row time out we
# flag the network and skip the SMTP-verify layer for the rest of the run.
_timeout_streak = 0
_port25_blocked = False
_last_probe_timed_out = False  # set by _smtp_probe on a socket timeout


def _get_mx(domain: str) -> str:
    """Return the highest-priority MX hostname for domain, or '' on failure."""
    if domain in _MX_CACHE:
        return _MX_CACHE[domain]
    try:
        import dns.resolver
        records = dns.resolver.resolve(domain, "MX")
        result = str(min(records, key=lambda r: r.preference).exchange).rstrip(".")
    except Exception:
        result = ""
    _MX_CACHE[domain] = result
    return result


def _smtp_probe(email: str, mx_host: str) -> Optional[bool]:
    """SMTP RCPT probe — True=mailbox exists, False=rejected, None=inconclusive.

    No message is ever sent. The connection is closed after the RCPT check.
    """
    global _last_probe_timed_out
    _last_probe_timed_out = False
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
    except (socket.timeout, TimeoutError):
        _last_probe_timed_out = True
        return None  # typical of ISP-blocked port 25 — see _note_domain_timeout
    except (OSError, smtplib.SMTPException, ConnectionRefusedError):
        return None  # port blocked or server refused — inconclusive


def _note_domain_timeout(log) -> None:
    """Count a domain whose SMTP connection timed out; flag port 25 after 2 in a row."""
    global _timeout_streak, _port25_blocked
    _timeout_streak += 1
    if _timeout_streak >= 2 and not _port25_blocked:
        _port25_blocked = True
        log("  (port 25 appears blocked — skipping SMTP verification)")


def _is_catch_all(mx_host: str, domain: str) -> bool:
    """Probe with a random definitely-invalid address to detect catch-all servers."""
    if domain in _CATCH_ALL_CACHE:
        return _CATCH_ALL_CACHE[domain]
    fake = f"zzz_nobody_xyz_99999@{domain}"
    result = _smtp_probe(fake, mx_host) is True
    _CATCH_ALL_CACHE[domain] = result
    return result


def _web_search_email(
    client, model: str, name: str, company: str, domain: str, log
) -> str:
    """Use Claude web_search to find a publicly listed email. Returns '' if not found.

    Raises PipelineError on connection/auth failures so the UI shows the real
    problem instead of a misleading "no email found" for every contact.
    """
    import anthropic

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
                output_config={"effort": "low"},
                cache_control={"type": "ephemeral"},
                messages=messages,
            )
            if resp.stop_reason == "pause_turn":
                messages.append({"role": "assistant", "content": resp.content})
                continue
            break
        text = "\n".join(b.text for b in resp.content if b.type == "text").strip()
    except anthropic.APIConnectionError as e:
        raise PipelineError(
            "Couldn't reach the AI endpoint (connection error). Check your "
            "network/VPN and ANTHROPIC_BASE_URL in .env, then try again."
        ) from e
    except anthropic.AuthenticationError as e:
        raise PipelineError(
            "The AI endpoint rejected the credential (401). "
            "Check ANTHROPIC_API_KEY in .env."
        ) from e
    except anthropic.APIStatusError as e:
        raise PipelineError(
            f"The AI endpoint returned an error ({e.status_code}). "
            "Try again in a minute."
        ) from e
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


def _smtp_verify(name: str, domain: str, log) -> str:
    """Free layer: pattern-guess candidates and confirm one via SMTP RCPT probe.

    Returns '' when nothing verifies — also when the domain has no MX, is
    catch-all, or port 25 is blocked on this network.
    """
    global _timeout_streak
    if _port25_blocked:
        return ""

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
        if _last_probe_timed_out:
            _note_domain_timeout(log)  # connect timed out — more patterns won't help
            return ""
        _timeout_streak = 0  # connection worked — the network can reach port 25
        time.sleep(0.4)  # be polite to the mail server
        if result is True:
            log(f"  ({name}: SMTP verified → {email})")
            return email
        elif result is False:
            continue
        # None = server blocked RCPT probes on this pattern, keep trying
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

    Layer 1 — SMTP verify (free): pattern-guess then RCPT-probe each candidate
    Layer 2 — web search (paid): papers, public bios, GitHub

    Returns the verified email or '' if nothing confirmed.
    """
    if not name or not domain:
        return ""
    parts = _name_parts(name)
    if not parts["first"] or not parts["last"]:
        return ""

    # Layer 1: SMTP verification of pattern-guessed candidates (free)
    email = _smtp_verify(name, domain, log)
    if email:
        return email

    # Layer 2: paid web search, only when the free layer came up empty
    if client and model:
        email = _web_search_email(client, model, name, affiliation, domain, log)
        if email:
            log(f"  ({name}: email found via web search → {email})")
            return email

    log(f"  ({name}: no email found for {domain})")
    return ""
