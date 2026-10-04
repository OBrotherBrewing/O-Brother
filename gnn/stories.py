"""Reading and writing story files (YAML front matter + Markdown body)."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

from . import ROOT

STORIES_DIR = ROOT / "content" / "stories"
QUEUE_DIR = ROOT / "queue"
EVIDENCE_DIR = ROOT / "data" / "evidence"
VERSIONS_DIR = ROOT / "data" / "versions"


@dataclass
class Story:
    meta: dict
    body: str
    path: Path | None = None
    extra: dict = field(default_factory=dict)

    @property
    def id(self) -> str:
        return self.meta["id"]

    @property
    def slug(self) -> str:
        return self.meta["slug"]

    def to_text(self) -> str:
        front = yaml.safe_dump(self.meta, sort_keys=False, allow_unicode=True, width=1000)
        return f"---\n{front}---\n\n{self.body.strip()}\n"


def parse(text: str, path: Path | None = None) -> Story:
    if not text.startswith("---"):
        raise ValueError(f"{path}: missing front matter")
    _, front, body = text.split("---", 2)
    return Story(meta=yaml.safe_load(front) or {}, body=body.strip(), path=path)


def load(path: Path) -> Story:
    return parse(path.read_text(encoding="utf-8"), path)


def save(story: Story, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(story.to_text(), encoding="utf-8")
    story.path = path
    return path


def story_path(meta: dict) -> Path:
    year = str(meta["published_at"])[:4]
    return STORIES_DIR / year / f"{meta['slug']}.md"


def published(include_retracted: bool = True) -> list[Story]:
    out = []
    for p in sorted(STORIES_DIR.rglob("*.md")):
        s = load(p)
        if s.meta.get("status") == "retracted" and not include_retracted:
            continue
        out.append(s)
    out.sort(key=lambda s: str(s.meta.get("published_at", "")), reverse=True)
    return out


def queued() -> list[Story]:
    out = []
    for p in sorted(QUEUE_DIR.glob("*/story.md")):
        out.append(load(p))
    return out


def find(slug_or_id: str) -> Story:
    for s in published():
        if s.slug == slug_or_id or s.id == slug_or_id:
            return s
    raise KeyError(f"no published story {slug_or_id!r}")
