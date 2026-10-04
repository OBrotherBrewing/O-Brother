"""Polite fetching: robots.txt, text-and-data-mining reservations, snapshots.

Rights rules (Irish S.I. 567/2021 reg. 15 / CRRA s.53A as amended): a rightsholder can
reserve text-and-data mining in machine-readable form, including metadata and website
terms. We treat any of these as a reservation and downgrade the page to LINK_ONLY:
  - HTTP header `tdm-reservation: 1`
  - <meta name="tdm-reservation" content="1">
  - <meta name="robots"> containing noai / noimageai
  - /.well-known/tdmrep.json covering the path with "tdm-reservation": 1
  - robots.txt disallowing our user agent
Website terms in prose can't be read reliably by code; sources.yaml records any we know of.
"""
from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
import urllib.robotparser
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin, urlparse

from . import ROOT, config
from .util import iso, sha256

SNAP_DIR = ROOT / ".cache" / "snapshots"


@dataclass
class Page:
    url: str
    status: int
    html: str
    headers: dict
    retrieved_at: str
    sha256: str
    text: str = ""
    title: str = ""
    links: list[tuple[str, str]] = field(default_factory=list)  # (absolute url, anchor text)
    tdm_reserved: bool = False
    robots_allowed: bool = True
    note: str = ""


class _Extractor(HTMLParser):
    """Keeps paragraph-level text and links; drops scripts, navigation and footers."""

    SKIP = {"script", "style", "nav", "footer", "header", "aside", "form", "noscript", "svg", "button"}
    BLOCK = {"p", "h1", "h2", "h3", "h4", "li", "blockquote", "figcaption", "td", "th", "dd", "dt"}

    def __init__(self, base: str):
        super().__init__(convert_charrefs=True)
        self.base = base
        self.skip_depth = 0
        self.block_depth = 0
        self.buf: list[str] = []
        self.blocks: list[str] = []
        self.links: list[tuple[str, str]] = []
        self._href: str | None = None
        self._atext: list[str] = []
        self.title = ""
        self._in_title = False
        self.meta: dict[str, str] = {}

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag in self.SKIP:
            self.skip_depth += 1
        if tag == "title":
            self._in_title = True
        if tag == "meta":
            name = (a.get("name") or a.get("property") or "").lower()
            if name:
                self.meta[name] = a.get("content") or ""
        if self.skip_depth:
            return
        if tag in self.BLOCK:
            self.block_depth += 1
        if tag == "a" and a.get("href"):
            self._href = urljoin(self.base, a["href"])
            self._atext = []
        if tag == "br":
            self.buf.append(" ")

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False
        if tag in self.SKIP and self.skip_depth:
            self.skip_depth -= 1
            return
        if self.skip_depth:
            return
        if tag == "a" and self._href:
            self.links.append((self._href, " ".join(self._atext).strip()))
            self._href = None
        if tag in self.BLOCK and self.block_depth:
            self.block_depth -= 1
            if self.block_depth == 0:
                block = re.sub(r"\s+", " ", "".join(self.buf)).strip()
                if len(block) > 1:
                    self.blocks.append(block)
                self.buf = []

    def handle_data(self, data):
        if self._in_title:
            self.title += data
        if self.skip_depth:
            return
        if self._href is not None:
            self._atext.append(data)
        if self.block_depth:
            self.buf.append(data)


def extract(html: str, base_url: str) -> tuple[str, str, list[tuple[str, str]], dict]:
    p = _Extractor(base_url)
    try:
        p.feed(html)
    except Exception:  # malformed markup: keep what we parsed
        pass
    text = "\n\n".join(p.blocks)
    return text, p.title.strip(), p.links, p.meta


def is_primary_url(url: str) -> bool:
    host = urlparse(url).hostname or ""
    for d in config.primary_domains():
        if host == d or host.endswith("." + d) or (len(d.split(".")) == 1 and host.endswith("." + d)):
            return True
    return False


class Fetcher:
    """Real HTTP fetcher. Swap for FixtureFetcher in tests and offline demos."""

    def __init__(self):
        cfg = config.pipeline().get("fetch", {})
        self.ua = cfg.get("user_agent", "GoodNewsNewsBot/0.1")
        self.timeout = cfg.get("timeout_seconds", 20)
        self.respect_robots = cfg.get("respect_robots", True)
        self.respect_tdm = cfg.get("respect_tdm_reservation", True)
        self.max_bytes = cfg.get("max_bytes", 3_000_000)
        self._robots: dict[str, urllib.robotparser.RobotFileParser | None] = {}
        self._tdmrep: dict[str, list] = {}

    # -- raw transport -------------------------------------------------
    def _http(self, url: str) -> tuple[int, bytes, dict]:
        req = urllib.request.Request(url, headers={"User-Agent": self.ua, "Accept": "*/*"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                body = r.read(self.max_bytes)
                return r.status, body, {k.lower(): v for k, v in r.headers.items()}
        except urllib.error.HTTPError as e:
            return e.code, b"", {k.lower(): v for k, v in (e.headers or {}).items()}

    def raw(self, url: str) -> tuple[int, bytes, dict]:
        return self._http(url)

    # -- rights checks -------------------------------------------------
    def robots_allowed(self, url: str) -> bool:
        if not self.respect_robots:
            return True
        u = urlparse(url)
        key = f"{u.scheme}://{u.netloc}"
        if key not in self._robots:
            rp = urllib.robotparser.RobotFileParser()
            try:
                status, body, _ = self._http(key + "/robots.txt")
                rp.parse(body.decode("utf-8", "ignore").splitlines() if status == 200 else [])
                self._robots[key] = rp
            except Exception:
                self._robots[key] = None
        rp = self._robots[key]
        return True if rp is None else rp.can_fetch(self.ua, url)

    def tdmrep_reserved(self, url: str) -> bool:
        u = urlparse(url)
        key = f"{u.scheme}://{u.netloc}"
        if key not in self._tdmrep:
            try:
                status, body, _ = self._http(key + "/.well-known/tdmrep.json")
                self._tdmrep[key] = json.loads(body) if status == 200 else []
            except Exception:
                self._tdmrep[key] = []
        for rule in self._tdmrep[key] or []:
            loc = str(rule.get("location", "")).replace("*", "")
            if u.path.startswith(loc or "/") and str(rule.get("tdm-reservation")) == "1":
                return True
        return False

    # -- main entry ----------------------------------------------------
    def get(self, url: str) -> Page:
        allowed = self.robots_allowed(url)
        if not allowed:
            return Page(url, 0, "", {}, iso(), "", robots_allowed=False, tdm_reserved=True,
                        note="robots.txt disallows our crawler; treated as LINK_ONLY")
        status, body, headers = self._http(url)
        html = body.decode(_charset(headers), "ignore")
        return self._page(url, status, html, headers)

    def _page(self, url: str, status: int, html: str, headers: dict) -> Page:
        text, title, links, meta = extract(html, url)
        notes = []
        if self.respect_tdm:
            if str(headers.get("tdm-reservation", "")).strip() == "1":
                notes.append("tdm-reservation header")
            if str(meta.get("tdm-reservation", "")).strip() == "1":
                notes.append("tdm-reservation meta")
            robots_meta = meta.get("robots", "").lower()
            if "noai" in robots_meta or "noimageai" in robots_meta:
                notes.append("robots noai meta")
            if self.tdmrep_reserved(url):
                notes.append("tdmrep.json")
        reserved = bool(notes)
        page = Page(url, status, html, headers, iso(), sha256(html), text=text, title=title,
                    links=links, tdm_reserved=reserved, note="; ".join(notes))
        snapshot(page)
        return page


class FixtureFetcher(Fetcher):
    """Serves pages from a dict {url: html} or a directory of files. No network."""

    def __init__(self, pages: dict[str, str] | None = None, directory: Path | None = None,
                 headers: dict[str, dict] | None = None):
        super().__init__()
        self.pages = dict(pages or {})
        self.headers = headers or {}
        if directory:
            index = directory / "index.json"
            if index.exists():
                for url, fname in json.loads(index.read_text()).items():
                    self.pages[url] = (directory / fname).read_text(encoding="utf-8")

    def _http(self, url: str):
        if url.endswith("/robots.txt") or url.endswith("/.well-known/tdmrep.json"):
            return 404, b"", {}
        if url in self.pages:
            return 200, self.pages[url].encode(), self.headers.get(url, {"content-type": "text/html; charset=utf-8"})
        return 404, b"", {}


def _charset(headers: dict) -> str:
    m = re.search(r"charset=([\w-]+)", headers.get("content-type", ""))
    return m.group(1) if m else "utf-8"


def snapshot(page: Page) -> Path | None:
    """Keeps a private copy of what we read (never committed: .cache is git-ignored)."""
    if not page.html:
        return None
    SNAP_DIR.mkdir(parents=True, exist_ok=True)
    p = SNAP_DIR / f"{page.sha256}.html"
    if not p.exists():
        p.write_text(page.html, encoding="utf-8")
        (SNAP_DIR / f"{page.sha256}.txt").write_text(page.text, encoding="utf-8")
    return p
