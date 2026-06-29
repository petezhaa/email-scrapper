# -*- coding: utf-8 -*-

QUALITY_CHECK_PROMPT = """You are a strict editor. A student named Dennis sent \
the email below to a professor. Rewrite it so it reads exactly like the REFERENCE \
EMAIL — same tone, structure, sentence rhythm, and directness. Then check every \
criterion below and fix any that fail.

REFERENCE EMAIL (match this style exactly):
---
Hi Professor Wei,

My name is Dennis Rui, and I am a recent Chemistry BA/MS graduate from \
Northwestern University. I am writing to ask about the possibility of pursuing \
post-bachelor's research in your group starting Summer 2026. My goal is to \
contribute to your group's work while preparing to apply for a PhD.

I am deeply interested in your work applying SRS to image Huntington's disease \
Htt aggregate spatial dynamics and the spatiotemporal metabolic flux in the TCA. \
My background in biophysics includes:

- Shu-ou Shan lab at Caltech: Studying cpSRP43-Gun4 chaperone-client interactions \
using NMR, XL-MS, and FRET as part of the Amgen Scholars program
- Samuel Stupp lab at Northwestern: Using AFM-IR and multi-nuclei NMR to study \
the structural properties of peptide amphiphiles

I would be grateful for the opportunity to learn more about potential projects \
and/or speak with current group members. My CV is attached. Thank you for your time!

Best,
Dennis
---

PASS/FAIL CRITERIA (fix every failure):
1. Greeting "Dear Professor [Last name]," — NOT "Hi Professor X,"
2. First sentence: exact position ask (research technician/assistant) + academic year
3. Hook sentence: "I recently read your [year] [full journal name] paper on [specific \
topic], and [why it connects]" — vague hooks like "your fascinating work" are a FAIL
4. Experience: 2 prose sentences (not bullets). Format: "At [University] in the [Lab] \
Lab, I [technique] to [goal]." or "In the [Lab] Lab at [University], I..."
5. No em dashes (— or –) anywhere
6. Body under 200 words
7. Does NOT read like a literature summary — shows why THIS student in THAT lab
8. Voice: direct, plain, confident but not arrogant. No flattery, no "I hope this email \
finds you well", no purple prose

Return improved_body with every issue fixed. List issues found in the issues array.

EMAIL TO REVIEW:
---
{body}
---"""

WEB_SEARCH_TOOL = {"type": "web_search_20260209", "name": "web_search", "max_uses": 8}
print("OK")
