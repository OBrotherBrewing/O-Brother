"""Tests for gnn.social: carousel, OG card and vertical video rendering."""
from __future__ import annotations

import json
import shutil
import subprocess
import unicodedata
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from gnn import social

BRAND = {
    "name": "Good News News",
    "tagline": "What got better today. Checked, sourced, and honest about the limits.",
    "newsletter_name": "The Better Brief",
    "url": "https://www.example.com/",
}

SAMPLE = {
    "slug": "test-wind-record",
    "title": "Test sample: wind supplied a record share of electricity",
    "dek": "Illustrative test copy: wind met 47% of demand in a month, up from 38%.",
    "section": "planet",
    "key_metric": {"label": "Wind share of electricity", "before": "38%", "after": "47%",
                   "period": "Illustrative period"},
    "why_it_matters": "Test copy. Two sentences on why it matters.",
    "limitations": ["Test copy: one windy month says little about the trend."],
    "sources": [
        {"publisher": "Sample Newswire", "url": "https://example.org/c", "role": "lead"},
        {"publisher": "Sample Grid Operator", "url": "https://example.org/a", "role": "primary"},
    ],
    "social": {
        "hook": "Test sample: wind supplied a record share of the grid’s electricity",
        "slides": [
            "Slide one of the test carousel.",
            "Slide two → with an arrow the fonts lack, €1.2bn and “curly quotes”.",
            "Slide three names Áine Ó Súilleabháin and Łukasz to check accents.",
        ],
        "caption": "Test caption.",
    },
}

LONG_TITLE = {
    "slug": "test-long-title",
    "title": ("Test sample with a deliberately very long headline: community energy co-operatives in "
              "Contae Dhún na nGall and Ó Súilleabháin's island scheme cut household bills by "
              "€310 a year while “Supercalifragilisticexpialidociousness-level” words still wrap"),
    "dek": "Illustrative test copy for the long-title layout.",
    "section": "science-health",
    "key_metric": {"label": "Average household electricity bill across the participating parishes",
                   "before": "€1,480 per household", "after": "€1,170 per household",
                   "period": "Illustrative: winter 2025 to winter 2026, before and after the scheme"},
    "why_it_matters": "Test copy.",
    "limitations": ["Test copy: " + "a very long caveat that keeps going " * 12],
    "sources": [{"publisher": f"Publisher number {i} with a fairly long organisation name",
                 "url": f"https://example.org/{i}", "role": role}
                for i, role in enumerate(["corroborating", "primary", "lead", "corroborating", "primary"])],
    "social": {
        "slides": [("Test slide %d. " % i) + "Words that fill a slide to the limit. " * 5 for i in range(5)],
    },
}

NO_SOCIAL = {
    "slug": "test-no-social",
    "title": "Test sample without social copy or a key metric",
    "dek": "Illustrative dek used as the first derived slide.",
    "section": "business-work",
    "why_it_matters": "First derived sentence for the test. Second derived sentence for the test.",
    "limitations": [],
    "sources": [],
}


def _sizes(paths):
    out = []
    for p in paths:
        with Image.open(p) as im:
            out.append(im.size)
    return out


def test_carousel_sample(tmp_path: Path):
    paths = social.render_carousel(SAMPLE, BRAND, tmp_path)
    assert len(paths) == 1 + len(SAMPLE["social"]["slides"]) + 2  # cover + slides + caveat + sources
    assert [p.name for p in paths] == [f"test-wind-record-{i:02d}.png" for i in range(1, len(paths) + 1)]
    assert all(p.exists() for p in paths)
    assert set(_sizes(paths)) == {(1080, 1350)}


def test_carousel_long_title(tmp_path: Path):
    paths = social.render_carousel(LONG_TITLE, BRAND, tmp_path, section_name="Science & Health")
    # social.slides is capped at five
    assert len(paths) == 1 + 5 + 2
    assert set(_sizes(paths)) == {(1080, 1350)}


def test_carousel_derives_slides_and_removes_stale_files(tmp_path: Path):
    (tmp_path / "test-no-social-09.png").write_bytes(b"old")
    paths = social.render_carousel(NO_SOCIAL, BRAND, tmp_path)
    derived = social.story_slide_texts(NO_SOCIAL)
    assert derived[0] == NO_SOCIAL["dek"]
    assert len(paths) == 1 + len(derived) + 2
    assert not (tmp_path / "test-no-social-09.png").exists()
    assert set(_sizes(paths)) == {(1080, 1350)}


def test_og_image(tmp_path: Path):
    for story in (SAMPLE, LONG_TITLE, NO_SOCIAL):
        out = social.render_og_image(story, BRAND, tmp_path / f"{story['slug']}-og.png")
        with Image.open(out) as im:
            assert im.size == (1200, 630)


def test_fit_text_never_overflows():
    draw = ImageDraw.Draw(Image.new("RGB", (10, 10)))
    text = "Pneumonoultramicroscopicsilicovolcanoconiosis appears in a sentence " * 6
    for box_w, box_h in ((904, 600), (300, 200), (200, 60)):
        font, lines = social.fit_text(draw, text, social.SERIF_SEMIBOLD, box_w, box_h, 88, 40, 1.1)
        assert lines
        for line in lines:
            assert draw.textbbox((0, 0), line, font=font, anchor="ls")[2] <= box_w
        assert social.text_block_height(font, len(lines), 1.1) <= box_h


def test_fit_text_respects_max_lines_and_shrinks():
    draw = ImageDraw.Draw(Image.new("RGB", (10, 10)))
    short_font, short = social.fit_text(draw, "Short", social.SERIF_SEMIBOLD, 904, 600, 88, 40)
    long_font, long = social.fit_text(draw, LONG_TITLE["title"], social.SERIF_SEMIBOLD, 904, 600, 88, 40,
                                      max_lines=6)
    assert short_font.size == 88 and short == ["Short"]
    assert long_font.size < 88 and len(long) <= 6


def test_clean_text_unicode():
    text = social.clean_text('"Ó Súilleabháin" saw 38% → 47%, €5 and Łódź ✓', social.SERIF_SEMIBOLD)
    # Straight quotes become curly; "Ó" is bound to the surname with a no-break space; → is not
    # in the fonts so it reads as "to"; Ł has no accent to keep; ź keeps its acute as a combining
    # mark; the check mark is dropped.
    assert unicodedata.normalize("NFC", text) == "“Ó\u00a0Súilleabháin” saw 38% to 47%, €5 and Lódź"
    assert all(social.has_glyph(social.SERIF_SEMIBOLD, ch) for ch in text if ch != " ")


def test_section_display_name():
    assert social.section_display_name("science-health") == "Science & Health"
    assert social.section_display_name("business-work") == "Business & Work"
    assert social.section_display_name("top-progress") == "Top Progress"
    assert social.section_display_name("planet", "Our Planet") == "Our Planet"


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
def test_story_video(tmp_path: Path):
    out = social.render_story_video(None, SAMPLE, BRAND, tmp_path / "story.mp4")
    assert out.exists()
    assert out.stat().st_size > 10 * 1024
    if shutil.which("ffprobe") is None:
        pytest.skip("ffprobe not installed")
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
         "stream=width,height,codec_name,pix_fmt:format=duration", "-of", "json", str(out)],
        check=True, capture_output=True, text=True)
    info = json.loads(probe.stdout)
    stream = info["streams"][0]
    assert f"{stream['width']}x{stream['height']}" == "1080x1920"
    assert stream["codec_name"] == "h264" and stream["pix_fmt"] == "yuv420p"
    assert float(info["format"]["duration"]) < 30
    audio = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries",
                            "stream=index", "-of", "csv=p=0", str(out)], capture_output=True, text=True)
    assert audio.stdout.strip() == ""


def test_video_without_ffmpeg_raises(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(social.shutil, "which", lambda name: None)
    monkeypatch.setattr(social, "FFMPEG_FALLBACK", tmp_path / "no-ffmpeg-here")
    with pytest.raises(RuntimeError, match="ffmpeg"):
        social.render_story_video(None, SAMPLE, BRAND, tmp_path / "x.mp4")
