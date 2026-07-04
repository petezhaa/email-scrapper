# -*- coding: utf-8 -*-
"""Resolve organization names to researcher-directory URLs.

Entries can be:
  - A plain URL (http/https) — passed through unchanged.
  - A name (university, company, or national lab) — looked up in KNOWN_ORGS
    first, then web-searched as a fallback.

The web-search fallback fires one Claude call with the web_search tool and
returns the best researcher-directory URL it finds. If nothing is found the
entry is skipped with a warning.
"""
from __future__ import annotations

import difflib
import json
import re

# ── Built-in lookup table ──────────────────────────────────────────────────────
# Single source of truth: canonical name → URLs.
# Aliases are defined separately and resolved at module load.
# To update a school's URLs, edit only the canonical entry below.
_CANONICAL: dict[str, list[str]] = {
    # UC System
    "uc berkeley": [
        "https://chemistry.berkeley.edu/people/faculty",
    ],
    "ucsf": [
        "https://biophysics.ucsf.edu/people/faculty",
    ],
    "uc san diego": [
        "https://chemistry.ucsd.edu/faculty-research/faculty-profiles/index.html",
    ],
    "ucla": [
        "https://www.chemistry.ucla.edu/faculty-and-research/",
    ],
    "uc santa barbara": [
        "https://www.chemistry.ucsb.edu/people/faculty",
    ],
    "uc irvine": [
        "https://www.chemistry.uci.edu/faculty/",
    ],
    # Stanford / Bay Area
    "stanford": [
        "https://chemistry.stanford.edu/people/faculty",
    ],
    "caltech": [
        "https://www.cce.caltech.edu/faculty",
    ],
    # East Coast / Ivy+
    "mit": [
        "https://chemistry.mit.edu/faculty/",
    ],
    "harvard": [
        "https://chemistry.harvard.edu/people/faculty",
    ],
    "yale": [
        "https://chem.yale.edu/people/faculty",
    ],
    "columbia": [
        "https://chem.columbia.edu/faculty",
    ],
    "princeton": [
        "https://chemistry.princeton.edu/faculty",
    ],
    "upenn": [
        "https://www.chem.upenn.edu/people/faculty",
    ],
    "cornell": [
        "https://chemistry.cornell.edu/faculty",
    ],
    # Midwest
    "university of chicago": [
        "https://chemistry.uchicago.edu/faculty",
    ],
    "university of michigan": [
        "https://lsa.umich.edu/chemistry/people/faculty.html",
    ],
    # Other strong programs
    "johns hopkins": [
        "https://chemistry.jhu.edu/directory/faculty/",
    ],
    "duke": [
        "https://chem.duke.edu/faculty",
    ],
    "vanderbilt": [
        "https://www.vanderbilt.edu/chemistry/faculty/",
    ],
    "university of washington": [
        "https://chem.washington.edu/people/faculty",
    ],
    "scripps research": [
        "https://www.scripps.edu/faculty/",
    ],
    "rockefeller university": [
        "https://www.rockefeller.edu/our-scientists/",
    ],
    "ut southwestern": [
        "https://www.utsouthwestern.edu/departments/biochemistry/",
    ],
    "cold spring harbor": [
        "https://www.cshl.edu/research/faculty-staff/",
    ],
    "northwestern": [
        "https://www.chemistry.northwestern.edu/people/core-faculty/",
    ],
    "university of illinois": [
        "https://chemistry.illinois.edu/directory/faculty",
    ],

    # ── Biotech / Pharma ──────────────────────────────────────────────────────
    "genentech": [
        "https://www.gene.com/scientists/our-scientists",
    ],
    "relay therapeutics": [
        "https://relaytx.com/our-team/",
    ],
    "recursion": [
        "https://www.recursion.com/team",
    ],
    "calico": [
        "https://www.calicolabs.com/people/",
    ],
    "insitro": [
        "https://www.insitro.com/people/",
    ],
    "merck": [
        "https://www.merck.com/research/meet-our-scientists/",
    ],

    # ── National Labs ─────────────────────────────────────────────────────────
    "argonne": [
        "https://www.anl.gov/bio/bio-staff-directory",
        "https://www.anl.gov/cse/cse-staff-directory",
    ],
}

# Aliases → canonical key. All aliases point to the same list object.
_ALIASES: dict[str, str] = {
    "berkeley": "uc berkeley",
    "uc san francisco": "ucsf",
    "ucsd": "uc san diego",
    "uc los angeles": "ucla",
    "ucsb": "uc santa barbara",
    "uchicago": "university of chicago",
    "umich": "university of michigan",
    "jhu": "johns hopkins",
    "uw": "university of washington",
    "scripps": "scripps research",
    "rockefeller": "rockefeller university",
    "utsw": "ut southwestern",
    "cshl": "cold spring harbor",
    "university of pennsylvania": "upenn",
    # Company aliases
    "relay": "relay therapeutics",
    "recursion pharmaceuticals": "recursion",
    "calico life sciences": "calico",
    "argonne national laboratory": "argonne",
    "anl": "argonne",
    "merck research labs": "merck",
    "merck research": "merck",
}

# Flat lookup dict — aliases resolve to the same list as their canonical entry.
KNOWN_SCHOOLS: dict[str, list[str]] = {**_CANONICAL}  # kept for back-compat
KNOWN_ORGS = KNOWN_SCHOOLS
for _alias, _canon in _ALIASES.items():
    KNOWN_SCHOOLS[_alias] = _CANONICAL[_canon]

# ── Normalise helpers ──────────────────────────────────────────────────────────
def _normalise(s: str) -> str:
    """Lowercase + strip common prefixes for fuzzy matching."""
    s = s.lower().strip()
    for prefix in ("university of ", "the university of ", "the "):
        if s.startswith(prefix):
            s = s[len(prefix):]
            break
    return s.strip()


def _lookup(name: str) -> tuple[list[str], str] | None:
    """Return (urls, matched_key) for a school name, or None if not in the table."""
    key = _normalise(name)
    # 1. Exact match
    if key in KNOWN_SCHOOLS:
        return KNOWN_SCHOOLS[key], key
    # 2. Substring match
    for k, urls in KNOWN_SCHOOLS.items():
        if key in k or k in key:
            return urls, k
    # 3. Fuzzy match (handles typos like "Miy" → "mit", "Berkely" → "uc berkeley")
    matches = difflib.get_close_matches(key, KNOWN_SCHOOLS.keys(), n=1, cutoff=0.6)
    if matches:
        matched = matches[0]
        return KNOWN_SCHOOLS[matched], matched
    return None


# ── Web-search fallback ────────────────────────────────────────────────────────
_SEARCH_SCHEMA = {
    "type": "object",
    "properties": {
        "urls": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Faculty directory page URLs found, most relevant first",
        }
    },
    "required": ["urls"],
    "additionalProperties": False,
}

_SEARCH_TOOL = {
    "type": "web_search_20260209",
    "name": "web_search",
    "max_uses": 3,
}

_SEARCH_PROMPT = """\
Find the researcher directory page for "{school}". This may be a university chemistry / \
biochemistry / biophysics department, a biotech or pharma company, or a national lab.

Return the direct URL of the page that lists individual researchers by name with links to \
their profiles or bios — not a job board, not a general "about us" page, not a single \
person's profile. The actual team or faculty listing page. Return up to 3 URLs, most \
relevant first."""

# ── Industry discovery ────────────────────────────────────────────────────────

_DISCOVER_TOOL = {
    "type": "web_search_20260209",
    "name": "web_search",
    "max_uses": 6,
}

_DISCOVER_PROMPT = """\
Search the web and find 8-12 biotech or pharma companies with research teams working in: {query}

Steps:
1. Search "{query} biotech company research team 2025" — find active companies
2. Search "{query} startup research scientist careers" — find more companies
3. For each company you find, search for their /team or /people page that lists \
   individual researchers by name with profile links

Only include companies where you actually found a team/people directory page URL \
(not a job board, not a careers page — the actual page with researcher names on it).

Your entire response must be ONLY this JSON array, with no text before or after it:
[
  {{"name": "Company Name", "url": "https://company.com/team", "focus": "one sentence"}},
  {{"name": "Company Name", "url": "https://company.com/people", "focus": "one sentence"}}
]"""

_DISCOVER_PROMPT_ACADEMIA = """\
Search the web and find 8-12 universities or academic research institutions with \
labs / faculty working in: {query}

Steps:
1. Search "{query} university department faculty" — find strong departments
2. Search "{query} research lab faculty directory" — find more institutions
3. For each one, find the department's faculty / people directory page that lists \
   individual professors by name with profile links

Only include institutions where you actually found a faculty/people directory page \
URL (not a general homepage, not an admissions page — the actual page listing faculty \
names with profile links).

Your entire response must be ONLY this JSON array, with no text before or after it:
[
  {{"name": "University — Department", "url": "https://university.edu/people", "focus": "one sentence"}},
  {{"name": "University — Department", "url": "https://dept.university.edu/faculty", "focus": "one sentence"}}
]"""


def discover_orgs(query: str, client, model: str, log=print, kind: str = "industry") -> list[str]:
    """Web-search for organizations in the given field.

    kind="industry" finds biotech/pharma companies; kind="academia" finds
    universities/departments. Returns researcher-directory URLs for the scraper.
    """
    where = "universities/institutions" if kind == "academia" else "organizations"
    log(f"Searching for {where} in: {query}...")
    template = _DISCOVER_PROMPT_ACADEMIA if kind == "academia" else _DISCOVER_PROMPT
    prompt = template.format(query=query)
    messages = [{"role": "user", "content": prompt}]
    try:
        for _ in range(8):
            resp = client.messages.create(
                model=model,
                max_tokens=2000,
                tools=[_DISCOVER_TOOL],
                messages=messages,
            )
            if resp.stop_reason == "pause_turn":
                messages.append({"role": "assistant", "content": resp.content})
                continue
            break
        text = "\n".join(b.text for b in resp.content if b.type == "text").strip()
    except Exception as e:
        log(f"Discovery search failed: {e}")
        return []

    # Extract JSON array from the response text (greedy — grab the whole array).
    match = re.search(r"\[.*\]", text, re.DOTALL)
    if not match:
        log(f"Discovery returned no parseable results. Raw response:\n{text[:500]}")
        return []
    try:
        orgs = json.loads(match.group(0))
    except Exception as e:
        log(f"Could not parse discovery results ({e}). Raw:\n{text[:500]}")
        return []

    urls: list[str] = []
    for org in orgs:
        url = (org.get("url") or "").strip()
        name = org.get("name", "")
        focus = org.get("focus", "")
        if url.startswith("http"):
            log(f"  Found: {name} — {focus}")
            urls.append(url)
    log(f"Discovered {len(urls)} organization(s).")
    return urls


# ── Agentic people / jobs finders ───────────────────────────────────────────
# Modern faculty and company directories are JS/search-driven, so fetching their
# HTML rarely yields the actual people. Instead we let the web-search agent browse
# directories and profile pages and return individuals (or job openings) directly.

_FINDER_TOOL = {"type": "web_search_20260209", "name": "web_search", "max_uses": 5}

_PEOPLE_SCHEMA = {
    "type": "object",
    "properties": {
        "people": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "title": {"type": "string"},
                    "affiliation": {"type": "string"},
                    "research_interests": {"type": "string"},
                    "profile_url": {"type": "string"},
                    "email": {"type": "string"},
                },
                "required": ["name", "title", "affiliation", "research_interests",
                             "profile_url", "email"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["people"],
    "additionalProperties": False,
}

_JOBS_SCHEMA = {
    "type": "object",
    "properties": {
        "jobs": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "job_title": {"type": "string"},
                    "company": {"type": "string"},
                    "job_url": {"type": "string"},
                    "summary": {"type": "string"},
                    "contact_name": {"type": "string"},
                    "contact_title": {"type": "string"},
                    "contact_email": {"type": "string"},
                    "contact_url": {"type": "string"},
                },
                "required": ["job_title", "company", "job_url", "summary",
                             "contact_name", "contact_title", "contact_email",
                             "contact_url"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["jobs"],
    "additionalProperties": False,
}

_PEOPLE_PROMPT = """\
Use web search to find {count} individual academic researchers whose work is in: {query}.

Browse university department faculty directories and the researchers' own profile pages.
For each real person, collect:
  - name: their full name
  - title: academic title (e.g. "Associate Professor")
  - affiliation: "University — Department"
  - research_interests: one sentence on what they actually study
  - profile_url: their faculty/lab profile page URL (not the directory listing)
  - email: their academic email if it is publicly listed, otherwise ""

Only include active, individual faculty — not labs, not staff pages, not emeritus.
Spread across several institutions. Leave a field "" if you truly cannot find it, but
always include name, title, affiliation, and profile_url."""

_JOBS_PROMPT = """\
Use web search to find {count} CURRENT job openings related to: {query}, at biotech,
pharma, chemistry, or research companies (industry, not academia).

Browse company careers pages and job boards. For each opening, collect:
  - job_title: the role title
  - company: the hiring company
  - job_url: a direct link to the job posting
  - summary: one sentence on the role / key requirements
  - contact_name: a specific person to reach out to (hiring manager, team lead, or
    recruiter) if you can find one, otherwise ""
  - contact_title: that person's title, otherwise ""
  - contact_email: their email if publicly findable, otherwise ""
  - contact_url: their LinkedIn or profile URL if findable, otherwise ""

Prefer real, currently-open postings. Always include job_title, company, job_url, and
summary; leave the contact_* fields "" when you cannot find a person."""


def _finder_search(prompt: str, schema: dict, client, model: str, log,
                   effort: str = "medium") -> dict:
    """Run a web-search agent turn (handling pause_turn) and parse its JSON result."""
    messages = [{"role": "user", "content": prompt}]
    resp = None
    for i in range(8):
        if i:
            # Heartbeat so the job bar shows life during the (slow) web browsing.
            log(f"  · still searching the web… (round {i + 1})")
        resp = client.messages.create(
            model=model,
            max_tokens=16000,
            tools=[_FINDER_TOOL],
            output_config={
                "effort": effort,
                "format": {"type": "json_schema", "schema": schema},
            },
            messages=messages,
        )
        if resp.stop_reason == "pause_turn":
            messages.append({"role": "assistant", "content": resp.content})
            continue
        break
    text = "".join(b.text for b in resp.content if getattr(b, "type", None) == "text").strip()
    if not text:
        return {}
    try:
        return json.loads(text)
    except (ValueError, json.JSONDecodeError):
        match = re.search(r"\{.*\}", text, re.DOTALL)
        return json.loads(match.group(0)) if match else {}


def _exclude_clause(exclude: list[str] | None) -> str:
    if not exclude:
        return ""
    names = "; ".join(x for x in exclude[:60] if x)
    if not names:
        return ""
    return ("\n\nIMPORTANT: do NOT return any of these — they are already found, "
            f"so find DIFFERENT ones:\n{names}")


def find_academics(query: str, client, model: str, log=print, count: int = 10,
                   effort: str = "medium", exclude: list[str] | None = None,
                   enrich_emails: bool = False) -> list[dict]:
    """Web-search for individual professors in a field. Returns normalized contact rows.

    enrich_emails: when True, run the email finder (web search + SMTP verify) for
    people whose email wasn't listed — slower, but turns names into sendable
    contacts (academic directories rarely publish addresses).
    """
    log(f"Searching the web for researchers in: {query} …")
    try:
        prompt = _PEOPLE_PROMPT.format(query=query, count=count) + _exclude_clause(exclude)
        data = _finder_search(prompt, _PEOPLE_SCHEMA, client, model, log, effort=effort)
    except Exception as e:
        log(f"Researcher search failed: {e}")
        return []
    out = []
    for p in data.get("people") or []:
        name = (p.get("name") or "").strip()
        if not name:
            continue
        profile = (p.get("profile_url") or "").strip()
        out.append({
            "name": name,
            "email": (p.get("email") or "").strip(),
            "title": (p.get("title") or "").strip(),
            "affiliation": (p.get("affiliation") or "").strip(),
            "research_interests": (p.get("research_interests") or "").strip(),
            "profile_url": profile,
            "source_url": profile,
        })
        log(f"  {name} — {(p.get('affiliation') or '').strip()}")
    log(f"Found {len(out)} researcher(s).")

    if enrich_emails:
        from . import emailfinder
        need = [r for r in out if not r["email"] and r["profile_url"]]
        if need:
            log(f"Looking up emails for {len(need)} researcher(s) without one — this is slower…")
            for r in need:
                domain = emailfinder.domain_from_url(r["profile_url"])
                if not domain:
                    continue
                try:
                    found = emailfinder.find_email(
                        r["name"], domain, client=client, model=model,
                        affiliation=r["affiliation"], log=log,
                    )
                except Exception as e:
                    log(f"  ({r['name']}: email lookup error: {e})")
                    continue
                if found:
                    r["email"] = found
            got = sum(1 for r in need if r["email"])
            log(f"Found emails for {got} of {len(need)}.")
    return out


def find_jobs(query: str, client, model: str, log=print, count: int = 10,
              effort: str = "medium", exclude: list[str] | None = None) -> list[dict]:
    """Web-search for industry job openings + who to contact. Returns contact rows."""
    log(f"Searching the web for job openings in: {query} …")
    try:
        prompt = _JOBS_PROMPT.format(query=query, count=count) + _exclude_clause(exclude)
        data = _finder_search(prompt, _JOBS_SCHEMA, client, model, log, effort=effort)
    except Exception as e:
        log(f"Job search failed: {e}")
        return []
    out = []
    for jb in data.get("jobs") or []:
        title = (jb.get("job_title") or "").strip()
        company = (jb.get("company") or "").strip()
        if not title and not company:
            continue
        contact = (jb.get("contact_name") or "").strip()
        contact_title = (jb.get("contact_title") or "").strip()
        job_url = (jb.get("job_url") or "").strip()
        contact_url = (jb.get("contact_url") or "").strip()
        display_title = title
        if contact and contact_title:
            display_title = f"{title} · contact: {contact_title}"
        out.append({
            "name": contact or company,
            "email": (jb.get("contact_email") or "").strip(),
            "title": display_title,
            "affiliation": company,
            "research_interests": (jb.get("summary") or "").strip(),
            "profile_url": contact_url or job_url,
            "source_url": job_url,
        })
        log(f"  {title} @ {company}" + (f" — contact {contact}" if contact else ""))
    log(f"Found {len(out)} opening(s).")
    return out


def resolve_school(name: str, client, model: str, log=print) -> list[str]:
    """
    Given a school name, return a list of faculty-directory URLs.
    Tries the lookup table first; falls back to a web search.
    Returns [] if nothing is found.
    """
    # 1. Lookup table
    result = _lookup(name)
    if result:
        known, matched = result
        label = f" (matched '{matched}')" if matched != _normalise(name) else ""
        log(f"  {name}: found {len(known)} URL(s){label}")
        return known

    # 2. Web-search fallback
    log(f"  {name}: not in lookup table — searching the web...")
    try:
        resp = client.messages.create(
            model=model,
            max_tokens=512,
            tools=[_SEARCH_TOOL],
            output_config={
                "effort": "low",
                "format": {"type": "json_schema", "schema": _SEARCH_SCHEMA},
            },
            messages=[{"role": "user", "content": _SEARCH_PROMPT.format(school=name)}],
        )
        text = next((b.text for b in resp.content if b.type == "text"), "")
        data = json.loads(text)
        urls = [u.strip() for u in (data.get("urls") or []) if u.strip().startswith("http")]
        if urls:
            log(f"  {name}: web search found {len(urls)} URL(s)")
        else:
            log(f"  {name}: web search returned no URLs — skipping")
        return urls
    except Exception as exc:
        log(f"  {name}: web search error ({exc}) — skipping")
        return []


def resolve_entries(lines: list[str], client, model: str, log=print) -> list[str]:
    """
    Take the raw lines from directory_urls.txt (names or URLs) and return
    a flat list of URLs, resolving school names along the way.
    """
    out: list[str] = []
    seen: set[str] = set()
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if re.match(r"https?://", line, re.IGNORECASE):
            if line not in seen:
                seen.add(line)
                out.append(line)
        else:
            urls = resolve_school(line, client, model, log=log)
            for u in urls:
                if u not in seen:
                    seen.add(u)
                    out.append(u)
    return out
