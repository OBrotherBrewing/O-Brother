"""Static site generator: content/ + templates/ → dist/.

Cookie-free, no third-party requests by default (fonts are self-hosted), so the site
needs no consent banner. Output works on any static host (Cloudflare Pages, Netlify,
GitHub Pages, S3).
"""
from __future__ import annotations

import json
import re
import shutil
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from pathlib import Path
from xml.sax.saxutils import escape

import markdown as md
import yaml
from jinja2 import Environment, FileSystemLoader, select_autoescape

from . import ROOT, config
from .stories import EVIDENCE_DIR, Story, published
from .util import fmt_date, read_json

TEMPLATES = ROOT / "templates"
STATIC = ROOT / "static"
PAGES = ROOT / "content" / "pages"
CATEGORY_LABELS = {"A": "Verified progress", "B": "Solution working", "C": "Human achievement",
                   "D": "Early research", "E": "Constructive context"}
CORRO_LABELS = {"primary_confirmed": "Confirmed by a primary source", "two_independent": "Two independent sources",
                "single_source": "Single source"}


def _dt(value) -> datetime:
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def _md(text: str) -> str:
    return md.markdown(text or "", extensions=["extra", "sane_lists", "smarty"])


def reading_minutes(text: str) -> int:
    return max(1, round(len(text.split()) / 220))


def env() -> Environment:
    e = Environment(loader=FileSystemLoader(TEMPLATES), autoescape=select_autoescape(["html", "xml"]),
                    trim_blocks=True, lstrip_blocks=True)
    fmt = config.site()["site"].get("date_format", "%d/%m/%Y")
    e.filters["date"] = lambda v, f=fmt: fmt_date(v, f) if v else ""
    e.filters["longdate"] = lambda v: f"{_dt(v).day} {_dt(v):%B %Y}" if v else ""
    e.filters["markdown"] = _md
    e.filters["host"] = lambda u: re.sub(r"^www\.", "", re.sub(r"^https?://([^/]+).*$", r"\1", u or ""))
    e.globals["CATEGORY_LABELS"] = CATEGORY_LABELS
    e.globals["CORRO_LABELS"] = CORRO_LABELS
    return e


def load_pages(ctx: dict) -> list[dict]:
    pages = []
    jenv = Environment(autoescape=False)
    for p in sorted(PAGES.glob("*.md")):
        text = p.read_text(encoding="utf-8")
        if not text.startswith("---"):
            continue
        _, front, body = text.split("---", 2)
        meta = yaml.safe_load(front) or {}
        body = jenv.from_string(body).render(**ctx)
        meta["html"] = _md(body)
        meta.setdefault("slug", p.stem)
        pages.append(meta)
    pages.sort(key=lambda m: (m.get("group", "z"), m.get("order", 99)))
    return pages


def story_view(s: Story, sections: dict) -> dict:
    m = dict(s.meta)
    m["body_html"] = _md(s.body)
    m["url"] = f"/stories/{m['slug']}/"
    m["section_name"] = sections.get(m.get("section"), m.get("section", "").title())
    m["category_label"] = CATEGORY_LABELS.get(m.get("category"), "")
    m["minutes"] = reading_minutes(s.body)
    ev = read_json(EVIDENCE_DIR / f"{m['id']}.json", {}) or {}
    docs = {d["n"]: d for d in ev.get("documents", [])}
    m["claims"] = [{**c, "doc_info": docs.get(c["doc"], {})} for c in ev.get("claims", [])]
    m["checks"] = ev.get("checks", {})
    m["corro_label"] = CORRO_LABELS.get(m.get("corroboration") or ev.get("corroboration", ""), "")
    m["n_sources"] = len(m.get("sources") or [])
    m["is_sample"] = "sample" in (m.get("tags") or [])
    return m


def build(out: Path | None = None, include_pending: bool = False, preview_banner: str = "", relative: bool = False) -> Path:
    out = out or ROOT / "dist"
    cfg = config.site()
    base_url = cfg["site"]["base_url"].rstrip("/")
    sections = config.section_names()
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    shutil.copytree(STATIC, out / "static")

    stories = []
    for s in published():
        mode = (s.meta.get("review") or {}).get("mode")
        if mode == "pending" and not include_pending:
            continue
        stories.append(story_view(s, sections))
    live = [s for s in stories if s.get("status") != "retracted"]
    now = datetime.now(timezone.utc)

    by_section = defaultdict(list)
    for s in live:
        by_section[s["section"]].append(s)
    recent = [s for s in live if _dt(s["published_at"]) >= now - timedelta(days=7)] or live
    top = sorted(recent, key=lambda s: (s["scores"].get("significance", 0), s["published_at"]), reverse=True)[: cfg["site"].get("top_progress_count", 3)]
    today = [s for s in live if _dt(s["published_at"]).date() == (live[0]["published_at"] and _dt(live[0]["published_at"]).date())] if live else []
    corrections = sorted(
        [{"story": s, **c} for s in stories for c in (s.get("corrections") or [])],
        key=lambda c: c["date"], reverse=True)

    ctx = {
        "brand": cfg["brand"], "site": cfg["site"], "publisher": cfg["publisher"], "newsletter": cfg.get("newsletter", {}),
        "analytics": cfg.get("analytics", {}), "social": cfg.get("social", {}),
        "sections": cfg["sections"], "section_names": sections, "build_time": now,
        "preview_banner": preview_banner,
    }
    pages = load_pages(ctx)
    ctx["pages"] = pages
    ctx["footer_groups"] = {g: [p for p in pages if p.get("group") == g] for g in ("about", "standards", "legal")}
    ledger = {
        "date": live[0]["published_at"] if live else now.isoformat(),
        "count": len(today),
        "sources": sum(s["n_sources"] for s in today),
        "claims": sum(len(s["claims"]) for s in today),
        "corrections_30d": sum(1 for c in corrections if _dt(c["date"] + "T00:00:00+00:00") >= now - timedelta(days=30)),
    }
    ctx["ledger"] = ledger
    j = env()

    def write(rel: str, template: str, **kw):
        path = out / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(j.get_template(template).render(**ctx, **kw), encoding="utf-8")

    write("index.html", "index.html", top=top, latest=live[:12], by_section=by_section, page_title="")
    write("latest/index.html", "list.html", items=live, heading="Latest", intro="Every story, newest first.", page_title="Latest")
    for sec in cfg["sections"]:
        items = live if sec["slug"] == "top-progress" else by_section.get(sec["slug"], [])
        if sec["slug"] == "top-progress":
            items = sorted(live, key=lambda s: s["scores"].get("significance", 0), reverse=True)[:30]
        write(f"section/{sec['slug']}/index.html", "list.html", items=items, heading=sec["name"], intro=sec.get("blurb", ""), page_title=sec["name"])
    for s in stories:
        related = [r for r in by_section.get(s["section"], []) if r["id"] != s["id"]][:3]
        write(f"stories/{s['slug']}/index.html", "story.html", s=s, related=related, page_title=s["title"],
              jsonld=json.dumps(jsonld(s, cfg, base_url), ensure_ascii=False))
        ev = EVIDENCE_DIR / f"{s['id']}.json"
        if ev.exists():
            (out / "evidence").mkdir(exist_ok=True)
            shutil.copy(ev, out / "evidence" / ev.name)
    for p in pages:
        write(f"{p['slug']}/index.html", "page.html", p=p, page_title=p["title"])
    write("corrections/index.html", "corrections.html", corrections=corrections, page_title="Corrections")
    write("newsletter/index.html", "newsletter.html", issues=newsletter_issues(), page_title=cfg["brand"]["newsletter_name"])
    write("trackers/index.html", "trackers.html", trackers=load_trackers(), page_title="Progress trackers")
    write("404.html", "404.html", page_title="Page not found")
    copy_newsletter_archive(out)

    (out / "feed.xml").write_text(rss(live[:50], cfg, base_url), encoding="utf-8")
    (out / "sitemap.xml").write_text(sitemap(stories, pages, cfg, base_url), encoding="utf-8")
    (out / "news-sitemap.xml").write_text(news_sitemap(live, cfg, base_url, now), encoding="utf-8")
    (out / "robots.txt").write_text(f"User-agent: *\nAllow: /\n\nSitemap: {base_url}/sitemap.xml\nSitemap: {base_url}/news-sitemap.xml\n", encoding="utf-8")
    (out / "_headers").write_text(HEADERS, encoding="utf-8")
    _og_images(stories, cfg, out)
    if relative:
        relativize(out)
    return out


def relativize(out: Path) -> None:
    """Rewrites root-relative links so the site works from any sub-path or file host
    (used for private previews). Directory links become explicit index.html files."""
    attr = re.compile(r'(href|src|content|action)="/(?!/)([^"#?]*)([#?][^"]*)?"')

    def fix(m, depth):
        name, path, tail = m.group(1), m.group(2), m.group(3) or ""
        if name == "content" and not path.startswith(("og/", "static/")):
            return m.group(0)
        if path == "" or path.endswith("/"):
            path += "index.html"
        return f'{name}="{"../" * depth}{path}{tail}"'

    for f in out.rglob("*.html"):
        depth = len(f.relative_to(out).parts) - 1
        text = f.read_text(encoding="utf-8")
        f.write_text(attr.sub(lambda m: fix(m, depth), text), encoding="utf-8")
    css = out / "static" / "style.css"
    css.write_text(css.read_text(encoding="utf-8").replace('url("/static/', 'url("'), encoding="utf-8")


HEADERS = """/*
  X-Content-Type-Options: nosniff
  Referrer-Policy: strict-origin-when-cross-origin
  Permissions-Policy: interest-cohort=(), browsing-topics=()
  X-Frame-Options: SAMEORIGIN
/static/*
  Cache-Control: public, max-age=31536000, immutable
"""


def _og_images(stories: list[dict], cfg: dict, out: Path) -> None:
    try:
        from . import social
    except Exception:
        return
    brand = {**cfg["brand"], "url": cfg["site"]["base_url"]}
    (out / "og").mkdir(exist_ok=True)
    for s in stories:
        try:
            social.render_og_image(s, brand, out / "og" / f"{s['slug']}.png", section_name=s["section_name"])
        except Exception:
            continue


def jsonld(s: dict, cfg: dict, base_url: str) -> dict:
    pub = cfg["publisher"]
    review = s.get("review") or {}
    author = ({"@type": "Person", "name": review["editor"]} if review.get("editor") else {"@type": "Organization", "name": cfg["brand"]["name"]})
    data = {
        "@context": "https://schema.org", "@type": "NewsArticle",
        "headline": s["title"][:110], "description": s.get("dek", ""),
        "datePublished": s["published_at"], "dateModified": s.get("updated_at") or s["published_at"],
        "author": [author],
        "publisher": {"@type": "Organization", "name": cfg["brand"]["name"], "legalName": pub.get("legal_name"),
                      "logo": {"@type": "ImageObject", "url": f"{base_url}/static/logo.png"}},
        "mainEntityOfPage": f"{base_url}{s['url']}",
        "image": [f"{base_url}/og/{s['slug']}.png"],
        "isBasedOn": [src["url"] for src in s.get("sources") or []],
        "articleSection": s["section_name"],
        "keywords": ", ".join(s.get("tags") or []),
    }
    if s.get("corrections"):
        data["correction"] = [{"@type": "CorrectionComment", "text": c["note"], "datePublished": c["date"]} for c in s["corrections"]]
    return data


def rss(items: list[dict], cfg: dict, base_url: str) -> str:
    entries = []
    for s in items:
        entries.append(
            f"<item><title>{escape(s['title'])}</title><link>{base_url}{s['url']}</link>"
            f"<guid isPermaLink=\"false\">{escape(s['id'])}</guid>"
            f"<pubDate>{format_datetime(_dt(s['published_at']))}</pubDate>"
            f"<category>{escape(s['section_name'])}</category>"
            f"<description>{escape(s.get('dek', ''))}</description></item>")
    b = cfg["brand"]
    return ("<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n<rss version=\"2.0\"><channel>"
            f"<title>{escape(b['name'])}</title><link>{base_url}/</link><description>{escape(b['tagline'])}</description>"
            f"<language>{cfg['site'].get('language', 'en')}</language>" + "".join(entries) + "</channel></rss>\n")


def sitemap(stories: list[dict], pages: list[dict], cfg: dict, base_url: str) -> str:
    urls = [f"{base_url}/", f"{base_url}/latest/", f"{base_url}/corrections/", f"{base_url}/newsletter/", f"{base_url}/trackers/"]
    urls += [f"{base_url}/section/{s['slug']}/" for s in cfg["sections"]]
    urls += [f"{base_url}/{p['slug']}/" for p in pages]
    body = "".join(f"<url><loc>{escape(u)}</loc></url>" for u in urls)
    body += "".join(f"<url><loc>{base_url}{s['url']}</loc><lastmod>{s.get('updated_at') or s['published_at']}</lastmod></url>" for s in stories)
    return f"<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n<urlset xmlns=\"http://www.sitemaps.org/schemas/sitemap/0.9\">{body}</urlset>\n"


def news_sitemap(stories: list[dict], cfg: dict, base_url: str, now: datetime) -> str:
    recent = [s for s in stories if _dt(s["published_at"]) >= now - timedelta(days=2)]
    name = escape(cfg["brand"]["name"])
    body = "".join(
        f"<url><loc>{base_url}{s['url']}</loc><news:news><news:publication><news:name>{name}</news:name>"
        f"<news:language>en</news:language></news:publication><news:publication_date>{s['published_at']}</news:publication_date>"
        f"<news:title>{escape(s['title'])}</news:title></news:news></url>" for s in recent)
    return ("<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n<urlset xmlns=\"http://www.sitemaps.org/schemas/sitemap/0.9\" "
            f"xmlns:news=\"http://www.google.com/schemas/sitemap-news/0.9\">{body}</urlset>\n")


def newsletter_issues() -> list[dict]:
    d = ROOT / "content" / "newsletter"
    issues = []
    for p in sorted(d.glob("*.json"), reverse=True):
        issues.append(json.loads(p.read_text(encoding="utf-8")))
    return issues[:60]


def copy_newsletter_archive(out: Path) -> None:
    d = ROOT / "content" / "newsletter"
    for p in d.glob("*.html"):
        dest = out / "newsletter" / p.stem / "index.html"
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(p, dest)


def load_trackers() -> list[dict]:
    out = []
    for p in sorted((ROOT / "data" / "trackers").glob("*.json")):
        t = json.loads(p.read_text(encoding="utf-8"))
        t["svg"] = sparkline_svg(t.get("series", []))
        out.append(t)
    return out


def sparkline_svg(series: list[list], w: int = 320, h: int = 96, pad: int = 8) -> str:
    """Server-rendered line chart (no JavaScript). series = [[year, value], ...]."""
    pts = [(float(x), float(y)) for x, y in series if y is not None]
    if len(pts) < 2:
        return ""
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
    sx = lambda x: pad + (x - x0) / ((x1 - x0) or 1) * (w - 2 * pad)
    sy = lambda y: h - pad - (y - y0) / ((y1 - y0) or 1) * (h - 2 * pad)
    path = " ".join(f"{'M' if i == 0 else 'L'}{sx(x):.1f},{sy(y):.1f}" for i, (x, y) in enumerate(pts))
    area = path + f" L{sx(xs[-1]):.1f},{h - pad} L{sx(xs[0]):.1f},{h - pad} Z"
    lx, ly = sx(xs[-1]), sy(ys[-1])
    return (f'<svg viewBox="0 0 {w} {h}" class="spark" role="img" aria-hidden="true">'
            f'<path d="{area}" class="spark-area"/><path d="{path}" class="spark-line"/>'
            f'<circle cx="{lx:.1f}" cy="{ly:.1f}" r="4" class="spark-dot"/></svg>')
