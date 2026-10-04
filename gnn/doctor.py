"""Go-live readiness check: `python -m gnn doctor`. Exit code 1 if anything blocks launch."""
from __future__ import annotations

import os
import re

from . import ROOT, config

PLACEHOLDER = re.compile(r"\[[A-Z][A-Z /]+\]|example\.com")


def checks() -> list[tuple[str, str, str]]:
    """(status, area, message) with status in OK / WARN / BLOCK."""
    out = []
    site = config.site()
    pub = site.get("publisher", {})
    for key in ("legal_name", "company_number", "registered_address", "editor_of_record", "contact_email",
                "corrections_email", "complaints_email"):
        val = str(pub.get(key, ""))
        out.append(("BLOCK" if PLACEHOLDER.search(val) or not val else "OK", "publisher", f"{key}: {val or '(empty)'}"))
    for o in pub.get("owners", []):
        out.append(("BLOCK" if PLACEHOLDER.search(str(o.get("name", ""))) else "OK", "ownership", f"owner: {o.get('name')}"))
    base = site["site"]["base_url"]
    out.append(("BLOCK" if "example.com" in base else "OK", "site", f"base_url: {base}"))
    out.append(("WARN" if site["brand"]["name"] == "Good News News" else "OK", "brand", f"name: {site['brand']['name']} (working title?)"))
    nl = site.get("newsletter", {})
    out.append(("WARN" if not nl.get("signup_action") else "OK", "newsletter", f"signup form: {nl.get('signup_action') or 'not connected'}"))
    if nl.get("provider") == "buttondown" and not os.environ.get("BUTTONDOWN_API_KEY"):
        out.append(("BLOCK", "newsletter", "BUTTONDOWN_API_KEY not set"))
    if nl.get("provider") == "kit" and not os.environ.get("KIT_API_KEY"):
        out.append(("BLOCK", "newsletter", "KIT_API_KEY not set"))
    out.append(("OK" if os.environ.get("ANTHROPIC_API_KEY") else "BLOCK", "ai", "ANTHROPIC_API_KEY " + ("set" if os.environ.get("ANTHROPIC_API_KEY") else "not set")))
    mode = config.pipeline()["editorial"]["mode"]
    out.append(("OK" if mode == "review_all" else "WARN", "editorial", f"mode: {mode}"))
    unverified = [s["id"] for s in config.sources() if not s.get("verified")]
    out.append(("WARN" if unverified else "OK", "sources", f"{len(unverified)} feeds not yet verified: run `python -m gnn sources`, then set verified: true"))
    pages = list((ROOT / "content" / "pages").glob("*.md"))
    pending = [p.stem for p in pages if "legal_review_required: true" in p.read_text(encoding="utf-8")]
    out.append(("WARN" if pending else "OK", "legal", f"{len(pending)} policy pages awaiting solicitor sign-off (set legal_review_required: false when approved)"))
    return out


def main() -> int:
    blocked = 0
    for status, area, msg in checks():
        blocked += status == "BLOCK"
        print(f"{status:<5} {area:<11} {msg}")
    print(f"\n{'Ready to launch' if not blocked else f'{blocked} blocking item(s) before launch'}")
    return 1 if blocked else 0
