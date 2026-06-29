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


def discover_orgs(query: str, client, model: str, log=print) -> list[str]:
    """Web-search for industry organizations in the given field.

    Returns a list of researcher-directory URLs to feed into the scraper.
    """
    log(f"Searching for organizations in: {query}...")
    prompt = _DISCOVER_PROMPT.format(query=query)
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
