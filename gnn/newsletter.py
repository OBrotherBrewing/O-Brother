"""The Better Brief: builds the daily email from the last day's reviewed stories and hands it
to the email provider as a draft (default) or a scheduled send.

Provider adapters are deliberately small. Check them against the provider's current API
docs before the first live send (see docs/RUNBOOK.md).
"""
from __future__ import annotations

import json
import os
import urllib.request
from datetime import datetime, timedelta, timezone

from jinja2 import Environment, FileSystemLoader, select_autoescape

from . import ROOT, audit, config
from .site import CATEGORY_LABELS, story_view
from .stories import published
from .util import write_json

OUT_DIR = ROOT / "content" / "newsletter"


def pick_stories(since_hours: int = 26, max_items: int | None = None) -> list[dict]:
    cfg = config.site()
    max_items = max_items or cfg.get("newsletter", {}).get("max_items", 7)
    cutoff = datetime.now(timezone.utc) - timedelta(hours=since_hours)
    sections = config.section_names()
    items = []
    for s in published(include_retracted=False):
        if (s.meta.get("review") or {}).get("mode") == "pending":
            continue
        pub = datetime.fromisoformat(str(s.meta["published_at"]).replace("Z", "+00:00"))
        if pub < cutoff:
            continue
        # newsletters can't be recalled: only stories that have been live for 6 hours
        if pub > datetime.now(timezone.utc) - timedelta(hours=6) and not os.environ.get("GNN_NEWSLETTER_NO_DELAY"):
            continue
        items.append(story_view(s, sections))
    items.sort(key=lambda v: v["scores"].get("significance", 0), reverse=True)
    return items[:max_items]


def build(date: datetime | None = None, since_hours: int = 26) -> dict | None:
    cfg = config.site()
    date = date or datetime.now(timezone.utc)
    items = pick_stories(since_hours)
    if not items:
        return None
    brand = cfg["brand"]
    n = len(items)
    subject = f"{n} {'thing' if n == 1 else 'things'} that got better: {items[0]['title']}"
    if len(subject) > 120:
        subject = subject[:117].rsplit(" ", 1)[0] + "…"
    env = Environment(loader=FileSystemLoader(ROOT / "templates"), autoescape=select_autoescape(["html"]))
    ctx = dict(brand=brand, site=cfg["site"], publisher=cfg["publisher"], items=items, date=date,
               subject=subject, base_url=cfg["site"]["base_url"].rstrip("/"), labels=CATEGORY_LABELS)
    html = env.get_template("email.html").render(**ctx)
    text = env.get_template("email.txt").render(**ctx)
    slug = date.strftime("%Y-%m-%d")
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / f"{slug}.html").write_text(html, encoding="utf-8")
    (OUT_DIR / f"{slug}.txt").write_text(text, encoding="utf-8")
    issue = {"slug": slug, "date": date.isoformat(), "subject": subject, "stories": [i["id"] for i in items]}
    write_json(OUT_DIR / f"{slug}.json", issue)
    audit.record("newsletter_built", issue=slug, stories=issue["stories"])
    return {**issue, "html": html, "text": text}


def _post(url: str, headers: dict, payload: dict) -> dict:
    req = urllib.request.Request(url, data=json.dumps(payload).encode(), method="POST",
                                 headers={"Content-Type": "application/json", **headers})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read() or b"{}")


def send(issue: dict) -> str:
    """Creates the issue at the provider. Returns a short status line."""
    nl = config.site().get("newsletter", {})
    provider = nl.get("provider", "none")
    mode = nl.get("send_mode", "draft")
    if provider == "none":
        return "no provider configured: issue saved to content/newsletter/ only"
    if provider == "buttondown":
        key = os.environ["BUTTONDOWN_API_KEY"]
        payload = {"subject": issue["subject"], "body": issue["html"], "status": "draft" if mode == "draft" else "scheduled"}
        res = _post("https://api.buttondown.com/v1/emails", {"Authorization": f"Token {key}"}, payload)
    elif provider == "kit":
        key = os.environ["KIT_API_KEY"]
        payload = {"subject": issue["subject"], "content": issue["html"], "public": True, "send_at": None}
        res = _post("https://api.kit.com/v4/broadcasts", {"X-Kit-Api-Key": key}, payload)
    else:
        raise ValueError(f"unknown newsletter provider {provider!r}")
    audit.record("newsletter_sent_to_provider", issue=issue["slug"], provider=provider, mode=mode)
    return f"{provider}: created {mode} ({str(res)[:120]})"
