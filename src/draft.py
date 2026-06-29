# -*- coding: utf-8 -*-
"""Generate personalized draft emails from data/targets.csv.

Three-model pipeline per contact:
  1. Claude  (Anthropic SDK + web search) — researches the contact, builds a brief
  2. GPT     (OpenAI-compatible SDK)      — writes the email from the brief
  3. Gemini  (OpenAI-compatible SDK)      — reviews + fixes against the quality rules

Each draft is saved as a Markdown file with YAML front-matter in drafts/.
YOU review every draft before anything is sent.

Usage:
    python -m src.cli draft           # draft for everyone without a draft
    python -m src.cli draft --limit 5 # only draft the next 5
"""
from __future__ import annotations

import csv
import json
import re
from pathlib import Path

from . import scrape
from .config import (
    PipelineError,
    build_anthropic_client,
    build_reviewer_client,
    build_writer_client,
    load_config,
    load_secrets,
    resolve,
)

# How much of the professor's fetched page to feed the model (chars).
MAX_PAGE_CHARS = 6000
# How much of the student's resume to feed the model (chars).
MAX_RESUME_CHARS = 6000


def _extract_resume_text(path: Path) -> str:
    """Pull text out of the resume PDF so the email can cite real experience."""
    if not path.exists() or path.suffix.lower() != ".pdf":
        return ""
    try:
        from pypdf import PdfReader
        reader = PdfReader(str(path))
        text = "\n".join((page.extract_text() or "") for page in reader.pages)
    except Exception:
        return ""
    return " ".join(text.split())[:MAX_RESUME_CHARS]


DRAFT_SCHEMA = {
    "type": "object",
    "properties": {
        "subject": {"type": "string"},
        "body": {"type": "string"},
    },
    "required": ["subject", "body"],
    "additionalProperties": False,
}

# ── Step 1: Claude researches the professor ────────────────────────────────────

WEB_SEARCH_TOOL = {"type": "web_search_20260209", "name": "web_search", "max_uses": 2}

WEB_RESEARCH_PROMPT = """You are helping a chemist write a targeted cold email to an \
industry researcher for a full-time research scientist position. Search thoroughly and \
build a research brief so the email opens with a specific, well-informed hook.

Researcher: {name}
Company / Affiliation: {affiliation}
Known focus: {interests}

Do the following searches:
1. Search "{name} {affiliation} research" to find their recent publications or work.
2. Search "{name} {affiliation}" to find their LinkedIn, company bio, or lab page.
3. Search "{affiliation} research pipeline" or "{affiliation} research focus" to understand \
what the company's research team is working on.

The applicant's techniques: NMR spectroscopy (multinuclear, PRE, multiple-quantum filtration), \
AFM-IR (Nano-IR), XL-MS, FRET, solution biophysics. When choosing a hook, prefer work that \
connects to these specific methods.

Return a plain-text brief with ALL of the following (if findable):

BEST HOOK: The most specific connection between this person's/company's research and the \
applicant's background. Could be a published paper, a patent, a known product/platform, or \
a specific research focus the company is known for. Format: "[Year if applicable] [source] — \
[1-2 sentences on what it is and exactly why the applicant's techniques are relevant]." \
This is the most important field.

SECOND HOOK: A second specific angle if one exists (different technique or project). \
Leave blank if nothing fits well — do not force it.

CURRENT RESEARCH FOCUS: 2-3 sentences on what this team/person is actively working on \
now — the problems, model systems, and specific techniques they use.

COMPANY CONTEXT: What the company does, its pipeline/products, and where research fits \
in their work.

Be accurate and specific. Only include things you actually found via search. \
If you cannot confidently identify this person's research, reply with exactly: \
NO_RELIABLE_INFO"""


def _web_research(client, model: str, name: str, affiliation: str, interests: str, log) -> str:
    """Claude: web-search the professor and return a plain-text research brief (or '')."""
    if not name:
        return ""
    prompt = WEB_RESEARCH_PROMPT.format(
        name=name, affiliation=affiliation or "(unknown)", interests=interests or "(none listed)"
    )
    messages = [{"role": "user", "content": prompt}]
    try:
        for _ in range(3):
            resp = client.messages.create(
                model=model, max_tokens=1500, tools=[WEB_SEARCH_TOOL], messages=messages
            )
            if resp.stop_reason == "pause_turn":
                messages.append({"role": "assistant", "content": resp.content})
                continue
            break
        text = "\n".join(b.text for b in resp.content if b.type == "text").strip()
    except Exception as e:
        log(f"  (web research failed for {name}: {e})")
        return ""
    if not text or "NO_RELIABLE_INFO" in text:
        return ""
    return text[:MAX_PAGE_CHARS]


# ── Step 2: GPT writes the email ──────────────────────────────────────────────

WRITER_SYSTEM_PROMPT = """You are writing a cold outreach email on behalf of Dennis Rui \
for a full-time research scientist position. Your only job is to write the hook — one \
paragraph that connects Dennis's real techniques to something specific about the recipient's \
work. The rest of the email follows a fixed template; a reviewer will enforce it.

WHAT MAKES THE HOOK WORK:
Industry researchers delete generic emails immediately. This one survives because Dennis \
knows their specific work. Name something concrete (a paper, patent, product, or platform), \
say one precise thing about what it does or found, then connect it directly to a technique \
Dennis has actually used. The connection must feel inevitable — like he is the natural \
person for this team. One specific sentence beats three vague ones.

THE HOOK MUST:
- Name the specific paper/platform/pipeline (not "your drug discovery work")
- Say what it found or does — one concrete detail, not the topic area
- Connect to a real technique Dennis has used with his hands
- Be 2-4 sentences maximum

DENNIS'S TECHNIQUES (use these for the bridge):
- Multinuclear NMR, NMR-PRE, multiple-quantum filtration (ion binding, chaperone contacts)
- AFM-IR / Nano-IR (nanoscale chemical imaging)
- XL-MS (protein complex mapping)
- FRET (conformational dynamics)
- Python scripting, ORCA simulations for spectroscopic data

FIXED EMAIL STRUCTURE — write to fill this exactly:
---
Hi [First],

My name is Dennis Rui, and I am a recent Chemistry BA/MS graduate from Northwestern \
University. [HOOK PARAGRAPH — this is what you write]

My background in research encompasses:

-  Wet-Lab: Experience synthesizing biomaterials and utilizing FPLC-based protein \
purification workflows.

-  Spectroscopy: Probing transient and non-equilibrium states using multi-nuclei, \
multi-phase NMR, AFM-IR, and FRET.

-  Computational Modeling: Building custom Python scripts and running high-performance \
simulations (e.g., ORCA) to process and analyze heavy spectroscopic data.

I would be grateful for the opportunity to learn more about any potential opportunities. \
My CV is attached below and thank you for your time!

Best,

Dennis
---

VOICE:
- Write the hook like a scientist talking to another scientist, not a student applying.
- Short sentences land harder than long ones.
- Warmth comes from specificity, not adjectives. Never say "fascinating" or "exciting."
- Dennis is offering value, not asking for a favor. Write from that posture.

Only use facts from the inputs. Return JSON: {"subject": "...", "body": "..."} \
where body is the COMPLETE email (greeting through sign-off), with [HOOK PARAGRAPH] \
replaced by the actual hook you wrote."""

WRITER_USER_PROMPT = """Applicant profile:
<profile>
{profile}
</profile>
{resume_section}
Recipient:
- Name: {name}
- Title: {title}
- Company / Affiliation: {affiliation}
- Research focus: {research_interests}

{page_section}

Research brief from web search (build the hook from BEST HOOK; cite only what is listed here):
<research_brief>
{research_brief}
</research_brief>

Write the email. Greet as: "Hi {first_name}," (always first name — industry culture).
Sign off as: {sender_name}
Return JSON: {{"subject": "...", "body": "..."}}"""


def _write_email(
    oai_client,
    model: str,
    name: str,
    title: str,
    affiliation: str,
    interests: str,
    profile: str,
    resume_section: str,
    page_section: str,
    research_brief: str,
    sender_name: str,
    log,
) -> dict:
    """GPT: write the email from the research brief. Returns {subject, body}."""
    prompt = WRITER_USER_PROMPT.format(
        profile=profile,
        resume_section=resume_section,
        name=name or "(unknown)",
        title=title or "(unknown)",
        affiliation=affiliation or "(unknown)",
        research_interests=interests or "(none listed)",
        page_section=page_section,
        research_brief=research_brief or "(no web research available — use directory interests only)",
        first_name=_first_name(name) or "(name)",
        sender_name=sender_name,
    )
    resp = oai_client.chat.completions.create(
        model=model,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": WRITER_SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
    )
    text = resp.choices[0].message.content or ""
    return json.loads(text)


# ── Step 3: Gemini reviews the email ──────────────────────────────────────────

REVIEWER_PROMPT = """You edit cold outreach emails written by Dennis Rui for industry \
research scientist positions. Fix every rule violation and return ONLY the corrected \
email body — no explanation, no preamble, no markdown.

FIXED TEMPLATE (the email must follow this structure exactly — only the hook changes):

Hi [First],

My name is Dennis Rui, and I am a recent Chemistry BA/MS graduate from Northwestern \
University. [Hook paragraph]

My background in research encompasses:

-  Wet-Lab: Experience synthesizing biomaterials and utilizing FPLC-based protein \
purification workflows.

-  Spectroscopy: Probing transient and non-equilibrium states using multi-nuclei, \
multi-phase NMR, AFM-IR, and FRET.

-  Computational Modeling: Building custom Python scripts and running high-performance \
simulations (e.g., ORCA) to process and analyze heavy spectroscopic data.

I would be grateful for the opportunity to learn more about any potential opportunities. \
My CV is attached below and thank you for your time!

Best,

Dennis

---

RULES (fix every failure):
1. Greeting: "Hi [First name]," — never "Dear", never "Hi Professor", never full name.
2. P1 intro sentence must be word-for-word: "My name is Dennis Rui, and I am a recent \
Chemistry BA/MS graduate from Northwestern University." Do not paraphrase it.
3. Hook paragraph: must name something specific (a paper, patent, product, or platform) \
and what it does or found. "Your work in drug discovery" is a FAIL. Must connect to a \
real technique Dennis has used (NMR, AFM-IR, XL-MS, FRET, Python/ORCA).
4. Background block: must match the template exactly — 3 bullets (Wet-Lab, Spectroscopy, \
Computational Modeling) with the exact text above. Do not shorten, reorder, or rephrase.
5. Closing sentence must be word-for-word: "I would be grateful for the opportunity to \
learn more about any potential opportunities. My CV is attached below and thank you for \
your time!"
6. Sign-off: "Best," blank line, "Dennis" — nothing else.
7. No em dashes anywhere. No links or contact info in the body.
8. No academic jargon: no "principal investigator", no "PI", no "post-bacc", no "PhD programs."
9. No filler: "passionate about", "fascinated by", "excited to", "would love the opportunity", \
"iterating", "leveraging", "synergy", "robust", "novel", "pioneering", "groundbreaking", \
"align", "spearheading."

EMAIL TO FIX:
---
{body}
---"""


def _review_email(oai_client, model: str, body: str, log, name: str = "") -> str:
    """Gemini: review the email body and return the fixed version."""
    try:
        resp = oai_client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": REVIEWER_PROMPT.format(body=body)}],
        )
        fixed = (resp.choices[0].message.content or "").strip()
        return fixed if fixed else body
    except Exception as e:
        log(f"  (review failed for {name}: {e})")
        return body


# ── Shared helpers ─────────────────────────────────────────────────────────────

RESUME_SECTION_TEMPLATE = """
The student's resume (cite concrete, real experience from here or the profile — \
never invent anything beyond these):

<resume>
{resume_text}
</resume>
"""

PAGE_SECTION_TEMPLATE = """Content from the recipient's own profile/research page \
(use this to find the specific connection — quote or paraphrase a concrete detail):

<recipient_page>
{page_text}
</recipient_page>"""

NO_PAGE_SECTION = (
    "No profile page was available for this person — rely on the research "
    "interests above and keep specifics modest."
)


def _fetch_profile_context(target: dict, log) -> str:
    """Best-effort fetch of the professor's profile page text for grounding."""
    url = (target.get("profile_url") or "").strip()
    if not url:
        return ""
    try:
        text = scrape.fetch_page_text(url)
    except Exception as e:
        log(f"  (couldn't fetch profile page {url}: {e})")
        return ""
    return text[:MAX_PAGE_CHARS]


def _slug(email: str) -> str:
    return re.sub(r"[^a-z0-9._-]+", "_", email.strip().lower())


def _no_dashes(text: str) -> str:
    """Remove em/en dashes (and '--') the model may slip in, tidying spacing."""
    text = text.replace("—", ", ").replace("–", ", ").replace("--", ", ")
    text = re.sub(r"\s+([,.;:!?])", r"\1", text)
    text = re.sub(r",\s*,", ", ", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    return text


def _last_name(full_name: str) -> str:
    parts = [p for p in re.split(r"\s+", full_name.strip()) if p]
    return parts[-1] if parts else full_name.strip()


def _first_name(full_name: str) -> str:
    parts = [p for p in re.split(r"\s+", full_name.strip()) if p]
    return parts[0] if parts else full_name.strip()


def write_draft_file(
    path: Path,
    *,
    to: str,
    name: str,
    subject: str,
    body: str,
    source_url: str,
    status: str = "pending",
) -> None:
    """Write a draft as Markdown with simple front-matter."""
    front = (
        "---\n"
        f"to: {json.dumps(to)}\n"
        f"name: {json.dumps(name)}\n"
        f"subject: {json.dumps(subject)}\n"
        f"status: {json.dumps(status)}   # pending | approved | skip | sent\n"
        f"source_url: {json.dumps(source_url)}\n"
        "---\n\n"
    )
    path.write_text(front + body.strip() + "\n", encoding="utf-8")


# ── Main entry point ───────────────────────────────────────────────────────────

def run(limit: int | None = None, log=print, keep_going=None) -> dict:
    """Generate drafts for all contacts that don't have one yet.

    keep_going: optional callable that returns True while scraping is still
    running. When provided the drafter loops, re-reading the CSV for new
    contacts every 15 seconds, until keep_going() returns False.
    """
    import time as _time

    cfg = load_config()
    claude_client = build_anthropic_client()
    writer_client = build_writer_client()

    targets_path = resolve(cfg["paths"]["targets"])
    profile_path = resolve(cfg["paths"]["profile"])
    drafts_dir = resolve(cfg["paths"]["drafts_dir"])
    drafts_dir.mkdir(parents=True, exist_ok=True)

    if not targets_path.exists():
        raise PipelineError("No contacts yet. Scrape some schools on the Setup page first.")
    profile = profile_path.read_text(encoding="utf-8")
    sender_name = cfg["sender"]["name"]
    if not sender_name.strip():
        raise PipelineError(
            "Sender name is not set. Fill in your name on the Setup page and save."
        )

    resume_text = _extract_resume_text(resolve(cfg["paths"]["resume"]))
    resume_section = (
        RESUME_SECTION_TEMPLATE.format(resume_text=resume_text) if resume_text else ""
    )
    if resume_text:
        log("Using your resume contents to ground the emails.")
    else:
        log("No readable resume PDF found — using your typed profile only.")

    claude_model = cfg["model"]["name"]
    writer_model = cfg["model"].get("writer", "openai/gpt-4o")
    reviewer_model = cfg["model"].get("reviewer", "google/gemini-2.0-flash-001")
    web_research = bool(cfg.get("drafting", {}).get("web_research", True))
    quality_review = bool(cfg.get("drafting", {}).get("quality_review", True))

    # Only build the reviewer client when the quality-review step is enabled —
    # users without Gemini credentials can disable it without getting errors.
    reviewer_client = build_reviewer_client() if quality_review else None

    log(f"Pipeline: Claude ({claude_model}) → GPT ({writer_model})" + (f" → Gemini ({reviewer_model})" if quality_review else ""))
    if web_research:
        log("Web research ON — Claude will look up each professor's recent papers.")

    made = 0

    while True:
        with targets_path.open("r", encoding="utf-8", newline="") as f:
            targets = list(csv.DictReader(f))

        made_this_pass = 0
        for t in targets:
            email = (t.get("email") or "").strip()
            name = t.get("name", "").strip()
            if email:
                slug = _slug(email)
            elif name:
                slug = _slug(name)
            else:
                slug = _slug(t.get("profile_url", "") or t.get("source_url", "unknown"))
            draft_path = drafts_dir / f"{slug}.md"
            if draft_path.exists():
                continue
            if limit is not None and made >= limit:
                break

            affiliation = t.get("affiliation", "").strip()
            interests = t.get("research_interests", "").strip()
            title = t.get("title", "").strip()

            # Step 1: Claude researches
            research_brief = ""
            if web_research:
                log(f"  [{name}] researching…")
                research_brief = _web_research(
                    claude_client, claude_model, name, affiliation, interests, log
                )

            # Step 2: GPT writes
            page_text = _fetch_profile_context(t, log)
            page_section = (
                PAGE_SECTION_TEMPLATE.format(page_text=page_text) if page_text else NO_PAGE_SECTION
            )
            try:
                log(f"  [{name}] writing…")
                data = _write_email(
                    writer_client, writer_model,
                    name, title, affiliation, interests,
                    profile, resume_section, page_section, research_brief,
                    sender_name, log,
                )
            except Exception as e:
                log(f"  [{name}] write failed: {e}")
                continue

            subject = _no_dashes(data.get("subject") or cfg["email"]["subject_fallback"])
            body = (data.get("body") or "").strip()

            # Step 3: Gemini reviews (optional — controlled by quality_review toggle)
            if quality_review:
                log(f"  [{name}] reviewing…")
                body = _review_email(reviewer_client, reviewer_model, body, log, name=name)
            body = _no_dashes(body)

            write_draft_file(
                draft_path,
                to=email,
                name=name,
                subject=subject,
                body=body,
                source_url=t.get("source_url", ""),
            )
            made += 1
            made_this_pass += 1
            log(f"  [{name}] done — subject: {subject}")

        if keep_going is None:
            break
        if keep_going():
            if made_this_pass == 0:
                log("Waiting for scraper to find more contacts…")
                _time.sleep(15)
        else:
            break

    log(f"Done. Wrote {made} new draft(s).")
    return {"made": made}
