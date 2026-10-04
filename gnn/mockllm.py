"""Deterministic stand-in for the model, for tests and the offline demo. Costs nothing.

It never invents facts: claims are sentences lifted from the supplied documents, and the
draft quotes them verbatim inside quotation marks, so every deterministic check can run
for real. Its prose is deliberately flat; it exists to exercise the plumbing.
"""
from __future__ import annotations

import re


def _sentences(text: str) -> list[str]:
    out = []
    for block in re.split(r"\n\s*\n", text or ""):
        block = re.sub(r"\s+", " ", block).strip()
        out += [p.strip() for p in re.split(r"(?<=[.!?])\s+(?=[A-Z\"“])", block) if 40 <= len(p.strip()) <= 300]
    return out


def mock(stage: str, system: str, user: str, schema: dict, ctx: dict) -> dict:
    if stage == "triage":
        return {"items": [{
            "id": c["id"], "category": "A", "positivity": 75, "significance": 60, "risk": 5,
            "risk_flags": [], "section": (c.get("sections_hint") or ["world"])[0], "reason": "mock triage",
        } for c in ctx.get("candidates", [])]}

    if stage == "extract":
        claims = []
        for d in ctx.get("docs", []):
            if d["rights"] not in ("LICENSED", "PRIMARY_PUBLIC"):
                continue
            for s in _sentences(d["text"]):
                if re.search(r"\d", s) and len(claims) < 8:
                    claims.append({"id": f"c{len(claims) + 1}", "text": s, "doc": d["n"], "span": s,
                                   "kind": "number", "needs_primary": False})
        return {
            "qualifies": len(claims) >= 2, "reason": "mock extraction",
            "outcome": claims[0]["text"] if claims else "", "category": "A",
            "section": (ctx.get("candidate", {}).get("sections_hint") or ["world"])[0],
            "geography": ["GLOBAL"], "claims": claims,
            "limitations": ["The figures cover a single reporting period, so a trend is not yet established."],
            "key_metric": {"present": False, "label": "", "before": "", "after": "", "period": ""},
            "scores": {"positivity": 75, "significance": 60, "novelty": 50, "risk": 5},
            "risk_flags": [], "actors": [],
        }

    if stage == "draft":
        cand = ctx.get("candidate", {})
        claims = ctx.get("claims", [])
        pubs = {d["n"]: d["publisher"] for d in ctx.get("docs", [])}
        paras = [f'{pubs.get(c["doc"], "The source")} reported: "{c["span"]}"' for c in claims[:5]]
        filler = ("This layout sample was produced by the offline test model, which quotes its sources "
                  "word for word instead of writing original prose. The live pipeline writes in the house "
                  "style and is checked in exactly the same way before anything reaches a reader.")
        body = "\n\n".join(paras + [filler, filler.replace("This layout sample", "Each sample story")])
        return {
            "title": (cand.get("title") or "Sample story")[:88],
            "dek": f'{pubs.get(claims[0]["doc"], "A source")} reported progress.' if claims else "Sample.",
            "body_markdown": body,
            "why_it_matters": "This is a sample story generated offline to test the pipeline. It shows where the explanation of who benefits will appear.",
            "limitations": ["The figures cover a single reporting period, so a trend is not yet established."],
            "key_metric": {"present": False, "label": "", "before": "", "after": "", "period": ""},
            "tags": ["sample", "pipeline-test"],
            "social": {"hook": (cand.get("title") or "Sample")[:110],
                       "slides": [f'"{c["span"][:200]}"' for c in claims[:3]] or ["Sample slide"],
                       "caption": "Sample caption. Source: " + ", ".join(sorted(set(pubs.values())))},
            "newsletter_blurb": "Sample blurb for the daily email.",
        }

    if stage == "factcheck":
        return {"assertions": [], "headline_supported": True, "missing_caveat": "", "fairness_issue": "",
                "pass": True, "fixes": []}

    raise ValueError(f"mock has no stage {stage}")
