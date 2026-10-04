"""Command line: python -m gnn <command>. Run `python -m gnn -h` for the list."""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


def _print(obj) -> None:
    print(json.dumps(obj, indent=2, ensure_ascii=False, default=str))


def cmd_run(a):
    from .fetch import FixtureFetcher, Fetcher
    from .llm import LLM
    from .pipeline import run
    llm = LLM()
    if a.mock:
        from .mockllm import mock
        llm = LLM(mock=mock)
    fetcher = FixtureFetcher(directory=Path(a.fixtures)) if a.fixtures else Fetcher()
    report = run(llm=llm, fetcher=fetcher, only_sources=a.only.split(",") if a.only else None)
    _print(report.as_dict())
    return 1 if report.errors and not (report.queued or report.published) and report.candidates else 0


def cmd_sources(a):
    from . import config
    from .fetch import Fetcher
    import feedparser
    f = Fetcher()
    ok = 0
    for s in config.sources():
        try:
            if s.get("kind") == "gdelt":
                status, body, _ = f.raw(f"{s['url']}?query=solar&mode=artlist&format=json&maxrecords=1")
                n = len(json.loads(body or b"{}").get("articles", [])) if status == 200 else 0
            else:
                status, body, _ = f.raw(s["url"])
                n = len(feedparser.parse(body).entries) if status == 200 else 0
            good = status == 200 and n > 0
            ok += good
            print(f"{'OK  ' if good else 'FAIL'} {s['id']:<20} HTTP {status} entries={n}")
        except Exception as e:
            print(f"FAIL {s['id']:<20} {type(e).__name__}: {e}")
    print(f"{ok}/{len(config.sources())} feeds working")


def cmd_queue(a):
    from .stories import queued
    for s in queued():
        print(f"{s.id}\t{s.meta['section']}\t{s.meta['title']}")


def cmd_stage(a):
    from .publish import stage_for_review
    s = stage_for_review(a.id)
    print(s.path)


def cmd_approve(a):
    from .publish import approve
    s = approve(a.id, a.editor)
    print(f"published {s.slug} reviewed by {s.meta['review']['editor']}")


def cmd_finalize(a):
    from .publish import finalize
    _print(finalize(a.editor))


def cmd_reject(a):
    from .publish import reject
    reject(a.id, a.reason, a.editor)
    print("rejected", a.id)


def cmd_correct(a):
    from .publish import correct
    s = correct(a.slug, a.note, a.editor, kind=a.kind)
    print(f"{a.kind} recorded on {s.slug} (version {s.meta['version']})")


def cmd_build(a):
    from .site import build
    out = build(Path(a.out) if a.out else None, include_pending=a.include_pending, preview_banner=a.banner or "", relative=a.relative)
    print(out)


def cmd_newsletter(a):
    from . import newsletter
    issue = newsletter.build(since_hours=a.since)
    if not issue:
        print("no stories for an issue")
        return 0
    print(f"built {issue['slug']}: {issue['subject']}")
    if a.send:
        print(newsletter.send(issue))


def cmd_social(a):
    from . import config, social
    from .site import story_view
    from .stories import find, published
    cfg = config.site()
    brand = {**cfg["brand"], "url": cfg["site"]["base_url"]}
    sections = config.section_names()
    targets = [find(a.slug)] if a.slug else published(include_retracted=False)[: a.top]
    out = Path(a.out)
    for st in targets:
        v = story_view(st, sections)
        d = out / v["slug"]
        slides = social.render_carousel(v, brand, d, section_name=v["section_name"])
        print(f"{v['slug']}: {len(slides)} slides")
        if a.video:
            print(social.render_story_video(slides, v, brand, d / f"{v['slug']}.mp4", section_name=v["section_name"]))
        (d / "caption.txt").write_text((v.get("social") or {}).get("caption", v["dek"]), encoding="utf-8")


def cmd_audit(a):
    from .audit import verify
    ok, n, msg = verify()
    print(f"{'OK' if ok else 'BROKEN'}: {msg} ({n} entries)")
    return 0 if ok else 1


def cmd_monitor(a):
    from .monitor import run
    flagged = run()
    _print(flagged)
    if flagged and a.issue_file:
        lines = ["Source changes found after publication. An editor should check each story and correct it if needed.", ""]
        for f in flagged:
            lines.append(f"### {f['title']} (`{f['slug']}`)")
            lines += [f"- {p}" for p in f["problems"]]
            lines.append("")
        Path(a.issue_file).write_text("\n".join(lines), encoding="utf-8")


def cmd_trackers(a):
    from .trackers import update
    for line in update():
        print(line)


def cmd_doctor(a):
    from .doctor import main
    return main()


def cmd_demo(a):
    """Offline end to end: fictional fixtures + mock model → queue → approve → site."""
    from . import ROOT
    scratch = Path(a.root or tempfile.mkdtemp(prefix="gnn-demo-"))
    if scratch.exists():
        shutil.rmtree(scratch)
    for d in ("config", "prompts", "templates", "static", "assets", "content/pages"):
        src = ROOT / d
        if src.exists():
            shutil.copytree(src, scratch / d)
    shutil.copy(ROOT / "tests/fixtures/demo/sources.yaml", scratch / "config/sources.yaml")
    env = {**os.environ, "GNN_ROOT": str(scratch), "GNN_NEWSLETTER_NO_DELAY": "1"}
    py = [sys.executable, "-m", "gnn"]
    steps = [
        py + ["run", "--mock", "--fixtures", str(ROOT / "tests/fixtures/demo")],
        py + ["_approve_all", "--editor", "Sample Editor"],
        py + ["newsletter"],
        py + ["build", "--banner", a.banner],
    ]
    for cmd in steps:
        r = subprocess.run(cmd, env=env, cwd=ROOT)
        if r.returncode:
            print("demo step failed:", " ".join(cmd[2:]))
            return r.returncode
    print(f"demo site: {scratch / 'dist'}")


def cmd_approve_all(a):
    from .publish import approve
    from .stories import queued
    for s in queued():
        approve(s.id, a.editor)
        print("approved", s.id)


def main(argv=None):
    p = argparse.ArgumentParser(prog="gnn", description="Good News News newsroom tools")
    sub = p.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run", help="one pipeline run: discover → draft → checks → queue")
    r.add_argument("--mock", action="store_true", help="use the offline mock model (no API key, no cost)")
    r.add_argument("--fixtures", help="serve pages from a fixtures directory instead of the web")
    r.add_argument("--only", help="comma-separated source ids")
    r.set_defaults(fn=cmd_run)

    sub.add_parser("sources", help="test every feed in config/sources.yaml").set_defaults(fn=cmd_sources)
    sub.add_parser("queue", help="list drafts awaiting review").set_defaults(fn=cmd_queue)

    s = sub.add_parser("stage", help="move a draft into content/ as pending (for a pull request)")
    s.add_argument("id"); s.set_defaults(fn=cmd_stage)

    s = sub.add_parser("approve", help="publish a draft, reviewed by EDITOR")
    s.add_argument("id"); s.add_argument("--editor", required=True); s.set_defaults(fn=cmd_approve)

    s = sub.add_parser("finalize", help="stamp pending stories as reviewed (after a PR merge)")
    s.add_argument("--editor", required=True); s.set_defaults(fn=cmd_finalize)

    s = sub.add_parser("reject", help="discard a draft with a reason")
    s.add_argument("id"); s.add_argument("--reason", required=True); s.add_argument("--editor", required=True)
    s.set_defaults(fn=cmd_reject)

    s = sub.add_parser("correct", help="add a dated correction/update/retraction to a story")
    s.add_argument("slug"); s.add_argument("--note", required=True); s.add_argument("--editor", required=True)
    s.add_argument("--kind", default="correction", choices=["correction", "clarification", "update", "retraction"])
    s.set_defaults(fn=cmd_correct)

    s = sub.add_parser("build", help="build the static site into dist/")
    s.add_argument("--out"); s.add_argument("--include-pending", action="store_true"); s.add_argument("--banner")
    s.add_argument("--relative", action="store_true", help="relative links, for previews on a sub-path")
    s.set_defaults(fn=cmd_build)

    s = sub.add_parser("newsletter", help="build today's Better Brief (and --send to the provider)")
    s.add_argument("--send", action="store_true"); s.add_argument("--since", type=int, default=26)
    s.set_defaults(fn=cmd_newsletter)

    s = sub.add_parser("social", help="render carousel slides (and --video) for stories")
    s.add_argument("--slug"); s.add_argument("--top", type=int, default=3); s.add_argument("--video", action="store_true")
    s.add_argument("--out", default="dist/social"); s.set_defaults(fn=cmd_social)

    sub.add_parser("audit", help="verify the audit log hash chain").set_defaults(fn=cmd_audit)

    s = sub.add_parser("monitor", help="re-check sources of recent stories; flag changes")
    s.add_argument("--issue-file", help="write a Markdown summary here if anything is flagged")
    s.set_defaults(fn=cmd_monitor)

    sub.add_parser("trackers", help="refresh progress tracker data from Our World in Data").set_defaults(fn=cmd_trackers)
    sub.add_parser("doctor", help="go-live readiness check (placeholders, keys, feeds, legal sign-off)").set_defaults(fn=cmd_doctor)

    s = sub.add_parser("demo", help="offline end-to-end demo on fictional fixtures")
    s.add_argument("--root", help="scratch directory (default: a new temp dir)")
    s.add_argument("--banner", default="Preview with fictional sample stories. Nothing here is real news.")
    s.set_defaults(fn=cmd_demo)

    s = sub.add_parser("_approve_all"); s.add_argument("--editor", required=True); s.set_defaults(fn=cmd_approve_all)

    a = p.parse_args(argv)
    return a.fn(a) or 0


if __name__ == "__main__":
    sys.exit(main())
