"""Stage 1: discover candidate stories from the source register and remove duplicates."""
from __future__ import annotations

import json
import re
from datetime import timedelta
from urllib.parse import parse_qsl, quote_plus, urlencode, urlparse, urlunparse

import feedparser

from . import ROOT, config
from .fetch import Fetcher
from .util import iso, now, read_json, sha256, write_json

SEEN = ROOT / "data" / "seen.json"
TRACKING = re.compile(r"^(utm_|fbclid|gclid|mc_|ref$|ref_src|cmpid|ito)")
STOP = set("a an the of to in on for and or with by from at as is are was were be been new its it this that".split())


def canonical_url(url: str) -> str:
    u = urlparse(url.strip())
    q = [(k, v) for k, v in parse_qsl(u.query) if not TRACKING.match(k)]
    return urlunparse((u.scheme or "https", (u.netloc or "").lower(), u.path.rstrip("/") or "/", "", urlencode(q), ""))


def words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", (text or "").lower()) if w not in STOP and len(w) > 2}


def similar(a: str, b: str, threshold: float = 0.6) -> bool:
    wa, wb = words(a), words(b)
    if not wa or not wb:
        return False
    return len(wa & wb) / len(wa | wb) >= threshold


def _strip_html(s: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", s or "")).strip()


def from_feed(source: dict, fetcher: Fetcher) -> list[dict]:
    status, body, _ = fetcher.raw(source["url"])
    if status != 200 or not body:
        raise RuntimeError(f"{source['id']}: HTTP {status}")
    feed = feedparser.parse(body)
    out = []
    for e in feed.entries:
        link = e.get("link")
        if not link:
            continue
        out.append({
            "source_id": source["id"],
            "url": canonical_url(link),
            "title": _strip_html(e.get("title", "")),
            "summary": _strip_html(e.get("summary", ""))[:1200],
            "published": e.get("published") or e.get("updated") or "",
        })
    return out


def from_gdelt(source: dict, fetcher: Fetcher, timespan: str = "24h") -> list[dict]:
    out = []
    for q in source.get("queries", []):
        params = f"query={quote_plus(q)}&mode=artlist&format=json&maxrecords=50&timespan={timespan}&sort=hybridrel"
        status, body, _ = fetcher.raw(f"{source['url']}?{params}")
        if status != 200:
            continue
        try:
            data = json.loads(body or b"{}")
        except json.JSONDecodeError:
            continue
        for a in data.get("articles", []):
            out.append({
                "source_id": source["id"],
                "url": canonical_url(a.get("url", "")),
                "title": a.get("title", ""),
                "summary": "",
                "published": a.get("seendate", ""),
                "owner_org": a.get("domain", ""),
            })
    return out


def load_seen() -> dict:
    return read_json(SEEN, {}) or {}


def save_seen(seen: dict, keep_days: int = 60) -> None:
    cutoff = (now() - timedelta(days=keep_days)).isoformat()
    write_json(SEEN, {k: v for k, v in seen.items() if v >= cutoff})


def discover(fetcher: Fetcher | None = None, only: list[str] | None = None) -> tuple[list[dict], list[str]]:
    """Returns (new candidates, errors). Candidates carry source metadata and a cluster key."""
    fetcher = fetcher or Fetcher()
    seen = load_seen()
    errors: list[str] = []
    raw: list[dict] = []
    for src in config.sources():
        if only and src["id"] not in only:
            continue
        if src.get("rights") == "RESTRICTED":
            continue
        try:
            items = from_gdelt(src, fetcher) if src.get("kind") == "gdelt" else from_feed(src, fetcher)
        except Exception as e:  # one broken feed must not stop the run
            errors.append(f"{src['id']}: {e}")
            continue
        for it in items:
            it.setdefault("owner_org", src.get("owner_org", ""))
            it.update(tier=src.get("tier", 4), rights=src.get("rights", "LINK_ONLY"),
                      source_name=src.get("name", src["id"]), sections_hint=src.get("sections", []),
                      always_human=bool(src.get("always_human")))
            raw.append(it)

    candidates: list[dict] = []
    for it in raw:
        key = sha256(it["url"])[:16]
        if key in seen or not it["title"]:
            continue
        seen[key] = iso()
        it["id"] = key
        # cluster with an earlier candidate on the same event
        for c in candidates:
            if similar(c["title"], it["title"]):
                it["cluster"] = c.get("cluster", c["id"])
                break
        else:
            it["cluster"] = key
        candidates.append(it)
    save_seen(seen)
    return candidates, errors
