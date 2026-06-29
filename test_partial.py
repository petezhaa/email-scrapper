# -*- coding: utf-8 -*-
"""Generate personalized draft emails from data/targets.csv.

For each professor without an existing draft, Claude writes a subject + body
grounded in your profile (data/profile.md) and that professor's research.
Each draft is saved as a Markdown file with YAML front-matter in drafts/.

YOU review every draft before anything is sent: open the file, edit freely,
and change `status: pending` to `status: approved` to queue it.

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
from .config import PipelineError, build_anthropic_client, load_config, load_secrets, resolve

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

SYSTEM_PROMPT = """You write concise, sincere cold emails from a student to a \
professor or postdoc inquiring about a research position. You write in the \
student's own voice using only facts from their profile — you never invent \
experience, skills, or publications.

The heart of each email is ONE specific, genuine connection between the \
recipient's ACTUAL research (drawn from their own profile page when provided, \
a particular project, method, paper, or research direction, named concretely) \
and the student's real experience or interests. Name the specific thing; do not \
speak in generalities like "your fascinating work." Only claim a connection that \
is genuinely supported by the student's profile; if the overlap is loose, keep \
the claim modest and honest rather than overstating it.

CRITICAL FAILURE MODE TO AVOID: Do NOT write a "mini literature summary" of the \
lab's papers. The research paragraph must show a concrete reason why THIS \
student belongs in THAT lab specifically — naming the lab's biological question, \
the specific gap the student is interested in, and how their experience bridges \
it. Sentences like "combining complementary techniques could probe the dynamics \
of these sites" are too broad to be convincing — they apply to dozens of labs. \
Be specific enough that the sentence couldn't appear in a letter to any other lab.

VOICE: Match the student's own writing sample (in their profile) as closely as \
you can: their sentence length and rhythm, word choice, and level of formality. \
The email should read like THEY wrote it, not like a template.

STYLE RULES:
- Open with "Dear Professor [Last name]," for faculty. "Dear Dr. [Last name]," for postdocs.
- No opener like "I hope this email finds you well"
- First sentence: state who the student is and the exact position ask + timeframe.
- Hook (2nd paragraph, opening sentence): "I recently read your [year] [journal] paper on \
[specific topic], and [one sentence on why it connects]." Only use this if research is \
provided — otherwise reference a specific technique or direction from their profile page.
- Experience: 2 sentences (prose, not bullets). Each: "At [University] in the [Lab] Lab, \
I [specific technique] to [specific goal]."
- Closing: "My resume is attached." + optionally 1 sentence on fit.
- Sign-off: name + email + phone on separate lines. No "Best," needed.
- Never use em dashes or en dashes ("—" or "–"). Use commas or periods instead.
- No flattery clichés, no purple prose. Plain and direct.
- Body under 200 words.

EXAMPLE EMAIL (model this structure and tone exactly):

Dear Professor Bustamante,

I am writing to inquire about a full-time research technician position in your \
lab for the 2026, 2027 academic year. I recently read your 2023 ACS Central \
Science paper on real-time nucleosome disassembly visualized by high-speed AFM, \
and it connected directly with work I have done myself.

In the Stupp Lab at Northwestern, I used Nano-IR and AFM to characterize the \
nanoscale structural origins of ferroelectric behavior in peptide amphiphile \
assemblies. Tracking dynamic structural transitions through nanoscale imaging is \
something I have hands-on experience with, and the high-speed AFM approach in \
your nucleosome work is a direction I am genuinely motivated to push into.

I also mapped transient protein-protein interaction interfaces at Caltech using \
19F NMR-PRE, XL-MS, and FRET, which gave me a grounding in multi-method \
structural characterization of dynamic biomolecular systems.

My resume is attached. I would welcome any opportunity to discuss openings.

Dennis Rui
ptr@gmail.com
626-773-6581"""

USER_PROMPT = """Here is the student's profile:

<profile>
{profile}
</profile>
{resume_section}
The recipient:
- Name: {name}
- Title: {title}
- Affiliation: {affiliation}
- Research interests (from a directory listing): {research_interests}

{page_section}
{research_section}
Write the email following the structure and tone of the example in the system prompt. Requirements:
- Greet with "Dear Professor {last_name}," for faculty. Use "Dear Dr. {last_name}," for \
postdocs. Never use "Hi".
- First sentence: "I am writing to ask about a full-time research technician or assistant \
position in your lab for the [year] academic year." (adjust position type as appropriate).
- Hook sentence (2nd paragraph, 1st sentence): if a BEST HOOK PAPER is in the web research, \
write it as "I recently read your [year] [full journal name] paper on [specific topic], and \
[one sentence on why it connects to the student's experience or interests]." This sentence \
must be specific enough that it could not appear in a letter to any other lab.
- Experience block: 2 sentences (not bullet points unless the profile uses them). Each: \
"At [University] in the [Lab] Lab, I [specific technique(s)] to [specific scientific goal]."
- Closing: "My resume is attached." then optionally 1 short sentence about fit.
- Sign-off: "{sender_name}\n{contact_info}"
- Keep the BODY under ~200 words. Do not include the subject line in the body.
- Ground specifics ONLY in what's given above. Never fabricate details about their work.

QUALITY_SCHEMA = {
    "type": "object",
    "properties": {
        "passes": {"type": "boolean"},
        "issues": {"type": "array", "items": {"type": "string"}},
        "improved_body": {"type": "string"},
    },
    "required": ["passes", "issues", "improved_body"],
    "additionalProperties": False,
}
