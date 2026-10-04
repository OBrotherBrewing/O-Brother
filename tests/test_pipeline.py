import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def run_cli(args, env):
    return subprocess.run([sys.executable, "-m", "gnn", *args], cwd=ROOT, env=env, capture_output=True, text=True)


def test_offline_demo_end_to_end(tmp_path):
    root = tmp_path / "root"
    r = run_cli(["demo", "--root", str(root)], os.environ.copy())
    assert r.returncode == 0, r.stdout + r.stderr
    stories = list((root / "content" / "stories").rglob("*.md"))
    assert len(stories) == 3
    text = stories[0].read_text()
    assert "mode: reviewed" in text and "Sample Editor" in text
    dist = root / "dist"
    for rel in ("index.html", "feed.xml", "sitemap.xml", "news-sitemap.xml", "corrections/index.html",
                "ai-policy/index.html", "newsletter/index.html"):
        assert (dist / rel).exists(), rel
    story_pages = list((dist / "stories").glob("*/index.html"))
    assert len(story_pages) == 3
    html = story_pages[0].read_text()
    assert "Evidence and limitations" in html and "application/ld+json" in html
    assert list((dist / "evidence").glob("*.json"))
    assert list((root / "content" / "newsletter").glob("*.html"))
    env = {**os.environ, "GNN_ROOT": str(root)}
    audit = run_cli(["audit"], env)
    assert audit.returncode == 0 and "intact" in audit.stdout


def test_demo_evidence_has_verified_claims(tmp_path):
    root = tmp_path / "root"
    assert run_cli(["demo", "--root", str(root)], os.environ.copy()).returncode == 0
    for ev in (root / "data" / "evidence").glob("*.json"):
        data = json.loads(ev.read_text())
        assert data["claims"], ev
        assert all(c["status"] == "verified" for c in data["claims"])
        assert data["checks"]["numbers"]["status"] == "pass"
        assert data["checks"]["quotes"]["status"] == "pass"
        assert data["corroboration"] == "primary_confirmed"


def test_correction_is_dated_versioned_and_audited(tmp_path):
    root = tmp_path / "root"
    assert run_cli(["demo", "--root", str(root)], os.environ.copy()).returncode == 0
    env = {**os.environ, "GNN_ROOT": str(root)}
    slug = sorted((root / "dist" / "stories").iterdir())[0].name
    r = run_cli(["correct", slug, "--note", "We misstated a figure.", "--editor", "Sample Editor"], env)
    assert r.returncode == 0, r.stderr
    assert "version 2" in r.stdout
    assert list((root / "data" / "versions").rglob("v1.md"))
    assert run_cli(["audit"], env).returncode == 0
