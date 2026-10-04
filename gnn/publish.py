"""Human decisions: approve, reject, correct, retract. Each one is written to the audit log.

On GitHub the approval is a merged pull request; `finalize` stamps the merger's name and
time onto stories still marked pending. Locally, `approve` does the same in one step.
"""
from __future__ import annotations

import shutil

from . import audit, config
from .stories import EVIDENCE_DIR, QUEUE_DIR, VERSIONS_DIR, Story, load, published, save, story_path
from .util import iso, read_json, write_json


def _disclosure(editor: str) -> str:
    return (f"Drafted with AI from the sources below, checked claim by claim against them, "
            f"and reviewed by {editor} before publication.")


def editor_name(login_or_name: str) -> str:
    return config.site().get("editors", {}).get(login_or_name, login_or_name)


def stage_for_review(story_id: str) -> Story:
    """Moves a queued draft into content/ marked pending, ready to go into a pull request."""
    folder = QUEUE_DIR / story_id
    story = load(folder / "story.md")
    ts = iso()
    story.meta.update(status="published", published_at=ts, updated_at=ts,
                      review={"mode": "pending", "editor": None, "reviewed_at": None})
    path = story_path(story.meta)
    save(story, path)
    write_json(EVIDENCE_DIR / f"{story.id}.json", read_json(folder / "evidence.json", {}))
    shutil.rmtree(folder)
    audit.record("staged_for_review", story=story.id)
    return story


def finalize(editor: str, at: str | None = None) -> list[str]:
    """Stamps every pending story with the approving editor (run after a PR merge)."""
    done = []
    name = editor_name(editor)
    for s in published():
        if (s.meta.get("review") or {}).get("mode") == "pending":
            s.meta["review"] = {"mode": "reviewed", "editor": name, "reviewed_at": at or iso()}
            s.meta["ai_disclosure"] = _disclosure(name)
            save(s, s.path)
            audit.record("approved", story=s.id, editor=name)
            done.append(s.id)
    return done


def approve(story_id: str, editor: str) -> Story:
    """Local one-step approval: queue → published, reviewed by `editor`."""
    story = stage_for_review(story_id)
    finalize(editor)
    return load(story_path(story.meta))


def reject(story_id: str, reason: str, editor: str) -> None:
    folder = QUEUE_DIR / story_id
    if folder.exists():
        shutil.rmtree(folder)
    audit.record("rejected", story=story_id, reason=reason, editor=editor_name(editor))


def _archive(story: Story) -> None:
    dest = VERSIONS_DIR / story.id / f"v{story.meta.get('version', 1)}.md"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(story.to_text(), encoding="utf-8")


def correct(slug_or_id: str, note: str, editor: str, kind: str = "correction",
            new_body: str | None = None, meta_changes: dict | None = None) -> Story:
    """Never edits facts silently: archives the old version, bumps the version, adds a dated note."""
    if kind not in ("correction", "clarification", "update", "retraction"):
        raise ValueError("kind must be correction, clarification, update or retraction")
    from .stories import find
    story = find(slug_or_id)
    _archive(story)
    if new_body is not None:
        story.body = new_body
    for k, v in (meta_changes or {}).items():
        story.meta[k] = v
    ts = iso()
    story.meta["version"] = int(story.meta.get("version", 1)) + 1
    story.meta["updated_at"] = ts
    story.meta.setdefault("corrections", []).append({"date": ts[:10], "type": kind, "note": note})
    if kind == "retraction":
        story.meta["status"] = "retracted"
        story.body = f"This story has been retracted. {note}"
    elif kind == "correction":
        story.meta["status"] = "corrected"
    save(story, story.path)
    audit.record(kind, story=story.id, note=note, editor=editor_name(editor), version=story.meta["version"])
    return story
