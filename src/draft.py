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

# Shared voice rules — the difference between a reply and the trash. Injected into
# every writer + reviewer prompt so drafts don't read as AI-generated.
_HUMAN_VOICE = """

SOUND LIKE A REAL PERSON, NOT AN AI — this matters more than anything else:
- No throat-clearing opener. Banned first lines: "I hope this email finds you well", \
"I am writing to", "I'm reaching out", "I recently came across", "I wanted to reach out". \
Start on the actual substance.
- Do not praise their work with adjectives ("impressive", "exciting", "fascinating", \
"groundbreaking", "cutting-edge", "innovative", "compelling"). Name the specific thing plainly \
and let it carry the weight.
- No stock closers: "I look forward to hearing from you", "Thank you for your time and \
consideration", "I would be grateful for the opportunity", "please don't hesitate". End plainly.
- No AI connectives: "Furthermore", "Moreover", "Additionally", "In conclusion", "That said".
- No buzzwords: leverage, align, synergy, robust, spearhead, passionate, delve, utilize, \
facilitate, tapestry, testament, meticulous.
- No "not just X, but Y" and no "it's not about X, it's about Y" constructions.
- Vary sentence length; short sentences are good. Contractions are fine. A plain, direct, \
even slightly blunt line reads more human than a polished one.
- One concrete detail beats three sentences of praise. Specific over smooth.
Write it the way a smart, busy person types a genuine two-minute email — not the way an \
assistant writes one."""

# ── Step 1: Claude researches the professor ────────────────────────────────────

WEB_SEARCH_TOOL = {"type": "web_search_20260209", "name": "web_search", "max_uses": 3}

WEB_RESEARCH_PROMPT = """You are helping the applicant (described below) \
write a short cold email about a specific job opening at a company. Search the web and \
build a brief so the email opens on something concrete about the company or the role.

Contact / company: {name}
Company: {affiliation}
What the posting says: {interests}
The applicant: {applicant}

Do these searches:
1. Search "{affiliation}" and its careers / team pages to see what the company does.
2. Search "{affiliation} product OR platform OR pipeline OR research" to find what it is \
known for.
3. If a contact person is named, search "{name} {affiliation}" for their role/bio.

Return a plain-text brief with:

BEST HOOK: The most specific, concrete thing the applicant can connect to — a product, \
platform, pipeline area, or the core responsibility of this role. One or two sentences on \
what it is. Pick whatever best matches the applicant's background above. This is the most \
important field.

COMPANY CONTEXT: 1-2 sentences on what the company does and where this role fits.

Be accurate and specific — only include what you actually found via search. If you cannot \
confidently identify the company or role, reply with exactly: NO_RELIABLE_INFO"""


def _web_research(client, model: str, name: str, affiliation: str, interests: str, log,
                  category: str = "research", applicant: str = "") -> str:
    """Claude: web-search the recipient and return a plain-text research brief (or '').

    Raises PipelineError on connection/auth failures so the UI shows the real
    problem; anything content-level (empty text, NO_RELIABLE_INFO) soft-skips.
    """
    import anthropic

    if not name:
        return ""
    template = WEB_RESEARCH_PROMPT if category == "industry" else ACADEMIC_WEB_RESEARCH_PROMPT
    prompt = template.format(
        name=name, affiliation=affiliation or "(unknown)",
        interests=interests or "(none listed)",
        applicant=applicant or "(no applicant details provided)",
    )
    messages = [{"role": "user", "content": prompt}]
    try:
        for _ in range(3):
            resp = client.messages.create(
                model=model, max_tokens=2000, tools=[WEB_SEARCH_TOOL],
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
            "network/VPN and ANTHROPIC_BASE_URL in .env, then draft again."
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
        log(f"  (web research failed for {name}: {e})")
        return ""
    if not text or "NO_RELIABLE_INFO" in text:
        return ""
    return text[:MAX_PAGE_CHARS]


# ── Step 2: GPT writes the email ──────────────────────────────────────────────

WRITER_SYSTEM_PROMPT = """You are writing a short, sincere cold email on behalf of the \
applicant described in the profile, expressing interest in a specific job opening at a \
company. Write the COMPLETE email.

WHY IT WORKS: Recruiters and hiring managers skim. This one earns a reply because it names \
the specific role and one concrete thing about the company, then connects that to the \
applicant's real, relevant experience. Specific and honest beats generic enthusiasm.

THE EMAIL MUST:
- Greet the contact by first name if one is given ("Hi [First],"); if the recipient is a \
  company or team rather than a named person, open with "Hello,".
- In the first 1-2 sentences, name the specific role and one concrete detail about the \
  company or role (from the brief) — not "your company" or "your work".
- If the research brief is empty or unavailable, open modestly and generically on the \
  role itself — name NO specific product, platform, or result, and NEVER invent facts \
  about the recipient or the applicant. Facts about the applicant may come only from the \
  profile/resume text provided.
- Give 2-3 sentences on the applicant's most relevant REAL experience, drawn only from the \
  profile and resume. Never invent experience, skills, or credentials.
- Close by expressing interest in the role and asking about next steps; note that a CV is \
  attached.
- Subject line: plain, specific, no clickbait — name the role (and company) and make the \
  ask clear (e.g. "Interest in the [Role] role at [Company]").
- Be under ~180 words. Plain, direct, professional — scientist/professional to \
  professional, not a nervous student.

VOICE:
- Short sentences land harder than long ones.
- Warmth comes from specificity, not adjectives. No clichés: "passionate", "excited", \
  "fascinated", "align", "leverage", "synergy", "robust", "novel", "pioneering".
- The applicant is offering value, not begging. Write from that posture.

Only use facts from the inputs. Return JSON: {"subject": "...", "body": "..."} where body \
is the COMPLETE email (greeting through sign-off), signed with the sender's name.""" + _HUMAN_VOICE

WRITER_USER_PROMPT = """Applicant profile:
<profile>
{profile}
</profile>
{resume_section}
The opening:
- Role / title: {title}
- Company: {affiliation}
- What the posting says: {research_interests}
- Contact: {name}

{page_section}

Web research brief (open on BEST HOOK; cite only what is listed here):
<research_brief>
{research_brief}
</research_brief>

Write the email about this specific role. Greet as "Hi {first_name}," if "{name}" is a \
person; if "{name}" is a company or team, open with "Hello,". Sign off as {sender_name}.
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
    category: str = "research",
) -> dict:
    """GPT: write the email from the research brief. Returns {subject, body}."""
    common = dict(
        profile=profile,
        resume_section=resume_section,
        name=name or "(unknown)",
        title=title or "(unknown)",
        affiliation=affiliation or "(unknown)",
        research_interests=interests or "(none listed)",
        page_section=page_section,
        research_brief=research_brief or "(no web research available — use directory interests only)",
        sender_name=sender_name,
    )
    if category == "industry":
        system = WRITER_SYSTEM_PROMPT
        prompt = WRITER_USER_PROMPT.format(first_name=_first_name(name) or "(name)", **common)
    else:
        system = ACADEMIC_WRITER_SYSTEM_PROMPT
        prompt = ACADEMIC_WRITER_USER_PROMPT.format(last_name=_last_name(name) or "(name)", **common)
    resp = oai_client.chat.completions.create(
        model=model,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ],
    )
    text = resp.choices[0].message.content or ""
    return json.loads(text)


# ── Step 3: Gemini reviews the email ──────────────────────────────────────────

REVIEWER_PROMPT = """You edit short cold emails expressing interest in a specific job \
opening. Fix every rule violation and return ONLY the corrected email body — no \
explanation, no preamble, no markdown.

RULES (fix every failure):
1. Greeting: "Hi [First]," if a person is named; "Hello," if it's addressed to a company \
   or team. Never "Dear Professor", never a full name.
2. The opening must name the SPECIFIC role and one concrete detail about the company or \
   role. "Your company" or "your work" is a FAIL. Every specific claim about the company \
   or role must be supported by the research brief below — rewrite or remove any that isn't.
3. If the research brief is empty or unavailable, the email must open modestly and \
   generically — NO specific product, platform, or result named. Never invent facts about \
   the recipient or the applicant, and do not add specifics yourself.
4. It must connect that to the applicant's real experience. Invent nothing — no skills, \
   techniques, or credentials that aren't in the email already.
5. Under ~180 words. Must end by expressing interest in the role and asking about next \
   steps, and mention the attached CV.
6. No em dashes anywhere. No links or contact info in the body.
7. No filler / clichés: "passionate about", "fascinated by", "excited to", "would love \
   the opportunity", "leveraging", "synergy", "robust", "novel", "pioneering", \
   "groundbreaking", "align", "spearheading".
8. Keep it signed with the sender's name already in the email; do not change the name.
""" + _HUMAN_VOICE + """

CONTEXT (verify the email's specifics against this — the brief is the only allowed \
source for claims about the company/role):
Recipient: {name} — {affiliation}
Subject line: {subject}
<research_brief>
{research_brief}
</research_brief>

EMAIL TO FIX:
---
{body}
---"""


def _review_email(oai_client, model: str, body: str, log, name: str = "",
                  category: str = "research", subject: str = "",
                  affiliation: str = "", research_brief: str = "") -> str:
    """Gemini: review the email body and return the fixed version.

    Raises PipelineError on connection/auth failures so the UI shows the real
    problem; content-level hiccups keep the unreviewed body.
    """
    import openai

    template = REVIEWER_PROMPT if category == "industry" else ACADEMIC_REVIEWER_PROMPT
    prompt = template.format(
        body=body,
        name=name or "(unknown)",
        affiliation=affiliation or "(unknown)",
        subject=subject or "(none)",
        research_brief=research_brief or (
            "(no research brief available — the email must not contain specific "
            "claims about the recipient beyond the context above)"
        ),
    )
    try:
        resp = oai_client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
        )
        fixed = (resp.choices[0].message.content or "").strip()
        return fixed if fixed else body
    except openai.APIConnectionError as e:
        raise PipelineError(
            "Couldn't reach the AI endpoint for the review step (connection "
            "error). Check your network/VPN and .env, then draft again."
        ) from e
    except openai.AuthenticationError as e:
        raise PipelineError(
            "The AI endpoint rejected the credential for the review step (401). "
            "Check your key in .env."
        ) from e
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


def slug_for(email: str = "", name: str = "", profile_url: str = "", source_url: str = "") -> str:
    """The draft filename stem for a contact — same precedence used by run()."""
    if email.strip():
        return _slug(email)
    if name.strip():
        return _slug(name)
    return _slug(profile_url.strip() or source_url.strip() or "unknown")


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


def _profile_section(profile: str, *keywords: str) -> str:
    """Return the text under the first '## ' heading whose title contains any keyword."""
    for m in re.finditer(r"^##\s*(.+?)\s*$\n(.*?)(?=^##\s|\Z)", profile, re.M | re.S):
        heading = m.group(1).lower()
        if any(k in heading for k in keywords):
            body = m.group(2).strip()
            if body:
                return body
    return ""


def _profile_sections_blank(profile: str) -> bool:
    """True when the About / Experience / Interests sections of the profile are
    all effectively empty — i.e. there is nothing real to ground an email in."""
    if not re.search(r"^##\s", profile, re.M):
        return not profile.strip()  # free-form profile: any text counts
    return not any(
        _profile_section(profile, kw) for kw in ("who i am", "experience", "interests")
    )


def _applicant_summary(profile: str) -> str:
    """A 1-2 sentence sketch of the applicant (About + Interests) so the web
    research step can pick a BEST HOOK that actually connects to them."""
    parts = []
    for section in (_profile_section(profile, "who i am", "about"),
                    _profile_section(profile, "interests")):
        text = " ".join(section.split())
        if text:
            parts.append(re.split(r"(?<=[.!?])\s+", text)[0])
    return " ".join(parts)[:400]


def write_draft_file(
    path: Path,
    *,
    to: str,
    name: str,
    subject: str,
    body: str,
    source_url: str,
    status: str = "pending",
    category: str = "",
) -> None:
    """Write a draft as Markdown with simple front-matter."""
    front = (
        "---\n"
        f"to: {json.dumps(to)}\n"
        f"name: {json.dumps(name)}\n"
        f"subject: {json.dumps(subject)}\n"
        f"status: {json.dumps(status)}   # pending | approved | skip | sent\n"
        f"source_url: {json.dumps(source_url)}\n"
        f"category: {json.dumps(category)}\n"
        "---\n\n"
    )
    path.write_text(front + body.strip() + "\n", encoding="utf-8")


# ── Academic variant (category == "research") ──────────────────────────────────
# Professors aren't hiring for a company role — this is a student/researcher
# inquiring about a spot in their lab, opening on a specific recent paper.

ACADEMIC_WEB_RESEARCH_PROMPT = """You are helping someone write a targeted cold \
email to a professor to ask about research opportunities in their lab. Search the \
web and build a brief so the email can open on a specific, recent piece of the \
professor's work.

Professor: {name}
Affiliation: {affiliation}
Known focus: {interests}
The applicant: {applicant}

Do these searches:
1. "{name} {affiliation}" — their faculty/lab page and Google Scholar profile.
2. "{name} {affiliation} 2024 OR 2025" — a recent, specific publication or project.
3. Their lab website for what they are currently working on.

Return a plain-text brief with:
BEST HOOK: The most specific recent paper or project — "[year] [title or one-line \
description] — [one concrete finding or method]". Prefer work that connects to the \
applicant's background above. This is what the email opens on. The most important field.
CURRENT FOCUS: 2-3 sentences on what the lab is actively working on now.

Only include things you actually found via search. If you cannot confidently \
identify this professor's work, reply with exactly: NO_RELIABLE_INFO"""

ACADEMIC_WRITER_SYSTEM_PROMPT = """You are writing a short, sincere cold email on \
behalf of the applicant described in the profile, to a professor, asking about \
research opportunities in their lab. Write the COMPLETE email.

WHY IT WORKS: Professors delete generic email. This one earns a reply because it \
opens on something specific and recent from their own work, then connects it \
honestly to the applicant's real interests and experience. Specific and honest \
beats flattering and vague.

THE EMAIL MUST:
- Address them as "Dear Professor {last_name}," (use "Dear Dr. {last_name}," only if \
  their title clearly isn't professor).
- Open (1-2 sentences) on the specific recent paper/project from the brief, with one \
  concrete detail, and why it connects to the applicant's genuine interests.
- If the research brief is empty or unavailable, open modestly on the lab's general \
  area (from the research focus given) — name NO specific paper or result, and NEVER \
  invent facts about the professor or the applicant. Facts about the applicant may \
  come only from the profile/resume text provided.
- Give 2-3 sentences on the applicant's most relevant REAL experience, drawn only \
  from the profile and resume — never invent anything.
- Close by asking whether they are taking on students/researchers and offering to \
  share more; note that a CV is attached.
- Subject line: plain, specific, no clickbait — the research area plus the ask \
  (e.g. "Research opportunity inquiry — [topic]").
- Be under ~180 words. Plain, direct, scientist-to-scientist.
- No clichés ("passionate", "fascinated", "excited", "align", "leverage", "robust", \
  "novel", "groundbreaking"). No em dashes. No links in the body.

Only use facts from the inputs. Return JSON: {"subject": "...", "body": "..."} where \
body is the complete email (greeting through sign-off), signed with the sender's name.""" + _HUMAN_VOICE

ACADEMIC_WRITER_USER_PROMPT = """Applicant profile:
<profile>
{profile}
</profile>
{resume_section}
Recipient (a professor):
- Name: {name}
- Title: {title}
- Affiliation: {affiliation}
- Research focus: {research_interests}

{page_section}

Research brief from web search (open on BEST HOOK; cite only what is listed here):
<research_brief>
{research_brief}
</research_brief>

Write the email. Address them as "Dear Professor {last_name}," and sign off as \
{sender_name}. Return JSON: {{"subject": "...", "body": "..."}}"""

ACADEMIC_REVIEWER_PROMPT = """You edit cold research-inquiry emails written to \
professors. Fix every rule violation and return ONLY the corrected email body — no \
explanation, no preamble, no markdown.

RULES (fix every failure):
1. Greeting: "Dear Professor [Last]," or "Dear Dr. [Last]," — never "Hi", never a \
   first name, never a full name.
2. The opening must name a SPECIFIC recent paper or project of the professor and one \
   concrete detail. "Your work on X" or "your research in the field" is a FAIL. Every \
   specific claim about the professor's work must be supported by the research brief \
   below — rewrite or remove any that isn't.
3. If the research brief is empty or unavailable, the email must open modestly and \
   generically on the lab's area — NO specific paper or result named. Never invent \
   facts about the professor or the applicant, and do not add specifics yourself.
4. It must connect that work to the applicant's real interests/experience. Invent nothing.
5. Under ~180 words. Must end with a clear ask about research opportunities and mention \
   the attached CV.
6. No clichés: "passionate", "fascinated", "excited", "align", "leverage", "synergy", \
   "robust", "novel", "pioneering", "groundbreaking". No em dashes. No links in the body.
7. Keep it signed with the sender's name; do not add contact details.
""" + _HUMAN_VOICE + """

CONTEXT (verify the email's specifics against this — the brief is the only allowed \
source for claims about the professor's work):
Recipient: {name} — {affiliation}
Subject line: {subject}
<research_brief>
{research_brief}
</research_brief>

EMAIL TO FIX:
---
{body}
---"""


# ── Main entry point ───────────────────────────────────────────────────────────

def run(limit: int | None = None, log=print, keep_going=None,
        only: str | None = None) -> dict:
    """Generate drafts for all contacts that don't have one yet.

    keep_going: optional callable that returns True while scraping is still
    running. When provided the drafter loops, re-reading the CSV for new
    contacts every 15 seconds, until keep_going() returns False.

    only: an identifier (email, name, or profile_url) — draft just that one
    contact, regenerating even if a draft already exists ("Draft this" action).
    """
    import time as _time

    import openai

    cfg = load_config()
    claude_client = build_anthropic_client()
    writer_client = build_writer_client()

    targets_path = resolve(cfg["paths"]["targets"])
    profile_path = resolve(cfg["paths"]["profile"])
    drafts_dir = resolve(cfg["paths"]["drafts_dir"])
    drafts_dir.mkdir(parents=True, exist_ok=True)

    if not targets_path.exists():
        raise PipelineError("No contacts yet. Scrape some schools on the Setup page first.")
    # profile.md is gitignored (personal data) — a fresh clone won't have it;
    # the blank-grounding guard below gives the actionable message.
    profile = profile_path.read_text(encoding="utf-8") if profile_path.exists() else ""
    sender_name = cfg["sender"]["name"]
    if not sender_name.strip():
        raise PipelineError(
            "Sender name is not set. Fill in your name on the Setup page and save."
        )

    resume_text = _extract_resume_text(resolve(cfg["paths"]["resume"]))
    if not resume_text and _profile_sections_blank(profile):
        raise PipelineError(
            "There is nothing to ground the emails in: your profile is blank and "
            "no resume text could be read. Fill in Setup → \"About you\" and "
            "upload a real resume PDF first."
        )
    resume_section = (
        RESUME_SECTION_TEMPLATE.format(resume_text=resume_text) if resume_text else ""
    )
    if resume_text:
        log("Using your resume contents to ground the emails.")
    else:
        log("No readable resume PDF found — using your typed profile only.")
    applicant_summary = _applicant_summary(profile)

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
            profile_url = (t.get("profile_url") or "").strip()
            if only and only not in (email, name, profile_url):
                continue
            slug = slug_for(email, name, profile_url, t.get("source_url", ""))
            draft_path = drafts_dir / f"{slug}.md"
            # In normal mode skip contacts that already have a draft; in "only"
            # mode the user explicitly asked to (re)draft this one.
            if draft_path.exists() and not only:
                continue
            if limit is not None and made >= limit:
                break

            affiliation = t.get("affiliation", "").strip()
            interests = t.get("research_interests", "").strip()
            title = t.get("title", "").strip()
            category = (t.get("category") or "research").strip() or "research"

            # Step 1: Claude researches
            research_brief = ""
            if web_research:
                log(f"  [{name}] researching…")
                research_brief = _web_research(
                    claude_client, claude_model, name, affiliation, interests, log,
                    category=category, applicant=applicant_summary,
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
                    sender_name, log, category=category,
                )
            except PipelineError:
                raise  # real cause (connection/auth) — surface it to the user
            except openai.APIConnectionError as e:
                raise PipelineError(
                    "Couldn't reach the AI endpoint for the writer step (connection "
                    "error). Check your network/VPN and .env, then draft again."
                ) from e
            except openai.AuthenticationError as e:
                raise PipelineError(
                    "The AI endpoint rejected the credential for the writer step "
                    "(401). Check your key in .env."
                ) from e
            except Exception as e:
                log(f"  [{name}] write failed: {e}")
                continue

            if category == "industry":
                fallback_subject = (
                    f"Interest in the {title} role" if title
                    else "Interest in joining your team"
                )
            else:
                fallback_subject = cfg["email"]["subject_fallback"]
            subject = _no_dashes(data.get("subject") or fallback_subject)
            body = (data.get("body") or "").strip()

            # Step 3: Gemini reviews (optional — controlled by quality_review toggle)
            if quality_review:
                log(f"  [{name}] reviewing…")
                body = _review_email(reviewer_client, reviewer_model, body, log,
                                     name=name, category=category, subject=subject,
                                     affiliation=affiliation,
                                     research_brief=research_brief)
            body = _no_dashes(body)

            write_draft_file(
                draft_path,
                to=email,
                name=name,
                subject=subject,
                body=body,
                source_url=t.get("source_url", ""),
                category=category,
            )
            made += 1
            made_this_pass += 1
            log(f"  [{name}] done — subject: {subject}")
            if only:
                break  # single-contact "Draft this" — one and done

        if only or keep_going is None:
            break
        if keep_going():
            if made_this_pass == 0:
                log("Waiting for scraper to find more contacts…")
                _time.sleep(15)
        else:
            break

    log(f"Done. Wrote {made} new draft(s).")
    return {"made": made}


# ── Follow-ups (for the Sent view) ─────────────────────────────────────────────

FOLLOWUP_SYSTEM_PROMPT = """You are writing a very short, polite follow-up to a cold \
email that got no reply. 2 to 4 sentences, total. Briefly reference the earlier email, \
restate interest in one line, and make a small, low-pressure ask. No guilt, no "just \
checking in" filler, no re-pitching the whole thing. Warm and brief.

Return JSON: {"subject": "...", "body": "..."} where body is the complete email \
(greeting through sign-off), signed with the sender's name.""" + _HUMAN_VOICE

FOLLOWUP_USER_PROMPT = """The applicant (below) emailed {name} about "{subject}" a while \
ago and hasn't heard back. Write a brief, friendly follow-up.

Applicant profile:
<profile>
{profile}
</profile>

The original email (reference only what it actually said — do not invent claims or \
details that aren't in it):
<original_email>
{original_email}
</original_email>

Greet by first name if "{name}" is a person, otherwise open with "Hello,". Keep the \
subject as "{reply_subject}" unless a shorter one clearly fits. Sign off as {sender_name}.
Return JSON: {{"subject": "...", "body": "..."}}"""


def follow_up(to: str, name: str, subject: str, log=print) -> dict:
    """Write a short follow-up draft for a previously-sent email. Saved as a new
    pending draft (slug + '_followup') for review."""
    cfg = load_config()
    writer_client = build_writer_client()
    _profile_path = resolve(cfg["paths"]["profile"])
    profile = _profile_path.read_text(encoding="utf-8") if _profile_path.exists() else ""
    sender_name = cfg["sender"]["name"]
    if not sender_name.strip():
        raise PipelineError("Sender name is not set. Fill in your name on the Setup page and save.")
    drafts_dir = resolve(cfg["paths"]["drafts_dir"])
    drafts_dir.mkdir(parents=True, exist_ok=True)

    # Ground the follow-up in what the first email actually said.
    original_email = ""
    orig_path = drafts_dir / f"{slug_for(to, name)}.md"
    if orig_path.exists():
        raw = orig_path.read_text(encoding="utf-8")
        parts = raw.split("---\n", 2)
        original_email = (parts[2] if len(parts) == 3 else raw).strip()

    # Only prefix "Re: " when the original subject doesn't already carry one.
    reply_subject = (
        subject if re.match(r"^\s*re:", subject or "", re.I) else f"Re: {subject}"
    )

    prompt = FOLLOWUP_USER_PROMPT.format(
        name=name or "(unknown)", subject=subject or "(no subject)",
        profile=profile, sender_name=sender_name,
        original_email=original_email or (
            "(original draft not found — keep the reference generic and invent nothing)"
        ),
        reply_subject=reply_subject,
    )
    log(f"  [{name}] writing follow-up…")
    resp = writer_client.chat.completions.create(
        model=cfg["model"].get("writer", "openai/gpt-4o"),
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": FOLLOWUP_SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
    )
    data = json.loads(resp.choices[0].message.content or "{}")
    subj = _no_dashes(data.get("subject") or reply_subject)
    body = _no_dashes((data.get("body") or "").strip())

    slug = slug_for(to, name) + "_followup"
    write_draft_file(drafts_dir / f"{slug}.md", to=to, name=name, subject=subj,
                     body=body, source_url="", status="pending")
    log(f"  [{name}] follow-up drafted — subject: {subj}")
    return {"made": 1, "slug": slug}
