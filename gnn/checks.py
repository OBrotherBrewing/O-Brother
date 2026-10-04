"""Deterministic checks run on every draft. Any FAIL blocks publication (fail closed).

These don't trust the model. They compare the draft's text with the evidence text the
pipeline actually retrieved:
  numbers   every figure in the draft appears in the evidence (allowing for formatting)
  dates     every year / day-month in the draft appears in the evidence; no relative dates
  quotes    anything in quotation marks appears word for word in the evidence
  entities  capitalised names in the draft appear in the evidence (WARN: heuristic)
  hype      words like "cure" or "breakthrough" need the evidence to use them too
  copying   no long verbatim runs copied from a source outside quotation marks
  structure required parts are present (why it matters, limitations, 1+ primary source)
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .util import norm_space

PASS, WARN, FAIL = "pass", "warn", "fail"

NUMBER_WORDS = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
    "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15,
    "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19, "twenty": 20, "thirty": 30,
    "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90, "hundred": 100,
    "half": 0.5, "dozen": 12,
}
SCALES = {"thousand": 1e3, "million": 1e6, "billion": 1e9, "trillion": 1e12, "k": 1e3, "m": 1e6, "bn": 1e9, "tn": 1e12}
MONTHS = "january february march april may june july august september october november december".split()
RELATIVE = re.compile(r"\b(yesterday|today|tomorrow|tonight|this (?:morning|week|month|year)|last (?:week|month|night)|next (?:week|month))\b", re.I)
NUM_RE = re.compile(
    r"(?<![\w.])([€$£]?)(\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)(\s?(?:%|per ?cent|percent))?(?:\s?(thousand|million|billion|trillion|bn|tn|k|m)\b)?",
    re.I,
)
QUOTE_RE = re.compile(r"[\"“]([^\"”]{12,}?)[\"”]")
CAP_SEQ = re.compile(r"\b([A-Z][a-zA-ZÀ-ſ'’-]+(?:\s+(?:of|for|and|the|de|du|von|van|na|an|Ó|Mac|Mc)?\s*[A-Z][a-zA-ZÀ-ſ'’-]+)+)")
SAFE_CAPS = {"Why", "What", "The", "This", "That", "These", "Those", "It", "In", "On", "At", "But", "And", "A", "An", "Evidence", "Limitations", "Sources", "Ireland", "Europe", "European Union", "United States", "United Kingdom", "UK", "US", "EU"}


@dataclass
class CheckResult:
    name: str
    status: str
    details: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {"status": self.status, "details": self.details[:20]}


def _to_number(raw: str, scale: str | None) -> float:
    v = float(raw.replace(",", ""))
    if scale:
        v *= SCALES[scale.lower()]
    return v


def numbers_in(text: str) -> list[tuple[str, float, bool]]:
    """(surface form, value, is_percent) for every figure in text, including number words."""
    out = []
    for m in NUM_RE.finditer(text):
        cur, raw, pct, scale = m.groups()
        try:
            out.append((m.group(0).strip(), _to_number(raw, scale), bool(pct)))
        except ValueError:
            continue
    for m in re.finditer(r"\b(" + "|".join(NUMBER_WORDS) + r")\b(?:\s+(thousand|million|billion))?", text, re.I):
        word, scale = m.group(1).lower(), m.group(2)
        if word in ("one", "half") and not scale:
            continue  # too common in ordinary prose ("one of", "half the") to check
        v = NUMBER_WORDS[word] * (SCALES[scale.lower()] if scale else 1)
        out.append((m.group(0), v, False))
    return out


def _years(text: str) -> set[str]:
    return set(re.findall(r"\b(19\d{2}|20\d{2})\b", text))


def _close(a: float, b: float) -> bool:
    if a == b:
        return True
    if b == 0:
        return False
    return abs(a - b) / abs(b) <= 0.005  # rounding tolerance of half a percent


def check_numbers(draft: str, evidence: str) -> CheckResult:
    ev = numbers_in(evidence)
    ev_values = [v for _, v, _ in ev]
    years = _years(draft)
    problems = []
    for surface, value, pct in numbers_in(draft):
        if surface.isdigit() and surface in years:
            continue  # years are checked by check_dates
        if any(_close(value, v) for v in ev_values):
            continue
        # allow a rounded figure: "1.5 million" vs "1,486,000" (within 5%) only when marked approximate
        approx = re.search(r"(about|around|nearly|almost|roughly|more than|over|under|some|approximately)\s+" + re.escape(surface), draft, re.I)
        if approx and any(v and abs(value - v) / abs(v) <= 0.05 for v in ev_values):
            continue
        problems.append(f"'{surface}' not found in evidence")
    return CheckResult("numbers", FAIL if problems else PASS, problems)


def check_dates(draft: str, evidence: str) -> CheckResult:
    problems = []
    ev_years = _years(evidence)
    for y in sorted(_years(draft)):
        if y not in ev_years:
            problems.append(f"year {y} not in evidence")
    ev_low = evidence.lower()
    for m in re.finditer(r"\b(\d{1,2})\s+(" + "|".join(MONTHS) + r")\b|\b(" + "|".join(MONTHS) + r")\s+(\d{1,2})\b", draft, re.I):
        day = m.group(1) or m.group(4)
        month = (m.group(2) or m.group(3)).lower()
        if not re.search(rf"\b{int(day)}(st|nd|rd|th)?\s+{month}\b|\b{month}\s+{int(day)}\b", ev_low):
            problems.append(f"date '{m.group(0)}' not in evidence")
    rel = RELATIVE.findall(draft)
    if rel:
        problems.append(f"relative date words used: {sorted(set(r.lower() for r in rel))}")
    return CheckResult("dates", FAIL if problems else PASS, problems)


def check_quotes(draft: str, evidence: str) -> CheckResult:
    ev = norm_space(evidence).lower()
    problems = []
    for q in QUOTE_RE.findall(draft):
        if len(q.split()) < 3:
            continue
        if norm_space(q).lower().strip(" .,") not in ev:
            problems.append(f"quote not verbatim in evidence: “{q[:80]}”")
    return CheckResult("quotes", FAIL if problems else PASS, problems)


def check_entities(draft: str, evidence: str, allow: set[str] | None = None) -> CheckResult:
    ev = norm_space(evidence).lower()
    allow = {a.lower() for a in (allow or set())} | {s.lower() for s in SAFE_CAPS}
    missing = []
    for seq in set(CAP_SEQ.findall(draft)):
        s = seq.strip()
        if s.lower() in allow or s.lower() in ev:
            continue
        # tolerate possessives and leading articles
        core = re.sub(r"^(The|A|An)\s+", "", s).rstrip("'s’s")
        if core.lower() in ev or core.lower() in allow:
            continue
        missing.append(s)
    return CheckResult("entities", WARN if missing else PASS, [f"name not in evidence: {m}" for m in sorted(missing)])


def check_hype(draft: str, evidence: str, hype_words: list[str]) -> CheckResult:
    d, e = draft.lower(), evidence.lower()
    flagged = []
    for w in hype_words:
        pat = r"(?<![\w-])" + re.escape(w.lower()) + r"(?![\w-])"
        if re.search(pat, d) and not re.search(pat, e):
            flagged.append(w)
    return CheckResult("hype", FAIL if flagged else PASS, [f"'{w}' used but not supported by the evidence wording" for w in flagged])


def _shingles(text: str, n: int) -> set[tuple[str, ...]]:
    toks = re.findall(r"[a-z0-9']+", text.lower())
    return {tuple(toks[i:i + n]) for i in range(len(toks) - n + 1)}


def check_copying(draft: str, sources: list[str], run: int = 12, max_overlap: float = 0.15) -> CheckResult:
    """No run of `run` words copied from a source outside quotes; low overall 5-gram overlap."""
    unquoted = QUOTE_RE.sub(" ", draft)
    d_runs = _shingles(unquoted, run)
    d5 = _shingles(unquoted, 5)
    problems = []
    for i, src in enumerate(sources, 1):
        if not src:
            continue
        if d_runs & _shingles(src, run):
            problems.append(f"{run}+ word passage copied from source {i} without quotation marks")
        if d5:
            overlap = len(d5 & _shingles(src, 5)) / len(d5)
            if overlap > max_overlap:
                problems.append(f"{overlap:.0%} of 5-word phrases shared with source {i} (limit {max_overlap:.0%})")
    return CheckResult("copying", FAIL if problems else PASS, problems)


def check_structure(meta: dict, body: str) -> CheckResult:
    problems = []
    if len(body.split()) < 120:
        problems.append("article under 120 words")
    if len(body.split()) > 900:
        problems.append("article over 900 words")
    if not (meta.get("why_it_matters") or "").strip():
        problems.append("missing why_it_matters")
    if not [l for l in meta.get("limitations") or [] if l.strip()]:
        problems.append("no limitations stated")
    srcs = meta.get("sources") or []
    if not any(s.get("role") == "primary" for s in srcs):
        problems.append("no primary source")
    if not meta.get("dek"):
        problems.append("missing dek (outcome sentence)")
    return CheckResult("structure", FAIL if problems else PASS, problems)


def run_all(meta: dict, body: str, evidence_text: str, source_texts: list[str], hype_words: list[str],
            allow_names: set[str] | None = None) -> dict[str, CheckResult]:
    """Checks the whole public text: headline, dek, body, why-it-matters, limitations, social copy."""
    social = meta.get("social") or {}
    public = "\n".join([
        meta.get("title", ""), meta.get("dek", ""), body, meta.get("why_it_matters", ""),
        "\n".join(meta.get("limitations") or []), meta.get("newsletter_blurb", ""),
        social.get("hook", ""), "\n".join(social.get("slides") or []), social.get("caption", ""),
    ])
    km = meta.get("key_metric") or {}
    if km:
        public += f"\n{km.get('before', '')} {km.get('after', '')} {km.get('period', '')}"
    # limitations may legitimately mention what is *not* in the evidence (e.g. "no data yet"),
    # so only numbers/dates/quotes are enforced on them; names are a warning anyway.
    results = {
        "numbers": check_numbers(public, evidence_text),
        "dates": check_dates(public, evidence_text),
        "quotes": check_quotes(public, evidence_text),
        "entities": check_entities(public, evidence_text, allow_names),
        "hype": check_hype(public, evidence_text, hype_words),
        "copying": check_copying(body, source_texts),
        "structure": check_structure(meta, body),
    }
    return results


def passed(results: dict[str, CheckResult]) -> bool:
    return all(r.status != FAIL for r in results.values())
