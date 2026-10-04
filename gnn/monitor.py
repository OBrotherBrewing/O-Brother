"""After publication: re-check each story's sources and flag anything that changed.

A story's supporting passages are re-fetched at the intervals in pipeline.yaml
(monitor.recheck_after_hours). If a source disappears, or a passage we relied on is no
longer on the page, the story is flagged for an editor (never edited automatically).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from . import audit, config
from .fetch import Fetcher
from .stories import EVIDENCE_DIR, published
from .util import norm_space, read_json


def due(published_at: str, last_checked: str | None, hours: list[int]) -> bool:
    pub = datetime.fromisoformat(published_at.replace("Z", "+00:00"))
    age = datetime.now(timezone.utc) - pub
    marks = [timedelta(hours=h) for h in hours if age >= timedelta(hours=h)]
    if not marks:
        return False
    if not last_checked:
        return True
    last = datetime.fromisoformat(last_checked.replace("Z", "+00:00"))
    return last < pub + max(marks)


def check_story(story, fetcher: Fetcher) -> list[str]:
    ev = read_json(EVIDENCE_DIR / f"{story.id}.json", {}) or {}
    docs = {d["n"]: d for d in ev.get("documents", [])}
    problems = []
    pages = {}
    for c in ev.get("claims", []):
        d = docs.get(c["doc"])
        if not d or d.get("rights") == "LINK_ONLY":
            continue
        if d["url"] not in pages:
            pages[d["url"]] = fetcher.get(d["url"])
        page = pages[d["url"]]
        if page.status in (404, 410):
            problems.append(f"source gone (HTTP {page.status}): {d['url']}")
            continue
        if page.status != 200:
            continue  # temporary failure: try again next time
        if norm_space(c["span"]).lower() not in norm_space(page.text).lower():
            problems.append(f"passage no longer found at {d['url']}: “{c['span'][:90]}”")
    return sorted(set(problems))


def run(fetcher: Fetcher | None = None, max_age_days: int = 31) -> list[dict]:
    fetcher = fetcher or Fetcher()
    hours = config.pipeline().get("monitor", {}).get("recheck_after_hours", [6, 24, 168, 720])
    cutoff = datetime.now(timezone.utc) - timedelta(days=max_age_days)
    flagged = []
    for s in published(include_retracted=False):
        pub = s.meta.get("published_at")
        if not pub or datetime.fromisoformat(str(pub).replace("Z", "+00:00")) < cutoff:
            continue
        if not due(str(pub), s.meta.get("monitor_checked_at"), hours):
            continue
        problems = check_story(s, fetcher)
        audit.record("monitored", story=s.id, problems=len(problems))
        if problems:
            flagged.append({"story": s.id, "slug": s.slug, "title": s.meta["title"], "problems": problems})
    return flagged
