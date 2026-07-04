"""Scrape faculty-directory pages into data/targets.csv.

Strategy: fetch each page, strip it to visible text, and ask Claude to extract
structured rows (name, email, title, research interests). Claude is robust to
the wildly varying layouts of department pages and can de-obfuscate emails
written like "name [at] university [dot] edu".

Usage:
    python -m src.cli scrape
"""
from __future__ import annotations

import csv
import json
import re
import time
import urllib.robotparser as robotparser
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from .config import PipelineError, build_anthropic_client, load_config, resolve
from .schools import resolve_entries
from . import emailfinder

USER_AGENT = (
    "Mozilla/5.0 (compatible; research-outreach-helper/1.0; personal academic use)"
)

CSV_FIELDS = ["name", "email", "title", "affiliation", "research_interests", "profile_url", "source_url", "category"]

# JSON schema Claude must conform to when extracting people from a page.
EXTRACT_SCHEMA = {
    "type": "object",
    "properties": {
        "people": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "email": {"type": "string"},
                    "title": {"type": "string"},
                    "affiliation": {"type": "string"},
                    "research_interests": {"type": "string"},
                    "profile_url": {"type": "string"},
                },
                "required": ["name", "email", "title", "affiliation", "research_interests", "profile_url"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["people"],
    "additionalProperties": False,
}

EXTRACT_PROMPT = """You are extracting research contacts from a university department \
page or company research team page. Below is the visible text of the page. Links \
appear inline as "link text <URL>".

Return every person who is a professor, postdoctoral researcher, research scientist, \
senior scientist, principal scientist, staff scientist, research associate, director \
of research, or similar research-focused role. Include a person if they have EITHER \
a visible email OR a link to their own profile/faculty/lab/personal page (most \
directory listings only link to profiles — that's expected, capture the link). Rules:
- email: the person's email if visible (de-obfuscate "jdoe [at] uni [dot] edu" -> \
"jdoe@uni.edu"). If no personal email is shown, use "". Do NOT use a generic \
department/program address (info@, biophys@, admin@, contact@) as a person's email.
- profile_url: the URL (shown in <...> right after their name/entry) to THIS person's \
individual profile / faculty / lab / personal page. Use "" if there's no such link. \
Ignore nav/menu/social links.
- research_interests: a short comma-separated list pulled from the page, or "" if none shown.
- title: e.g. "Associate Professor", "Postdoctoral Researcher". Use "" if unclear.
- affiliation: department / university if shown, else "".
- Skip anyone with NEITHER an email NOR a profile link. Do not invent anything.

PAGE TEXT:
---
{page_text}
---"""

# Local-parts that are department/program addresses, not a person's email.
_GENERIC_LOCALS = {
    "info", "admin", "administrator", "webmaster", "contact", "support", "help",
    "noreply", "no-reply", "office", "dept", "department", "program", "inquiries",
    "general", "biophys", "biophysics", "gradadmissions", "admissions", "hr",
    "chair", "director", "coordinator", "dean", "head", "faculty", "staff",
    "postdoc", "fellow", "fellows", "secretary", "assistant",
}
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")


def _pick_email(page_text: str, name: str) -> str:
    """Find the most likely personal email on a fetched profile page."""
    found: list[str] = []
    for e in _EMAIL_RE.findall(page_text):
        e = e.strip().lower()
        local = e.split("@")[0]
        # Substring match catches compound role addresses like "mcbchair", "labdirector"
        if e not in found and not any(g in local for g in _GENERIC_LOCALS):
            found.append(e)
    if not found:
        return ""
    name_parts = [p.lower() for p in re.split(r"\s+", name.strip()) if len(p) > 1]
    # Prefer an address whose local-part contains any part of the person's name.
    for e in found:
        local = e.split("@")[0]
        if any(part in local for part in name_parts):
            return e
    # Don't fall back to an unrelated address — it would block every other person
    # who shares the same page-level department contact email.
    return "" if name_parts else found[0]


def _check_robots(url: str) -> bool:
    """Best-effort robots.txt check. Returns True if allowed (or on any error)."""
    try:
        parsed = urlparse(url)
        robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
        rp = robotparser.RobotFileParser()
        rp.set_url(robots_url)
        rp.read()
        return rp.can_fetch(USER_AGENT, url)
    except Exception:
        return True  # don't block on robots fetch failures


# Pages with fewer visible characters than this after stripping tags are almost
# certainly JS-rendered shells — Playwright is used to get the real DOM.
_JS_PAGE_THRESHOLD = 800


def _playwright_soup(url: str) -> BeautifulSoup:
    """Render a JS-heavy page with a headless Chromium browser."""
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        try:
            ctx = browser.new_context(user_agent=USER_AGENT)
            page = ctx.new_page()
            try:
                page.goto(url, wait_until="networkidle", timeout=30_000)
            except Exception:
                pass  # networkidle timed out — take whatever rendered so far
            html = page.content()
        finally:
            browser.close()
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    return soup


def _request_soup(url: str) -> BeautifulSoup:
    resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=30)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    # If the page is a thin JS shell (common on React/Next.js faculty sites),
    # re-fetch with Playwright so the real DOM content is available.
    if len(soup.get_text(separator=" ", strip=True)) < _JS_PAGE_THRESHOLD:
        try:
            return _playwright_soup(url)
        except Exception:
            pass  # Playwright not installed or failed — use what requests got
    return soup


def _decode_cf_email(encoded: str) -> str:
    """Decode a Cloudflare-obfuscated email (XOR cipher, key = first byte)."""
    try:
        key = int(encoded[:2], 16)
        return "".join(chr(int(encoded[i:i+2], 16) ^ key) for i in range(2, len(encoded), 2))
    except Exception:
        return ""


def _soup_text(soup: BeautifulSoup, base_url: str) -> str:
    # Decode Cloudflare email-protection spans before any text extraction.
    # Format 1: <span class="__cf_email__" data-cfemail="HEX">
    for span in soup.find_all("span", class_="__cf_email__"):
        encoded = span.get("data-cfemail", "")
        if encoded:
            email = _decode_cf_email(encoded)
            if email:
                span.replace_with(f" <{email}>")

    # Surface links inline so the model sees both emails AND profile-page URLs
    # (directory listings usually only link to each person's profile page).
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        low = href.lower()
        if low.startswith("mailto:"):
            a.append(f" <{href[7:]}>")
        # Format 2: Cloudflare /cdn-cgi/l/email-protection#HEX links
        elif "/cdn-cgi/l/email-protection#" in low:
            encoded = href.split("#", 1)[-1]
            email = _decode_cf_email(encoded)
            if email:
                a.append(f" <{email}>")
        elif low.startswith(("http://", "https://")) or href.startswith("/"):
            a.append(f" <{urljoin(base_url, href)}>")
    text = soup.get_text(separator="\n")
    return "\n".join(ln.strip() for ln in text.splitlines() if ln.strip())


def _fetch_text(url: str) -> str:
    return _soup_text(_request_soup(url), url)


_NEXT_TEXTS = {"next", "next page", "next ›", "next »", "›", "»", "→",
               "more", "load more", "show more", "older"}


def _next_page_urls(soup: BeautifulSoup, base_url: str) -> list[str]:
    """Find 'next page' / pagination links on a listing (same host only)."""
    host = urlparse(base_url).netloc
    out: list[str] = []
    seen: set[str] = set()
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if not href or href.startswith(("#", "javascript:", "mailto:", "tel:")):
            continue
        rel = " ".join(a.get("rel") or []).lower()
        txt = (a.get_text() or "").strip().lower()
        if rel == "next" or txt in _NEXT_TEXTS or "page=" in href.lower():
            absu = urljoin(base_url, href)
            if urlparse(absu).netloc == host and absu not in seen:
                seen.add(absu)
                out.append(absu)
    return out


def fetch_page_text(url: str) -> str:
    """Public wrapper so the drafter can pull a professor's profile page."""
    return _fetch_text(url)


# ── Research-field filter ────────────────────────────────────────────────────
FIELD_FILTER_SCHEMA = {
    "type": "object",
    "properties": {
        "is_match": {"type": "boolean"},
        "reason": {"type": "string"},
    },
    "required": ["is_match", "reason"],
    "additionalProperties": False,
}

FIELD_FILTER_PROMPT = """Filter this researcher for a chemistry M.S. graduate (biophysical \
chemistry focus) seeking a full-time research scientist role in industry or academia. \
Keep only contacts whose work involves experimental chemistry — spectroscopic, structural, \
or wet-lab techniques applied to molecular problems.

KEEP (is_match = true):
- Drug discovery / medicinal chemistry: target engagement, biophysical characterization \
of drug-protein interactions, structural biology for drug design
- Biophysical characterization: NMR, cryo-EM, X-ray crystallography, HDX-MS, native MS, \
SPR, ITC, fluorescence, FRET used on proteins, nucleic acids, or small molecules
- Chemical biology: enzyme mechanism, chemical probes, proximity labeling, bioconjugation, \
protein engineering with chemical tools
- Materials / biomaterials with chemistry focus: peptide assembly, polymer chemistry, \
surface functionalization studied with spectroscopic tools
- Analytical method development: mass spectrometry, chromatography, structural proteomics
- Computational chemistry with experimental validation: MD, free energy calculations \
paired with wet-lab work

FILTER OUT (is_match = false):
- Pure software / ML / AI roles with no wet-lab chemistry
- Clinical, medical, or patient-facing roles
- Pure genomics, transcriptomics, or sequencing-based biology
- Business development, sales, or commercial roles
- Operations, manufacturing, or QC/QA without research component
- Purely theoretical or computational roles with no experimental component

Researcher: {name}
Title: {title}
Research interests: {research_interests}

Respond with is_match (true/false) and a one-sentence reason."""


def _check_research_fit(client, model: str, name: str, title: str, interests: str) -> tuple[bool, str]:
    """Ask Claude if this professor's research matches the target fields."""
    if not interests and not title:
        return True, "no info to filter on — keeping"
    try:
        resp = client.messages.create(
            model=model,
            max_tokens=150,
            output_config={
                "effort": "low",
                "format": {"type": "json_schema", "schema": FIELD_FILTER_SCHEMA},
            },
            messages=[{"role": "user", "content": FIELD_FILTER_PROMPT.format(
                name=name or "(unknown)",
                title=title or "",
                research_interests=interests or "(none listed)",
            )}],
        )
        text = next((b.text for b in resp.content if b.type == "text"), "")
        data = json.loads(text)
        return bool(data.get("is_match", True)), (data.get("reason") or "")
    except Exception as e:
        return True, f"filter error ({e}) — keeping"


# ── Personal research-website finder ────────────────────────────────────────
_RESEARCH_SITE_TOOL = {
    "type": "web_search_20260209",
    "name": "web_search",
    "max_uses": 2,
}


def _find_research_website(client, model: str, name: str, affiliation: str) -> str:
    """Web-search for a professor's personal lab/research website. Returns URL or ''."""
    _SKIP = ("scholar.google", "researchgate", "linkedin", "orcid",
             "pubmed", "ncbi.nlm", "academia.edu", "semanticscholar",
             "/faculty/", "/people/", "/directory/")
    try:
        aff_clause = f" at {affiliation}" if affiliation else ""
        messages = [{"role": "user", "content": (
            f"Find the personal lab or research website for Professor {name}{aff_clause}. "
            f"Return only the URL. NOT a university faculty directory, NOT Google Scholar, "
            f"NOT ResearchGate. A dedicated lab site or personal academic homepage."
        )}]
        for _ in range(3):
            resp = client.messages.create(
                model=model,
                max_tokens=200,
                tools=[_RESEARCH_SITE_TOOL],
                output_config={"effort": "low"},
                messages=messages,
            )
            if resp.stop_reason == "pause_turn":
                messages.append({"role": "assistant", "content": resp.content})
                continue
            break
        text = "\n".join(b.text for b in resp.content if b.type == "text").strip()
        # Exclude * and [] so markdown like **URL** or [URL] doesn't bleed in.
        match = re.search(r'https?://[^\s<>"*\[\]]+', text)
        if match:
            url = match.group(0).rstrip(".,)>]\"'")
            if url.startswith("http") and not any(s in url.lower() for s in _SKIP):
                return url
        return ""
    except Exception:
        return ""


# Schema + prompt for AI-powered profile-page analysis (verify person + find email + research).
PROFILE_SCHEMA = {
    "type": "object",
    "properties": {
        "is_real_person": {"type": "boolean"},
        "email": {"type": "string"},
        "research_interests": {"type": "string"},
    },
    "required": ["is_real_person", "email", "research_interests"],
    "additionalProperties": False,
}

PROFILE_PROMPT = """You are analyzing a researcher's profile or lab website page.

Answer three questions:
1. is_real_person — true if this page is for a single real individual (faculty, postdoc,
   researcher, lecturer, scientist). Set false if it belongs to a lab group, department,
   program, or other collective entity.
2. email — look HARD for a personal email address. Universities hide emails in many ways:
   - Obfuscated text: "jdoe [at] university [dot] edu" -> "jdoe@university.edu"
   - Spaced letters: "j d o e @ c a l t e c h . e d u" -> reassemble it
   - Split format: "jdoe" "at" "caltech.edu" -> "jdoe@caltech.edu"
   - Written as prose: "email me at jdoe at caltech dot edu"
   De-obfuscate whatever format and return the clean address.
   Use "" ONLY if truly no email exists anywhere on the page.
   Never return a generic department address (info@, chair@, admin@, contact@, biophys@, etc.).
3. research_interests — a concise summary (1-3 sentences or comma-separated list) of the
   person's research focus, techniques, and biological questions. Pull from the "Research",
   "About", or "Overview" section. Use "" if nothing is available.

PAGE TEXT:
---
{page_text}
---"""


def _analyze_profile(client, model: str, page_text: str) -> dict:
    """AI-analyze a profile page: verify it's a real person, find email, extract research."""
    page_text = page_text[:60_000]
    resp = client.messages.create(
        model=model,
        max_tokens=400,
        output_config={
            "effort": "low",
            "format": {"type": "json_schema", "schema": PROFILE_SCHEMA},
        },
        messages=[{"role": "user", "content": PROFILE_PROMPT.format(page_text=page_text)}],
    )
    text = next((b.text for b in resp.content if b.type == "text"), "")
    data = json.loads(text)
    return {
        "is_real_person": bool(data.get("is_real_person", True)),
        "email": (data.get("email") or "").strip().lower(),
        "research_interests": (data.get("research_interests") or "").strip(),
    }


def _extract_people(client, model: str, effort: str, page_text: str) -> list[dict]:
    # Large pages: cap to keep the request sane. Most directory pages fit easily.
    page_text = page_text[:120_000]
    resp = client.messages.create(
        model=model,
        max_tokens=16000,
        output_config={
            "effort": effort,
            "format": {"type": "json_schema", "schema": EXTRACT_SCHEMA},
        },
        messages=[{"role": "user", "content": EXTRACT_PROMPT.format(page_text=page_text)}],
    )
    text = next((b.text for b in resp.content if b.type == "text"), "")
    data = json.loads(text)
    return data.get("people", [])


def _migrate_targets(targets_path: Path) -> None:
    """Add the `category` column to a pre-existing targets.csv (default research),
    so appends stay column-aligned."""
    if not targets_path.exists():
        return
    with targets_path.open("r", encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        existing_fields = reader.fieldnames or []
        if "category" in existing_fields:
            return
        legacy_rows = list(reader)
    with targets_path.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=CSV_FIELDS)
        w.writeheader()
        for r in legacy_rows:
            r.setdefault("category", "research")
            w.writerow({k: r.get(k, "") for k in CSV_FIELDS})


def save_contacts(rows: list[dict], category: str = "research", log=print) -> int:
    """Append normalized contact rows to targets.csv with de-duplication.

    Used by the agentic finders (find_academics / find_jobs). Dedupes by email,
    else by profile_url. Returns the number of new contacts written.
    """
    cfg = load_config()
    targets_path = resolve(cfg["paths"]["targets"])
    targets_path.parent.mkdir(parents=True, exist_ok=True)
    _migrate_targets(targets_path)

    seen_emails, seen_profiles = _load_existing(targets_path)
    write_header = not targets_path.exists()
    added = 0
    with targets_path.open("a", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=CSV_FIELDS)
        if write_header:
            writer.writeheader()
        for r in rows:
            email = (r.get("email") or "").strip().lower()
            profile = (r.get("profile_url") or "").strip()
            has_email = bool(email and "@" in email)
            if not has_email and not profile:
                continue
            if has_email and email in seen_emails:
                continue
            if not has_email and profile in seen_profiles:
                continue
            row = {k: (r.get(k) or "") for k in CSV_FIELDS}
            row["email"] = email
            row["category"] = category
            writer.writerow(row)
            fh.flush()  # visible to the Contacts page immediately
            if has_email:
                seen_emails.add(email)
            if profile:
                seen_profiles.add(profile)
            added += 1
    return added


def _load_existing(targets_path: Path) -> tuple[set[str], set[str]]:
    """Return (seen_emails, seen_profile_urls) for deduplication."""
    if not targets_path.exists():
        return set(), set()
    emails: set[str] = set()
    profiles: set[str] = set()
    with targets_path.open("r", encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            e = (row.get("email") or "").strip().lower()
            p = (row.get("profile_url") or "").strip()
            if e:
                emails.add(e)
            if p:
                profiles.add(p)
    return emails, profiles


def run(log=print, extra_urls: list[str] | None = None, category: str = "research") -> dict:
    cfg = load_config()
    client = build_anthropic_client()  # raises PipelineError if no credential

    urls_path = resolve(cfg["paths"]["directory_urls"])
    targets_path = resolve(cfg["paths"]["targets"])
    targets_path.parent.mkdir(parents=True, exist_ok=True)

    # Migrate older targets.csv files that predate the `category` column.
    _migrate_targets(targets_path)

    model = cfg["model"]["name"]
    effort = cfg["model"].get("effort", "high")

    if extra_urls:
        # Discovery mode: use the provided URLs directly, skip directory_urls.txt.
        urls = extra_urls
        log(f"Using {len(urls)} discovered URL(s).")
    else:
        raw_lines = urls_path.read_text(encoding="utf-8").splitlines()
        log("Resolving organizations to directory URLs...")
        urls = resolve_entries(raw_lines, client, model, log=log)
    if not urls:
        raise PipelineError("No schools or URLs found. Add at least one school name or URL on the Setup page.")
    scfg = cfg.get("scraping", {}) or {}
    respect_robots = bool(scfg.get("respect_robots", True))
    follow_profiles = bool(scfg.get("follow_profiles", True))
    verify_persons = bool(scfg.get("verify_persons", False))
    filter_by_research = bool(scfg.get("filter_by_research", False))
    max_profile_fetches = int(scfg.get("max_profile_fetches", 80))
    max_pages = int(scfg.get("max_pages", 12))

    seen_emails, seen_profiles = _load_existing(targets_path)
    added_total = 0
    profile_fetches = 0

    # Write contacts as they're found (so they appear on the Contacts page mid-run).
    write_header = not targets_path.exists()
    out_file = targets_path.open("a", encoding="utf-8", newline="")
    writer = csv.DictWriter(out_file, fieldnames=CSV_FIELDS)
    if write_header:
        writer.writeheader()
        out_file.flush()

    def process_people(people: list[dict], page_url: str) -> int:
        nonlocal added_total, profile_fetches
        added = 0
        for p in people:
            name = p.get("name", "").strip()
            email = (p.get("email") or "").strip().lower()
            profile_url = (p.get("profile_url") or "").strip()
            if profile_url:
                profile_url = urljoin(page_url, profile_url)  # relative -> absolute

            interests = (p.get("research_interests") or "").strip()
            title = (p.get("title") or "").strip()
            affiliation = (p.get("affiliation") or "").strip()
            has_email = bool(email and "@" in email)

            # Step 1: skip non-research roles before any network calls.
            _NON_RESEARCH_TITLES = (
                "lecturer", "instructor", "teaching professor", "adjunct",
                "visiting lecturer", "senior lecturer", "clinical", "emeritus",
            )
            if title and any(t in title.lower() for t in _NON_RESEARCH_TITLES):
                log(f"  ({name}: skipped — non-research title: {title})")
                continue

            # Step 2: scrape the profile page to get email + research interests.
            # This is always fast (no extra API search) and gives us the base info.
            if follow_profiles and profile_url and profile_fetches < max_profile_fetches:  # Step 2
                profile_fetches += 1
                try:
                    page_text = _fetch_text(profile_url)
                    result = _analyze_profile(client, model, page_text)
                    if verify_persons and not result["is_real_person"]:
                        log(f"  ({name}: AI says not an individual — skipped)")
                        time.sleep(1)
                        continue
                    if not has_email:
                        email = result["email"] or _pick_email(page_text, name)
                        has_email = bool(email and "@" in email)
                    if result["research_interests"]:
                        interests = result["research_interests"]
                except Exception as e:
                    log(f"  ({name}: profile error: {e})")
                time.sleep(1)

            # Step 3: if we still lack an email, search for their personal lab site.
            # Skipping this when we already have email keeps the scraper fast.
            research_site = ""
            if follow_profiles and not has_email and name and profile_fetches < max_profile_fetches:
                research_site = _find_research_website(client, model, name, affiliation)
                if research_site:
                    log(f"  ({name}: personal site → {research_site})")
                    profile_fetches += 1
                    try:
                        page_text = _fetch_text(research_site)
                        result = _analyze_profile(client, model, page_text)
                        email = result["email"] or _pick_email(page_text, name)
                        has_email = bool(email and "@" in email)
                        if result["research_interests"]:
                            interests = result["research_interests"]
                    except Exception as e:
                        log(f"  ({name}: personal site error: {e})")
                    time.sleep(1)

            # Step 4: RocketReach-style email discovery (web search + SMTP verify).
            # Only runs when we still have no email after steps 2 and 3.
            # Use research_site as the domain source if profile_url was empty.
            if not has_email and name:
                domain = emailfinder.domain_from_url(profile_url or research_site)
                if domain:
                    found = emailfinder.find_email(
                        name, domain,
                        client=client, model=model,
                        affiliation=affiliation,
                        log=log,
                    )
                    if found:
                        email = found
                        has_email = True

            # Step 5: filter with the real research info we now have.
            if filter_by_research:
                if not interests:
                    log(f"  ({name}: filtered out — no research description found)")
                    continue
                match, reason = _check_research_fit(client, model, name, title, interests)
                if not match:
                    log(f"  ({name}: filtered out — {reason})")
                    continue

            # Store personal site as profile_url so the drafter uses the richer page.
            if research_site:
                profile_url = research_site

            email = (email or "").strip().lower()
            has_email = bool(email and "@" in email)

            # Skip if we have nothing to identify this person.
            if not has_email and not profile_url:
                continue
            # Skip duplicates: an email match always wins; for email-less contacts
            # use profile_url so sites like Caltech (no public emails) still get saved.
            if has_email and email in seen_emails:
                continue
            if not has_email and profile_url in seen_profiles:
                continue

            if has_email:
                seen_emails.add(email)
            if profile_url:
                seen_profiles.add(profile_url)

            writer.writerow(
                {
                    "name": name,
                    "email": email,
                    "title": title,
                    "affiliation": affiliation,
                    "research_interests": interests,
                    "profile_url": profile_url,
                    "source_url": page_url,
                    "category": category,
                }
            )
            out_file.flush()  # make the row visible to the Contacts page now
            added += 1
            added_total += 1
        return added

    try:
        for start_url in urls:
            log(f"→ {start_url}")
            if respect_robots and not _check_robots(start_url):
                log("  robots.txt disallows this page — skipping. "
                    "(Untick 'Respect robots.txt' on the Setup page to override.)")
                continue

            queue = [start_url]
            visited: set[str] = set()
            pages = 0
            while queue and pages < max_pages:
                page_url = queue.pop(0)
                if page_url in visited:
                    continue
                visited.add(page_url)
                pages += 1
                try:
                    soup = _request_soup(page_url)
                except Exception as e:
                    log(f"  fetch failed: {e}")
                    continue
                try:
                    people = _extract_people(client, model, effort, _soup_text(soup, page_url))
                except Exception as e:
                    log(f"  extraction failed: {e}")
                    continue

                added = process_people(people, page_url)
                label = f"  page {pages}" if pages > 1 else "  listing"
                log(f"{label}: {len(people)} people, {added} new contacts.")

                for nxt in _next_page_urls(soup, page_url):
                    if nxt not in visited and nxt not in queue:
                        queue.append(nxt)
                time.sleep(1)  # be polite between pages
    finally:
        out_file.close()

    log(f"Done. Added {added_total} new contacts.")
    return {"added": added_total, "urls": len(urls)}
