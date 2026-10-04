"""Orchestrates one pipeline run: discover → triage → evidence → extract → verify →
corroborate → draft → checks → fact-check → editorial gate → queue (or publish).

Fail closed: any stage error, refusal or failed check means the story does not publish.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from urllib.parse import urlparse

from . import audit, checks, config, schemas
from .discover import discover
from .fetch import Fetcher, is_primary_url
from .llm import LLM, LLMError, load_prompt
from .stories import EVIDENCE_DIR, QUEUE_DIR, Story, save, story_path
from .util import iso, norm_space, short_hash, slugify, write_json

log = logging.getLogger("gnn")
MAX_DOC_CHARS = 40_000
READABLE = ("LICENSED", "PRIMARY_PUBLIC")


@dataclass
class RunReport:
    started_at: str = field(default_factory=iso)
    candidates: int = 0
    shortlisted: int = 0
    queued: list[str] = field(default_factory=list)
    published: list[str] = field(default_factory=list)
    rejected: list[dict] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return self.__dict__


# ---------------------------------------------------------------------------- triage
def triage(llm: LLM, candidates: list[dict]) -> list[dict]:
    system, version = load_prompt("triage")
    sections = ", ".join(config.section_names())
    scored: dict[str, dict] = {}
    for i in range(0, len(candidates), 30):
        batch = candidates[i:i + 30]
        lines = [f"[{c['id']}] {c['title']} — {c['summary'][:400]} (source: {c['source_name']}, tier {c['tier']})" for c in batch]
        user = f"Sections: {sections}\n\nCandidates:\n" + "\n".join(lines)
        out = llm.json("triage", system, user, schemas.TRIAGE, context={"candidates": batch})
        for it in out.get("items", []):
            scored[it["id"]] = it
    ranked = []
    for c in candidates:
        t = scored.get(c["id"])
        if not t or t["category"] not in "ABCDE" or t["positivity"] < 50:
            continue
        c["triage"] = t
        c["priority"] = 0.5 * t["positivity"] + 0.5 * t["significance"] - 0.3 * t["risk"] + (8 if c["tier"] == 1 else 0)
        ranked.append(c)
    ranked.sort(key=lambda c: c["priority"], reverse=True)
    picked, clusters = [], set()
    for c in ranked:
        if c["cluster"] in clusters:
            continue
        clusters.add(c["cluster"])
        picked.append(c)
    return picked


# ---------------------------------------------------------------------------- evidence
def gather_evidence(cand: dict, all_cands: list[dict], fetcher: Fetcher, max_primary: int = 2) -> list[dict]:
    """Fetch the candidate page, primary sources it links to, and other reports of the same event."""
    docs: list[dict] = []
    seen_urls: set[str] = set()

    def add(url: str, *, tier: int, rights: str, publisher: str, owner_org: str, role: str) -> dict | None:
        if url in seen_urls:
            return None
        seen_urls.add(url)
        page = fetcher.get(url)
        if page.status != 200 or not page.text:
            return None
        if page.tdm_reserved and rights != "LICENSED":
            rights = "LINK_ONLY"  # reservation honoured: we may link, not mine
        text = page.text if rights != "LINK_ONLY" else ""
        doc = {
            "n": len(docs) + 1, "url": url, "title": page.title or cand["title"], "publisher": publisher,
            "owner_org": owner_org, "tier": tier, "rights": rights, "role": role, "text": text[:MAX_DOC_CHARS],
            "truncated": len(text) > MAX_DOC_CHARS, "sha256": page.sha256, "retrieved_at": page.retrieved_at,
            "note": page.note, "links": page.links,
        }
        docs.append(doc)
        return doc

    rights = cand["rights"]
    lead_rights = "FACTS_ONLY" if rights in ("FACTS_ONLY", "LINK_ONLY") else rights
    first = add(cand["url"], tier=cand["tier"], rights=lead_rights, publisher=cand["source_name"],
                owner_org=cand.get("owner_org", ""), role="primary" if lead_rights in READABLE and cand["tier"] <= 2 else "lead")
    if first:
        n = 0
        for url, _anchor in first["links"]:
            if n >= max_primary:
                break
            if is_primary_url(url) and urlparse(url).netloc != urlparse(cand["url"]).netloc:
                host = urlparse(url).netloc
                if add(url, tier=1, rights="PRIMARY_PUBLIC", publisher=host, owner_org=host, role="primary"):
                    n += 1
    # independent reports of the same event found in this run
    for other in all_cands:
        if other is cand or other.get("cluster") != cand.get("cluster"):
            continue
        r = "FACTS_ONLY" if other["rights"] in ("FACTS_ONLY", "LINK_ONLY") else other["rights"]
        add(other["url"], tier=other["tier"], rights=r, publisher=other["source_name"],
            owner_org=other.get("owner_org", ""), role="corroborating")
        if len(docs) >= 5:
            break
    for d in docs:
        d.pop("links", None)
    return docs


def docs_prompt(docs: list[dict], include_facts_only: bool) -> str:
    parts = []
    for d in docs:
        if d["rights"] == "LINK_ONLY":
            body = "(link only: rights holder has reserved text and data mining; do not use its content)"
        elif d["rights"] == "FACTS_ONLY" and not include_facts_only:
            body = "(another outlet's journalism: not supplied to the writer)"
        else:
            body = d["text"]
        parts.append(f"<document n=\"{d['n']}\" publisher=\"{d['publisher']}\" rights=\"{d['rights']}\" tier=\"{d['tier']}\" url=\"{d['url']}\">\n{body}\n</document>")
    return "\n\n".join(parts)


# ---------------------------------------------------------------------------- verification
def verify_claims(claims: list[dict], docs: list[dict]) -> tuple[list[dict], list[dict]]:
    """Keep a claim only if its span is verbatim in its document (and, for FACTS_ONLY leads,
    the same span or figure also appears in a readable primary document)."""
    by_n = {d["n"]: d for d in docs}
    readable = norm_space(" ".join(d["text"] for d in docs if d["rights"] in READABLE)).lower()
    kept, dropped = [], []
    for c in claims:
        d = by_n.get(c.get("doc"))
        span = norm_space(c.get("span", "")).lower()
        if not d or not span or span not in norm_space(d["text"]).lower():
            dropped.append({**c, "why": "supporting passage not found verbatim"})
            continue
        if d["rights"] not in READABLE:
            if span not in readable:
                dropped.append({**c, "why": "only in another outlet's report; no primary confirmation"})
                continue
        kept.append({**c, "status": "verified"})
    return kept, dropped


def corroboration(kept: list[dict], docs: list[dict]) -> str:
    used = {c["doc"] for c in kept}
    used_docs = [d for d in docs if d["n"] in used]
    owners = {d["owner_org"] or d["publisher"] for d in used_docs}
    if any(d["tier"] == 1 and d["rights"] in READABLE for d in used_docs):
        return "primary_confirmed"
    if len(owners) >= 2:
        return "two_independent"
    return "single_source"


# ---------------------------------------------------------------------------- one story
def build_story(llm: LLM, cand: dict, docs: list[dict], report: RunReport) -> tuple[Story, dict] | None:
    sid_base = slugify(cand["title"], 40)
    story_id = f"{iso()[:10]}-{sid_base}-{short_hash(cand['url'])}"

    sys_x, ver_x = load_prompt("extract")
    user_x = f"Candidate headline: {cand['title']}\nSections: {', '.join(config.section_names())}\n\n{docs_prompt(docs, include_facts_only=True)}"
    x = llm.json("extract", sys_x, user_x, schemas.EXTRACT, story_id=story_id, context={"candidate": cand, "docs": docs})
    if not x.get("qualifies"):
        report.rejected.append({"url": cand["url"], "stage": "extract", "why": x.get("reason", "")})
        return None

    kept, dropped = verify_claims(x.get("claims", []), docs)
    if len(kept) < 2:
        report.rejected.append({"url": cand["url"], "stage": "verify", "why": f"only {len(kept)} verified claims"})
        return None
    corro = corroboration(kept, docs)

    sys_d, ver_d = load_prompt("draft")
    claim_lines = "\n".join(f"- [{c['id']}] {c['text']} (doc {c['doc']}: \"{c['span']}\")" for c in kept)
    km = x.get("key_metric") or {}
    user_d = (
        f"Outcome: {x.get('outcome')}\nCategory: {x.get('category')}\nSection: {x.get('section')}\n"
        f"Key metric: {json.dumps(km) if km.get('present') else 'none'}\n"
        f"Limitations noted at extraction: {json.dumps(x.get('limitations', []))}\n\n"
        f"Verified claims:\n{claim_lines}\n\nPrimary documents (rights permitting):\n{docs_prompt(docs, include_facts_only=False)}"
    )
    draft = llm.json("draft", sys_d, user_d, schemas.DRAFT, story_id=story_id,
                     context={"candidate": cand, "claims": kept, "docs": docs})

    meta, body = assemble(story_id, cand, x, draft, docs, kept, corro)
    evidence_text = "\n".join([d["text"] for d in docs if d["rights"] in READABLE] + [c["span"] for c in kept])
    source_texts = [d["text"] for d in docs]
    hype = config.pipeline()["editorial"].get("hype_words", [])
    allow = {config.site()["brand"]["name"]} | {d["publisher"] for d in docs} | set(x.get("actors", []))
    results = checks.run_all(meta, body, evidence_text, source_texts, hype, allow)

    sys_f, ver_f = load_prompt("factcheck")
    user_f = (f"DRAFT\nTitle: {meta['title']}\nDek: {meta['dek']}\n\n{body}\n\nWhy it matters: {meta['why_it_matters']}\n"
              f"Limitations: {json.dumps(meta['limitations'])}\nSocial: {json.dumps(meta.get('social', {}))}\n\n"
              f"EVIDENCE\nVerified claims:\n{claim_lines}\n\n{docs_prompt(docs, include_facts_only=False)}")
    fc = llm.json("factcheck", sys_f, user_f, schemas.FACTCHECK, story_id=story_id, context={})

    evidence = {
        "id": story_id,
        "documents": [{k: d[k] for k in ("n", "url", "title", "publisher", "owner_org", "tier", "rights", "role", "retrieved_at", "sha256", "truncated", "note")} for d in docs],
        "claims": [{k: c[k] for k in ("id", "text", "kind", "doc", "span", "status")} for c in kept],
        "dropped_claims": [{"text": c.get("text", ""), "why": c["why"]} for c in dropped],
        "checks": {k: v.as_dict() for k, v in results.items()},
        "factcheck": fc,
        "corroboration": corro,
        "prompts": {"extract": ver_x, "draft": ver_d, "factcheck": ver_f},
        "models": {s: config.pipeline()["llm"]["stages"][s]["model"] for s in ("extract", "draft", "factcheck")},
        "triage": cand.get("triage", {}),
        "risk_flags": sorted(set(x.get("risk_flags", []) + cand.get("triage", {}).get("risk_flags", []))),
    }
    return Story(meta=meta, body=body), evidence


def assemble(story_id: str, cand: dict, x: dict, draft: dict, docs: list[dict], kept: list[dict], corro: str) -> tuple[dict, str]:
    used = {c["doc"] for c in kept}
    sources = []
    for d in docs:
        if d["n"] in used:
            role = "primary" if d["rights"] in READABLE and d["tier"] <= 2 else ("corroborating" if d["rights"] in READABLE else "lead")
        elif d["role"] == "lead":
            role = "lead"  # credit the outlet whose report led us to the story
        else:
            continue
        sources.append({"title": d["title"][:160], "publisher": d["publisher"], "url": d["url"],
                        "accessed_at": d["retrieved_at"], "tier": d["tier"], "role": role, "rights": d["rights"]})
    km = draft.get("key_metric") or x.get("key_metric") or {}
    section = draft.get("section") or x.get("section") or (cand.get("sections_hint") or ["world"])[0]
    if section not in config.section_names():
        section = "world"
    s = x.get("scores", {})
    meta = {
        "id": story_id,
        "slug": slugify(draft["title"], 70),
        "version": 1,
        "status": "draft",
        "title": draft["title"].strip(),
        "dek": draft["dek"].strip(),
        "section": section,
        "category": x.get("category", "A"),
        "tags": draft.get("tags", [])[:6],
        "geography": x.get("geography", []),
        "published_at": None,
        "updated_at": None,
        "key_metric": {k: km[k] for k in ("label", "before", "after", "period")} if km.get("present") else None,
        "why_it_matters": draft["why_it_matters"].strip(),
        "limitations": [l.strip() for l in draft.get("limitations", []) if l.strip()],
        "sources": sources,
        "review": {"mode": "pending", "editor": None, "reviewed_at": None},
        "ai_disclosure": "",
        "scores": {"positivity": s.get("positivity", 0), "significance": s.get("significance", 0),
                   "novelty": s.get("novelty", 0), "risk": s.get("risk", 0)},
        "corroboration": corro,
        "evidence": f"evidence/{story_id}.json",
        "social": draft.get("social", {}),
        "newsletter_blurb": draft.get("newsletter_blurb", ""),
        "corrections": [],
    }
    return meta, draft["body_markdown"].strip()


# ---------------------------------------------------------------------------- gate
def gate(meta: dict, evidence: dict, results: dict, cand: dict) -> tuple[str, list[str]]:
    """Returns ('auto' | 'review' | 'reject', reasons)."""
    ed = config.pipeline()["editorial"]
    reasons = []
    failed = [k for k, v in evidence["checks"].items() if v["status"] == checks.FAIL]
    if failed:
        reasons.append(f"failed checks: {', '.join(failed)}")
    fc = evidence.get("factcheck", {})
    if not fc.get("pass"):
        reasons.append("fact-check did not pass")
    if failed and "structure" in failed and len(failed) > 2:
        return "reject", reasons
    flags = set(evidence.get("risk_flags", [])) & set(ed.get("always_human_topics", []))
    if flags:
        reasons.append(f"always-human topic: {', '.join(sorted(flags))}")
    if cand.get("always_human"):
        reasons.append("source requires human review")
    if ed.get("mode") != "auto_low_risk":
        reasons.append("editorial mode is review_all")
    ap = ed.get("auto_publish", {})
    s = meta["scores"]
    if s["positivity"] < ap.get("min_positivity", 70) or s["risk"] > ap.get("max_risk", 15):
        reasons.append("scores outside auto-publish limits")
    if meta["corroboration"] == "single_source":
        reasons.append("single source")
    if any(v["status"] == checks.WARN for v in evidence["checks"].values()):
        reasons.append("check warnings")
    if not reasons:
        return "auto", []
    return "review", reasons


def queue_story(story: Story, evidence: dict, reasons: list[str]) -> str:
    folder = QUEUE_DIR / story.id
    story.meta["status"] = "draft"
    save(story, folder / "story.md")
    write_json(folder / "evidence.json", evidence)
    (folder / "REVIEW.md").write_text(review_summary(story, evidence, reasons), encoding="utf-8")
    audit.record("queued", story=story.id, reasons=reasons, prompts=evidence.get("prompts"))
    return story.id


def publish_auto(story: Story, evidence: dict) -> str:
    """Mode B: publish with an AI-generated label. Only reachable when editorial.mode = auto_low_risk."""
    ts = iso()
    story.meta.update(status="published", published_at=ts, updated_at=ts,
                      review={"mode": "auto", "editor": None, "reviewed_at": None},
                      ai_disclosure="AI-generated and not reviewed by an editor before publication. Every figure, date and quote was checked automatically against the sources listed below.")
    path = story_path(story.meta)
    save(story, path)
    write_json(EVIDENCE_DIR / f"{story.id}.json", evidence)
    audit.record("published_auto", story=story.id, path=str(path.relative_to(path.parents[2])))
    return story.id


def review_summary(story: Story, evidence: dict, reasons: list[str]) -> str:
    m = story.meta
    lines = [f"# Review: {m['title']}", "", f"> {m['dek']}", "",
             f"**Section:** {m['section']} · **Category:** {m['category']} · **Corroboration:** {evidence['corroboration']} · "
             f"**Scores:** positivity {m['scores']['positivity']}, risk {m['scores']['risk']}", ""]
    if reasons:
        lines += ["**Why this needs a human:**", *[f"- {r}" for r in reasons], ""]
    lines += ["## Checks", "", "| Check | Result | Details |", "|---|---|---|"]
    for k, v in evidence["checks"].items():
        lines.append(f"| {k} | {v['status'].upper()} | {'; '.join(v['details'][:3]) or '-'} |")
    fc = evidence.get("factcheck", {})
    lines += ["", f"**AI fact-check:** {'pass' if fc.get('pass') else 'FAIL'}"]
    for fx in fc.get("fixes", [])[:8]:
        lines.append(f"- fix: {fx}")
    if fc.get("missing_caveat"):
        lines.append(f"- missing caveat: {fc['missing_caveat']}")
    lines += ["", "## Claims and their sources", ""]
    docs = {d["n"]: d for d in evidence["documents"]}
    for c in evidence["claims"]:
        d = docs.get(c["doc"], {})
        lines.append(f"- {c['text']}  \n  ↳ “{c['span']}” — [{d.get('publisher', '?')}]({d.get('url', '')})")
    if evidence.get("dropped_claims"):
        lines += ["", "<details><summary>Claims dropped by verification</summary>", ""]
        lines += [f"- {c['text']} ({c['why']})" for c in evidence["dropped_claims"][:10]]
        lines += ["", "</details>"]
    lines += ["", "## How to decide", "", "- **Approve:** merge this pull request (edit the story file first if needed).",
              "- **Reject:** close it with a one-line reason.", "- Every figure and quote must match the passages above."]
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------- run
def run(llm: LLM | None = None, fetcher: Fetcher | None = None, only_sources: list[str] | None = None) -> RunReport:
    llm = llm or LLM()
    fetcher = fetcher or Fetcher()
    vol = config.pipeline()["volumes"]
    report = RunReport()
    candidates, errors = discover(fetcher, only=only_sources)
    report.errors += errors
    candidates = candidates[: vol.get("max_candidates_per_run", 60)]
    report.candidates = len(candidates)
    if not candidates:
        audit.record("run", **{k: v for k, v in report.as_dict().items() if k != "rejected"})
        return report
    try:
        picked = triage(llm, candidates)
    except LLMError as e:
        report.errors.append(f"triage: {e}")
        return report
    report.shortlisted = len(picked)
    for cand in picked[: vol.get("max_drafts_per_run", 6)]:
        try:
            docs = gather_evidence(cand, candidates, fetcher)
            if not any(d["rights"] in READABLE for d in docs) and not any(d["rights"] == "FACTS_ONLY" for d in docs):
                report.rejected.append({"url": cand["url"], "stage": "evidence", "why": "nothing readable"})
                continue
            built = build_story(llm, cand, docs, report)
            if not built:
                continue
            story, evidence = built
            decision, reasons = gate(story.meta, evidence, evidence["checks"], cand)
            if decision == "reject":
                report.rejected.append({"url": cand["url"], "stage": "gate", "why": "; ".join(reasons)})
                audit.record("rejected", url=cand["url"], reasons=reasons)
            elif decision == "auto":
                report.published.append(publish_auto(story, evidence))
            else:
                report.queued.append(queue_story(story, evidence, reasons))
        except LLMError as e:
            report.errors.append(f"{cand['url']}: {e}")
        except Exception as e:  # keep the run going; record the failure
            log.exception("story failed")
            report.errors.append(f"{cand['url']}: {type(e).__name__}: {e}")
    audit.record("run", candidates=report.candidates, shortlisted=report.shortlisted,
                 queued=report.queued, published=report.published, errors=len(report.errors))
    return report
