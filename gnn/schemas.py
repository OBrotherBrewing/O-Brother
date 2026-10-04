"""JSON schemas for structured outputs. All objects are closed (additionalProperties: false)."""

CATEGORIES = ["A", "B", "C", "D", "E", "F", "G", "X"]
RISK_FLAGS = ["health_claim", "medical", "legal", "financial_advice", "crime", "children",
              "private_individual", "elections", "conflict", "allegation"]


def _obj(props: dict) -> dict:
    return {"type": "object", "properties": props, "required": list(props), "additionalProperties": False}


_str = {"type": "string"}
_int = {"type": "integer"}
_bool = {"type": "boolean"}
_strs = {"type": "array", "items": _str}
_flags = {"type": "array", "items": {"type": "string", "enum": RISK_FLAGS}}

KEY_METRIC = _obj({"present": _bool, "label": _str, "before": _str, "after": _str, "period": _str})

TRIAGE = _obj({
    "items": {"type": "array", "items": _obj({
        "id": _str,
        "category": {"type": "string", "enum": CATEGORIES},
        "positivity": _int, "significance": _int, "risk": _int,
        "risk_flags": _flags,
        "section": _str,
        "reason": _str,
    })},
})

EXTRACT = _obj({
    "qualifies": _bool,
    "reason": _str,
    "outcome": _str,
    "category": {"type": "string", "enum": CATEGORIES},
    "section": _str,
    "geography": _strs,
    "claims": {"type": "array", "items": _obj({
        "id": _str, "text": _str, "doc": _int, "span": _str,
        "kind": {"type": "string", "enum": ["number", "date", "entity", "quote", "causal", "other"]},
        "needs_primary": _bool,
    })},
    "limitations": _strs,
    "key_metric": KEY_METRIC,
    "scores": _obj({"positivity": _int, "significance": _int, "novelty": _int, "risk": _int}),
    "risk_flags": _flags,
    "actors": _strs,
})

DRAFT = _obj({
    "title": _str,
    "dek": _str,
    "body_markdown": _str,
    "why_it_matters": _str,
    "limitations": _strs,
    "key_metric": KEY_METRIC,
    "tags": _strs,
    "social": _obj({"hook": _str, "slides": _strs, "caption": _str}),
    "newsletter_blurb": _str,
})

FACTCHECK = _obj({
    "assertions": {"type": "array", "items": _obj({
        "text": _str,
        "verdict": {"type": "string", "enum": ["supported", "unsupported", "contradicted", "overstated"]},
        "claim_ids": _strs,
        "note": _str,
    })},
    "headline_supported": _bool,
    "missing_caveat": _str,
    "fairness_issue": _str,
    "pass": _bool,
    "fixes": _strs,
})
