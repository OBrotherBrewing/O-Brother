"""Social media assets for a story: Instagram carousel, vertical video and link-preview card.

Everything is drawn from the story's front matter (see ``docs/STORY-FORMAT.md``) with Pillow,
using the OFL fonts in ``assets/fonts-ttf/``. The vertical video is assembled with ffmpeg.

* :func:`render_carousel`    1080x1350 PNG slides (cover, story points, the caveat, sources).
* :func:`render_story_video` 1080x1920 H.264 MP4 for Reels / Shorts / TikTok, same layouts
  re-flowed for the platform safe area (never letterboxed).
* :func:`render_og_image`    1200x630 PNG Open Graph / link-preview card.
* :func:`fit_text`           the wrapping and auto-shrinking helper behind all of them.

Text is wrapped by measuring real pixel widths (``ImageDraw.textbbox``), shrinks until it fits
its box and is truncated with an ellipsis as a last resort, so it never overflows. Characters
the fonts cannot draw are substituted (``→`` becomes "to", ``Ł`` becomes "L") or dropped
(emoji), so no "tofu" boxes reach a published image.

Run ``python -m gnn.social`` to render a layout sample into ``.cache/social-demo/``.
"""
from __future__ import annotations

import math
import re
import shutil
import subprocess
import tempfile
import unicodedata
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable, Literal, Sequence
from urllib.parse import urlparse

from PIL import Image, ImageColor, ImageDraw, ImageFont

from gnn import ROOT

__all__ = [
    "NAVY", "PAPER", "MARIGOLD", "AMBER_TEXT", "TEAL", "MUTED_ON_NAVY",
    "fit_text", "wrap_text", "clean_text", "text_block_height", "draw_text_block",
    "section_display_name", "story_slide_texts",
    "render_carousel", "render_story_video", "render_og_image",
]

# --------------------------------------------------------------------------------------------
# Brand constants
# --------------------------------------------------------------------------------------------

NAVY = "#13233A"           # card backgrounds
PAPER = "#F5F6F1"          # text on navy, light backgrounds
MARIGOLD = "#F0A202"       # accent marks, delta bars, rules (never small text on light)
AMBER_TEXT = "#9A5B00"     # accent text on light backgrounds
TEAL = "#2F6F73"           # evidence badges
MUTED_ON_NAVY = "#AEB8C6"  # secondary text on navy

FONT_DIR = ROOT / "assets" / "fonts-ttf"
SERIF_SEMIBOLD = FONT_DIR / "newsreader-latin-600-normal.ttf"   # headlines
SERIF_MEDIUM = FONT_DIR / "newsreader-latin-500-normal.ttf"     # slide text
SERIF_ITALIC = FONT_DIR / "newsreader-latin-400-italic.ttf"     # the caveat
SANS_MEDIUM = FONT_DIR / "instrument-sans-latin-500-normal.ttf"
SANS_SEMIBOLD = FONT_DIR / "instrument-sans-latin-600-normal.ttf"
MONO = FONT_DIR / "jetbrains-mono-latin-500-normal.ttf"         # labels and data

CAROUSEL_SIZE = (1080, 1350)
VIDEO_SIZE = (1080, 1920)
OG_SIZE = (1200, 630)
SIDE_MARGIN = 88
VIDEO_SAFE_TOP = 220      # platform UI (progress bar, account name) covers the top...
VIDEO_SAFE_BOTTOM = 380   # ...and captions, buttons and the music ticker cover the bottom
VIDEO_SAFE_RIGHT = 120    # TikTok / Reels action rail
VIDEO_FPS = 30
MAX_VIDEO_SECONDS = 29.5
SLIDE_MAX_CHARS = 220
MAX_STORY_SLIDES = 5
MAX_SOURCES_SHOWN = 4
RULE_W, RULE_H = 112, 12  # the marigold rule under a slide label

PathLike = str | Path
WrapMode = Literal["greedy", "pretty", "balance"]

# --------------------------------------------------------------------------------------------
# Fonts and glyph coverage
# --------------------------------------------------------------------------------------------


@lru_cache(maxsize=512)
def _font(path: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(path, size)


def load_font(path: PathLike, size: int) -> ImageFont.FreeTypeFont:
    """Return a cached Pillow font for ``path`` at ``size`` pixels."""
    return _font(str(path), max(1, int(size)))


@lru_cache(maxsize=None)
def _glyph_print(path: str, ch: str) -> tuple[float, bytes]:
    font = _font(path, 40)
    img = Image.new("L", (100, 100), 0)
    ImageDraw.Draw(img).text((20, 20), ch, font=font, fill=255)
    return font.getlength(ch), img.tobytes()


@lru_cache(maxsize=8192)
def has_glyph(font_path: PathLike, ch: str) -> bool:
    """True if the font can draw ``ch`` (it does not render as the .notdef box)."""
    path = str(font_path)
    return _glyph_print(path, ch) != _glyph_print(path, "\U000FFFFD")


# Replacements for characters the latin-subset fonts lack. Arrows read as words in running
# text; the delta block draws its own vector arrow.
_SUBSTITUTES = {
    "→": " to ", "⟶": " to ", "➔": " to ", "⇒": " to ", "↑": " up ", "↓": " down ",
    "≈": "~", "≥": ">=", "≤": "<=", "×": "x",
    "Ł": "L", "ł": "l", "Đ": "D", "đ": "d", "Ħ": "H", "ħ": "h", "Ŧ": "T", "ŧ": "t",
    "Ŀ": "L", "ŀ": "l", "ŉ": "’n",
    "‐": "-", "‑": "-", "‒": "–", "―": "—", "′": "’", "″": "”", "‹": "‘", "›": "’",
}
_KEEP_SPACES = {"\u00a0"}                     # no-break space binds words when wrapping
_TO_NBSP = {"\u202f", "\u2007"}               # narrow / figure no-break spaces


# Surname particles bound to the name that follows (Ó Súilleabháin, Ní Dhomhnaill, Mac Giolla).
_NAME_PREFIX = re.compile(r"\b(Ó|Ní|Nic|Mac|Mag|Mhic|Uí|Ua|Mc|De|Van|Von) +(?=[A-ZÁÉÍÓÚ])")


def _smarten(text: str) -> str:
    """Straight quotes to typographic quotes; three dots to an ellipsis."""
    text = text.replace("...", "…")
    text = re.sub(r"'(?=\d0s\b)", "’", text)                     # the '90s
    text = re.sub(r'(^|[\s(\[{—–/-])"', "\\1“", text)
    text = text.replace('"', "”")
    text = re.sub(r"(^|[\s(\[{—–/-])'(?=\w)", "\\1‘", text)
    return text.replace("'", "’")


def clean_text(text: Any, font_path: PathLike | None = None) -> str:
    """Normalise text for drawing.

    NFC-normalises, collapses whitespace (keeping newlines and no-break spaces), turns straight
    quotes into curly ones and, when ``font_path`` is given, replaces or drops characters the font
    has no glyph for, so nothing renders as a missing-glyph box.
    """
    s = unicodedata.normalize("NFC", "" if text is None else str(text))
    s = s.replace("\r\n", "\n").replace("\r", "\n").replace("\u2028", "\n").replace("\u2029", "\n")
    s = _smarten(s)
    s = _NAME_PREFIX.sub("\\1\u00a0", s)   # never strand "Ó" or "Mac" at the end of a line
    path = str(font_path) if font_path is not None else None
    out: list[str] = []
    for ch in s:
        if ch in ("\n", " "):
            out.append(ch)
            continue
        if ch in _TO_NBSP:
            ch = "\u00a0"
        if ch in _KEEP_SPACES:
            out.append(ch)
            continue
        cat = unicodedata.category(ch)
        if cat.startswith("Z") or ch == "\t":
            out.append(" ")
            continue
        if cat in ("Cc", "Cf", "Co", "Cs", "Cn"):
            continue
        if path is None or has_glyph(path, ch):
            out.append(ch)
            continue
        sub = _SUBSTITUTES.get(ch)
        if sub is None:
            nfd = unicodedata.normalize("NFD", ch)
            if len(nfd) > 1 and has_glyph(path, nfd[0]):
                # Base letter plus whichever combining accents the font has (ź keeps its acute;
                # ř loses a caron the font lacks).
                sub = nfd[0] + "".join(m for m in nfd[1:] if has_glyph(path, m))
            else:
                base = "".join(c for c in unicodedata.normalize("NFKD", ch) if not unicodedata.combining(c))
                ok = base and base != ch and all(c == " " or has_glyph(path, c) for c in base)
                sub = base if ok else ""  # emoji and other symbols are dropped
        out.append(sub)
    s = "".join(out)
    s = re.sub(r" {2,}", " ", s)
    s = re.sub(r" *\n *", "\n", s)
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s.strip()


# --------------------------------------------------------------------------------------------
# Measuring, wrapping and fitting
# --------------------------------------------------------------------------------------------


@lru_cache(maxsize=1024)
def _vmetrics(path: str, size: int) -> tuple[int, int]:
    """(cap height, descent) in pixels: the block model used for vertical layout."""
    font = _font(path, size)
    cap = -font.getbbox("H", anchor="ls")[1]
    return cap, font.getmetrics()[1]


def _pitch(font: ImageFont.FreeTypeFont, line_spacing: float) -> int:
    return round(font.size * line_spacing)


def text_block_height(font: ImageFont.FreeTypeFont, n_lines: int, line_spacing: float = 1.15) -> int:
    """Height of ``n_lines`` lines: cap height of the first line to the descent of the last."""
    if n_lines <= 0:
        return 0
    cap, desc = _vmetrics(font.path, font.size)
    return cap + (n_lines - 1) * _pitch(font, line_spacing) + desc


# Instrument Sans has a tight word space (0.2 em); open it up a little so words never run together.
_WORD_SPACING = {str(SANS_MEDIUM): 0.07, str(SANS_SEMIBOLD): 0.06}


def _word_px(font: ImageFont.FreeTypeFont) -> float:
    return _WORD_SPACING.get(str(font.path), 0.0) * font.size


def _measure(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont, tracking_px: float = 0.0) -> float:
    """Pixel width of a single line: right edge of its ``textbbox`` (or summed advances when
    letter-spaced), plus any extra word spacing."""
    if not text:
        return 0.0
    extra = _word_px(font) * text.count(" ")
    if tracking_px:
        return sum(font.getlength(c) for c in text) + tracking_px * (len(text) - 1) + extra
    return float(draw.textbbox((0, 0), text, font=font, anchor="ls")[2]) + extra


def _draw_line(draw: ImageDraw.ImageDraw, x: float, baseline: float, text: str,
               font: ImageFont.FreeTypeFont, fill: str, tracking_px: float = 0.0) -> None:
    """Draw one line on ``baseline`` honouring letter spacing and the font's word spacing."""
    extra = _word_px(font)
    if tracking_px:
        for ch in text:
            draw.text((x, baseline), ch, font=font, fill=fill, anchor="ls")
            x += font.getlength(ch) + tracking_px + (extra if ch == " " else 0.0)
    elif extra and " " in text:
        space = font.getlength(" ") + extra
        for word in text.split(" "):
            if word:
                draw.text((x, baseline), word, font=font, fill=fill, anchor="ls")
                x += font.getlength(word)
            x += space
    else:
        draw.text((x, baseline), text, font=font, fill=fill, anchor="ls")


Measure = Callable[[str], float]


def _break_word(word: str, measure: Measure, width: float) -> list[str]:
    """Split a word wider than ``width``: after an existing hyphen or slash if possible,
    otherwise at the widest character position that fits with an added hyphen."""
    pieces: list[str] = []
    rest = word
    while len(rest) > 1 and measure(rest) > width:
        cut = 0
        for i in range(len(rest) - 1, 0, -1):
            if rest[i - 1] in "-/–—" and measure(rest[:i]) <= width:
                cut = i
                break
        if cut:
            pieces.append(rest[:cut])
        else:
            lo, hi = 1, len(rest) - 1
            while lo < hi:
                mid = (lo + hi + 1) // 2
                if measure(rest[:mid] + "-") <= width:
                    lo = mid
                else:
                    hi = mid - 1
            cut = lo
            pieces.append(rest[:cut] + "-")
        rest = rest[cut:]
    pieces.append(rest)
    return pieces


def _greedy(text: str, measure: Measure, width: float) -> list[str]:
    lines: list[str] = []
    for para in text.split("\n"):
        cur = ""
        for word in (w for w in para.split(" ") if w):
            trial = f"{cur} {word}" if cur else word
            if measure(trial) <= width:
                cur = trial
                continue
            if cur:
                lines.append(cur)
            if measure(word) <= width:
                cur = word
            else:
                pieces = _break_word(word, measure, width)
                lines.extend(pieces[:-1])
                cur = pieces[-1]
        if cur:
            lines.append(cur)
    return lines


def _balance(text: str, measure: Measure, width: float, lines: list[str]) -> list[str]:
    """Narrowest wrap with the same number of lines (like CSS ``text-wrap: balance``)."""
    n = len(lines)
    if n < 2:
        return lines
    widest_word = max(measure(w) for w in re.split(r"[ \n]+", text) if w)
    if widest_word > width:
        return lines
    lo, hi = int(math.ceil(widest_word)), int(width)
    while lo < hi:
        mid = (lo + hi) // 2
        if len(_greedy(text, measure, mid)) <= n:
            hi = mid
        else:
            lo = mid + 1
    return _greedy(text, measure, lo)


def _avoid_orphan(text: str, measure: Measure, width: float, lines: list[str]) -> list[str]:
    """If the last line is a single word, narrow the measure (up to 25%) to give it company."""
    n = len(lines)
    if n < 2 or " " in lines[-1]:
        return lines
    step = max(2.0, width * 0.02)
    w = width - step
    while w >= width * 0.75:
        cand = _greedy(text, measure, w)
        if len(cand) > n:
            break
        if " " in cand[-1]:
            return cand
        w -= step
    return lines


def _even_rag(text: str, measure: Measure, width: float) -> list[str] | None:
    """Minimum-raggedness wrap: minimises the squared white space at the end of every line but
    the last, and heavily penalises a single-word last line. None if a word must be broken."""
    out: list[str] = []
    for para in text.split("\n"):
        words = [w for w in para.split(" ") if w]
        n = len(words)
        if not n:
            continue
        best = [math.inf] * (n + 1)
        nxt = [n] * (n + 1)
        best[n] = 0.0
        for i in range(n - 1, -1, -1):
            line = ""
            for j in range(i + 1, n + 1):
                line = f"{line} {words[j - 1]}" if line else words[j - 1]
                w = measure(line)
                if w > width:
                    if j == i + 1:
                        return None
                    break
                if j == n:
                    cost = (width * 0.6) ** 2 if (j - i == 1 and n > 1) else 0.0
                else:
                    cost = (width - w) ** 2
                if cost + best[j] < best[i]:
                    best[i], nxt[i] = cost + best[j], j
        i = 0
        while i < n:
            out.append(" ".join(words[i:nxt[i]]))
            i = nxt[i]
    return out


def wrap_text(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont, width: float,
              *, mode: WrapMode = "pretty", tracking: float = 0.0) -> list[str]:
    """Wrap already-cleaned ``text`` into lines no wider than ``width`` pixels.

    ``mode`` is "greedy" (fill each line), "pretty" (even rag, no single-word last line; for
    body text) or "balance" (lines of similar length; for headlines). ``tracking`` is extra
    letter spacing in em. Words wider than the box are broken, after a hyphen or slash if
    possible. "pretty" and "balance" never use more lines than "greedy".
    """
    tracking_px = tracking * font.size
    widths: dict[str, float] = {}

    def measure(s: str) -> float:
        w = widths.get(s)
        if w is None:
            w = widths[s] = _measure(draw, s, font, tracking_px)
        return w

    lines = _greedy(text, measure, width)
    if mode == "pretty" and len(lines) > 1:
        even = _even_rag(text, measure, width)
        if even is not None and len(even) <= len(lines):
            return even
    if mode == "balance":
        lines = _balance(text, measure, width, lines)
    if mode in ("pretty", "balance"):
        lines = _avoid_orphan(text, measure, width, lines)
    return lines


def _ellipsize(line: str, measure: Measure, width: float, force: bool = False) -> str:
    """Shorten ``line`` to fit ``width`` with a trailing ellipsis (always added if ``force``)."""
    if not force and measure(line) <= width:
        return line
    ell = "…"
    words = line.split(" ")
    while len(words) > 1 and measure(" ".join(words) + ell) > width:
        words.pop()
    s = " ".join(words).rstrip(" ,;:.–—-")
    while s and measure(s + ell) > width:
        s = s[:-1].rstrip(" ,;:.–—-")
    return s + ell


def fit_text(draw: ImageDraw.ImageDraw, text: str, font_path: PathLike, box_w: float, box_h: float,
             max_size: int, min_size: int, line_spacing: float = 1.15, *,
             max_lines: int | None = None, wrap: WrapMode = "pretty",
             tracking: float = 0.0) -> tuple[ImageFont.FreeTypeFont, list[str]]:
    """Find the largest font size at which ``text`` fits a ``box_w`` x ``box_h`` box.

    Tries sizes from ``max_size`` down to ``min_size``; at each size the text is wrapped by
    measured pixel width and its block height (see :func:`text_block_height`) compared with
    ``box_h``. If it still does not fit at ``min_size`` the text is cut to the lines that fit and
    the last line ends with an ellipsis. Returned lines are never wider than ``box_w``.

    The text is cleaned for the font first (curly quotes, unsupported glyphs substituted), so
    draw the returned lines, not the input string. ``max_lines`` caps the line count, ``wrap``
    picks the wrapping style and ``tracking`` adds letter spacing in em.

    Returns ``(font, lines)``.
    """
    text = clean_text(text, font_path)
    if not text:
        return load_font(font_path, min_size), []
    max_size = max(int(max_size), int(min_size))
    tracking_px = 0.0

    def fits(font: ImageFont.FreeTypeFont, lines: list[str]) -> bool:
        if max_lines is not None and len(lines) > max_lines:
            return False
        return text_block_height(font, len(lines), line_spacing) <= box_h

    def attempt(size: int) -> tuple[ImageFont.FreeTypeFont, list[str]] | None:
        font = load_font(font_path, size)
        lines = wrap_text(draw, text, font, box_w, mode="greedy", tracking=tracking)
        return (font, lines) if fits(font, lines) else None

    # Binary search for the largest size that fits (fit is monotonic in size, near enough).
    found = attempt(max_size)
    if found is None and max_size > min_size:
        low = attempt(int(min_size))
        if low is not None:
            lo, hi = int(min_size), max_size  # lo fits, hi does not
            found = low
            while hi - lo > 1:
                mid = (lo + hi) // 2
                trial = attempt(mid)
                if trial is not None:
                    lo, found = mid, trial
                else:
                    hi = mid
    if found is not None:
        font, lines = found
        if wrap != "greedy":
            refined = wrap_text(draw, text, font, box_w, mode=wrap, tracking=tracking)
            if fits(font, refined):
                lines = refined
        return font, lines

    # Does not fit even at min_size: keep what fits and end with an ellipsis.
    size = int(min_size)
    while True:
        font = load_font(font_path, size)
        cap, desc = _vmetrics(font.path, font.size)
        if cap + desc <= box_h or size <= 6:
            break
        size -= 1
    tracking_px = tracking * font.size
    lines = wrap_text(draw, text, font, box_w, mode="greedy", tracking=tracking)
    room = 1 + max(0, int((box_h - cap - desc) // max(1, _pitch(font, line_spacing))))
    if max_lines is not None:
        room = min(room, max_lines)
    room = max(1, room)
    if len(lines) > room:
        lines = lines[:room]

        def measure(s: str) -> float:
            return _measure(draw, s, font, tracking_px)

        lines[-1] = _ellipsize(lines[-1], measure, box_w, force=True)
    return font, lines


def draw_text_block(draw: ImageDraw.ImageDraw, x: float, y_top: float, lines: Sequence[str],
                    font: ImageFont.FreeTypeFont, fill: str, line_spacing: float = 1.15, *,
                    tracking: float = 0.0, align: Literal["left", "right", "center"] = "left",
                    width: float | None = None) -> int:
    """Draw ``lines`` with the cap height of the first line at ``y_top``; lines sit on a
    baseline grid of ``font.size * line_spacing``. Returns the y of the block's bottom."""
    cap, _ = _vmetrics(font.path, font.size)
    pitch = _pitch(font, line_spacing)
    tracking_px = tracking * font.size
    baseline = y_top + cap
    for line in lines:
        lx = x
        if align != "left" and width is not None:
            lw = _measure(draw, line, font, tracking_px)
            lx = x + (width - lw) if align == "right" else x + (width - lw) / 2
        _draw_line(draw, lx, baseline, line, font, fill, tracking_px)
        baseline += pitch
    return int(round(y_top + text_block_height(font, len(lines), line_spacing)))


# --------------------------------------------------------------------------------------------
# Drawing primitives
# --------------------------------------------------------------------------------------------


class _Canvas:
    """An RGB image plus its draw handle, with anti-aliased shape helpers."""

    def __init__(self, size: tuple[int, int], background: str):
        self.img = Image.new("RGB", size, background)
        self.draw = ImageDraw.Draw(self.img)
        self.w, self.h = size

    def rect(self, x0: float, y0: float, x1: float, y1: float, fill: str) -> None:
        """Axis-aligned filled rectangle, end coordinates exclusive."""
        x0, y0, x1, y1 = (int(round(v)) for v in (x0, y0, x1, y1))
        if x1 > x0 and y1 > y0:
            self.draw.rectangle((x0, y0, x1 - 1, y1 - 1), fill=fill)

    def smooth(self, box: tuple[float, float, float, float], fill: str,
               paint: Callable[[ImageDraw.ImageDraw, Callable[[float, float], tuple[float, float]], int], None],
               ss: int = 4) -> None:
        """Paint a shape into a 4x supersampled mask and composite it, for smooth edges."""
        x0, y0 = math.floor(box[0]), math.floor(box[1])
        x1, y1 = math.ceil(box[2]), math.ceil(box[3])
        if x1 <= x0 or y1 <= y0:
            return
        mask = Image.new("L", ((x1 - x0) * ss, (y1 - y0) * ss), 0)

        def p(x: float, y: float) -> tuple[float, float]:
            return (x - x0) * ss, (y - y0) * ss

        paint(ImageDraw.Draw(mask), p, ss)
        self.img.paste(ImageColor.getrgb(fill), (x0, y0, x1, y1), mask.reduce(ss))

    def pill(self, x0: float, y0: float, x1: float, y1: float, fill: str) -> None:
        r = (y1 - y0) / 2

        def paint(md: ImageDraw.ImageDraw, p: Callable, ss: int) -> None:
            md.rounded_rectangle((*p(x0, y0), *p(x1, y1)), radius=r * ss, fill=255)

        self.smooth((x0, y0, x1, y1), fill, paint)

    def arrow(self, x0: float, x1: float, cy: float, thickness: float, head: float, fill: str) -> None:
        """A right-pointing arrow with an open chevron head and round caps."""
        pad = thickness + 2

        def paint(md: ImageDraw.ImageDraw, p: Callable, ss: int) -> None:
            t = max(1, round(thickness * ss))
            r = t / 2
            tip = x1 - thickness * 0.7
            pts = [(x0, cy), (tip, cy), (tip - head, cy - head), (tip - head, cy + head)]
            md.line((p(x0, cy), p(tip, cy)), fill=255, width=t)
            md.line((p(tip - head, cy - head), p(tip, cy), p(tip - head, cy + head)), fill=255, width=t, joint="curve")
            for px, py in (p(*pt) for pt in pts):
                md.ellipse((px - r, py - r, px + r, py + r), fill=255)

        self.smooth((x0 - pad, cy - head - pad, x1 + pad, cy + head + pad), fill, paint)


def _tracked_width(text: str, font: ImageFont.FreeTypeFont, tracking: float) -> float:
    if not text:
        return 0.0
    return (sum(font.getlength(c) for c in text) + tracking * font.size * (len(text) - 1)
            + _word_px(font) * text.count(" "))


def _label(c: _Canvas, x: float, y_top: float, text: str, fill: str, size: int, max_w: float,
           tracking: float = 0.14) -> int:
    """One line of letter-spaced uppercase mono; shrinks (then ellipsizes) to fit ``max_w``.
    Returns the baseline y."""
    text = clean_text(str(text).upper(), MONO)
    cap, _ = _vmetrics(str(MONO), size)
    font = load_font(MONO, size)
    for s in range(size, max(10, size - 8) - 1, -1):
        font = load_font(MONO, s)
        if _tracked_width(text, font, tracking) <= max_w:
            break
    else:
        text = _ellipsize(text, lambda t: _tracked_width(t, font, tracking), max_w)
    baseline = int(round(y_top + cap))
    _draw_line(c.draw, x, baseline, text, font, fill, tracking * font.size)
    return baseline


def _single_line(c: _Canvas, text: str, font_path: PathLike, max_w: float, max_size: int,
                 min_size: int) -> tuple[ImageFont.FreeTypeFont, str]:
    """Largest size at which ``text`` fits on one line; ellipsized at ``min_size`` if needed."""
    font, lines = fit_text(c.draw, text, font_path, max_w, 10_000, max_size, min_size, max_lines=1, wrap="greedy")
    return font, (lines[0] if lines else "")


# --------------------------------------------------------------------------------------------
# Story content
# --------------------------------------------------------------------------------------------

_SECTION_AMPERSAND = {"science-health", "business-work"}
_ROLE_ORDER = {"primary": 0, "corroborating": 1, "lead": 2}


def section_display_name(slug: str | None, section_name: str | None = None) -> str:
    """``section_name`` if given, else the slug title-cased ("science-health" ->
    "Science & Health", "top-progress" -> "Top Progress")."""
    if section_name:
        return section_name
    slug = (slug or "").strip()
    if not slug:
        return "News"
    sep = " & " if slug in _SECTION_AMPERSAND else " "
    return sep.join(part.capitalize() for part in slug.split("-") if part)


def _sentences(text: str) -> list[str]:
    text = re.sub(r"\s+", " ", text or "").strip()
    if not text:
        return []
    return [s for s in re.split(r"(?<=[.!?…])[”’\"']?\s+(?=[A-Z0-9“‘\"'ÁÉÍÓÚ])", text) if s]


def _chunks(text: str, limit: int = SLIDE_MAX_CHARS) -> list[str]:
    out: list[str] = []
    cur = ""
    for sentence in _sentences(text):
        trial = f"{cur} {sentence}".strip()
        if cur and len(trial) > limit:
            out.append(cur)
            cur = sentence
        else:
            cur = trial
    if cur:
        out.append(cur)
    return out


def story_slide_texts(story: dict) -> list[str]:
    """The middle-slide texts: ``social.slides`` (at most five), or, when missing, the dek
    followed by ``why_it_matters`` split into slide-sized chunks."""
    social = story.get("social") or {}
    slides = [str(s).strip() for s in (social.get("slides") or []) if s and str(s).strip()]
    if not slides:
        if story.get("dek"):
            slides.append(str(story["dek"]).strip())
        slides.extend(_chunks(str(story.get("why_it_matters") or ""))[:MAX_STORY_SLIDES - 1])
        if not slides and story.get("title"):
            slides.append(str(story["title"]).strip())
    return slides[:MAX_STORY_SLIDES]


def _domain(url: str | None) -> str:
    if not url:
        return ""
    netloc = urlparse(url if "//" in url else f"//{url}").netloc or url
    return re.sub(r"^www\.", "", netloc).rstrip("/")


@dataclass
class _Content:
    section_label: str
    title: str
    hook: str
    dek: str
    metric: dict | None
    points: list[str]
    caveat: str
    sources: list[tuple[str, str]]  # (publisher, role)
    brand_name: str
    tagline: str
    newsletter: str
    domain: str


def _prepare(story: dict, brand: dict, section_name: str | None) -> _Content:
    social = story.get("social") or {}
    title = str(story.get("title") or "").strip()
    hook = str(social.get("hook") or "").strip() or title
    metric = story.get("key_metric") or None
    if metric and not str(metric.get("after") or "").strip():
        metric = None
    limitations = [str(x).strip() for x in (story.get("limitations") or []) if x and str(x).strip()]
    caveat = limitations[0] if limitations else (
        "Early days: we will update this story as fuller data comes in.")

    seen: set[str] = set()
    sources: list[tuple[str, str]] = []
    ranked = sorted(enumerate(story.get("sources") or []),
                    key=lambda p: (_ROLE_ORDER.get(str((p[1] or {}).get("role", "")).lower(), 3), p[0]))
    for _, src in ranked:
        src = src or {}
        name = str(src.get("publisher") or "").strip() or _domain(src.get("url"))
        if not name or name.casefold() in seen:
            continue
        seen.add(name.casefold())
        sources.append((name, str(src.get("role") or "").strip().lower()))
        if len(sources) == MAX_SOURCES_SHOWN:
            break

    section = section_display_name(story.get("section"), section_name)
    return _Content(
        section_label=f"{section} · What got better",
        title=title or hook,
        hook=hook,
        dek=str(story.get("dek") or "").strip(),
        metric=metric,
        points=story_slide_texts(story),
        caveat=caveat,
        sources=sources,
        brand_name=str(brand.get("name") or "").strip(),
        tagline=str(brand.get("tagline") or "").strip(),
        newsletter=str(brand.get("newsletter_name") or "").strip() or "our newsletter",
        domain=_domain(brand.get("url")),
    )


# --------------------------------------------------------------------------------------------
# Layout frames: the carousel and the vertical video share layouts, not spacing
# --------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class _Frame:
    width: int
    height: int
    top: int      # cap top of the first label
    bottom: int   # footer baseline
    left: int
    right: int

    @property
    def content_w(self) -> int:
        return self.width - self.left - self.right

    @property
    def x1(self) -> int:
        return self.width - self.right

    def g(self, px: float) -> int:
        """Scale a vertical gap to the frame (the video's safe area is a little taller)."""
        return int(round(px * (self.bottom - self.top) / 1174))


CAROUSEL_FRAME = _Frame(*CAROUSEL_SIZE, top=SIDE_MARGIN, bottom=CAROUSEL_SIZE[1] - SIDE_MARGIN,
                        left=SIDE_MARGIN, right=SIDE_MARGIN)
VIDEO_FRAME = _Frame(*VIDEO_SIZE, top=VIDEO_SAFE_TOP + 28, bottom=VIDEO_SIZE[1] - VIDEO_SAFE_BOTTOM - 28,
                     left=SIDE_MARGIN, right=VIDEO_SAFE_RIGHT)


def _footer(c: _Canvas, f: _Frame, ct: _Content, index: int, total: int, light: bool = False) -> int:
    """Brand name bottom-left, "n/N" bottom-right, on a shared baseline. Returns the footer's top y."""
    counter = f"{index}/{total}"
    mono = load_font(MONO, 24)
    counter_w = _tracked_width(counter, mono, 0.08)
    baseline = f.bottom
    if ct.brand_name:
        font, name = _single_line(c, ct.brand_name, SANS_SEMIBOLD, f.content_w - counter_w - 48, 30, 22)
        _draw_line(c.draw, f.left, baseline, name, font, NAVY if light else PAPER)
    _draw_line(c.draw, f.x1 - counter_w, baseline, counter, mono,
                  AMBER_TEXT if light else MUTED_ON_NAVY, 0.08 * mono.size)
    return baseline - _vmetrics(str(SANS_SEMIBOLD), 30)[0]


def _label_and_rule(c: _Canvas, f: _Frame, text: str, color: str, rule: bool = True) -> int:
    """Slide label at the top of the frame, optionally with the marigold rule; returns the y
    below them where content may start."""
    baseline = _label(c, f.left, f.top, text, color, 26, f.content_w)
    if not rule:
        return baseline
    rule_top = baseline + f.g(34)
    c.rect(f.left, rule_top, f.left + RULE_W, rule_top + RULE_H, MARIGOLD)
    return rule_top + RULE_H


# --------------------------------------------------------------------------------------------
# The delta block (key metric)
# --------------------------------------------------------------------------------------------

_NUM_RE = re.compile(r"^(?P<pre>[^\d\-−.]*)(?P<num>\d[\d,]*(?:\.\d+)?|\.\d+)(?P<post>.*)$")


def _quantity(value: str) -> tuple[str, float, str] | None:
    m = _NUM_RE.match(value.strip())
    if not m:
        return None
    try:
        return m["pre"].strip(), float(m["num"].replace(",", "")), m["post"].strip()
    except ValueError:
        return None


def _bar_scale(before: str, after: str) -> tuple[float, float] | None:
    """Bar lengths (0-1) for comparable, non-negative quantities, else None. Percentages are
    drawn against 100%; other units against the larger value."""
    a, b = _quantity(before), _quantity(after)
    if not a or not b or (a[0], a[2]) != (b[0], b[2]):
        return None
    top = 100.0 if a[2].startswith("%") and max(a[1], b[1]) <= 100 else max(a[1], b[1])
    if top <= 0:
        return None
    return a[1] / top, b[1] / top


@dataclass
class _DeltaStyle:
    label_size: int
    value_max: int
    value_min: int
    period_size: int
    gap_label: int
    gap_period: int
    bars: bool
    label_lines: int = 2
    period_lines: int = 2


@dataclass
class _Values:
    font: ImageFont.FreeTypeFont
    before: str
    after: str
    arrow_w: int
    gap: int
    stacked: bool  # before on one line, "→ after" on the next
    unit: str = ""  # shared unit pulled out of both figures, set small beneath them


def _split_unit(before: str, after: str) -> tuple[str, str, str] | None:
    """("€1,480", "€1,170", "per household") for "€1,480 per household" -> "€1,170 per household"."""
    mb, ma = _NUM_RE.match(before.strip()), _NUM_RE.match(after.strip())
    if not (mb and ma):
        return None
    unit = mb["post"].strip()
    if not unit or unit != ma["post"].strip() or not re.search(r"[^\W\d_]", unit):
        return None
    return (mb["pre"] + mb["num"]).strip(), (ma["pre"] + ma["num"]).strip(), unit


def _fit_values(c: _Canvas, before: str, after: str, width: float, max_size: int, min_size: int) -> _Values:
    """Lay out "BEFORE → AFTER" at the largest size that fits ``width``: on one row when that
    keeps the figures big, otherwise stacked over two lines (for long values such as
    "€1,480 per household"). Ellipsized only if even the stacked form cannot fit."""
    before = clean_text(before, SANS_SEMIBOLD)
    after = clean_text(after, SANS_SEMIBOLD)

    def geometry(size: int) -> tuple[ImageFont.FreeTypeFont, int, int, float, float, float]:
        font = load_font(SANS_SEMIBOLD, size)
        arrow_w, gap = round(size * 0.58), round(size * 0.2)
        wb = _measure(c.draw, before, font) if before else 0.0
        wa = _measure(c.draw, after, font)
        return font, arrow_w, gap, wb, wa, arrow_w + gap

    def best(stacked: bool) -> int | None:
        for size in range(max_size, min_size - 1, -2):
            _, arrow_w, gap, wb, wa, lead = geometry(size)
            if not before:
                need = wa
            elif stacked:
                need = max(wb, lead + wa)
            else:
                need = wb + 2 * gap + arrow_w + wa
            if need <= width:
                return size
        return None

    row = best(False)
    stack = best(True) if before else None
    if row is not None and (stack is None or row >= 0.8 * stack):
        font, arrow_w, gap, *_ = geometry(row)
        return _Values(font, before, after, arrow_w, gap, False)
    if row is None and stack is None:
        split = _split_unit(before, after)
        if split:
            v = _fit_values(c, split[0], split[1], width, max_size, min_size)
            v.unit = clean_text(split[2], SANS_MEDIUM)
            return v
    size = stack if stack is not None else min_size
    font, arrow_w, gap, *_ = geometry(size)

    def measure(t: str) -> float:
        return _measure(c.draw, t, font)

    return _Values(font, _ellipsize(before, measure, width), _ellipsize(after, measure, width - arrow_w - gap),
                   arrow_w, gap, bool(before))


def _delta_block(c: _Canvas, x: float, y_top: float, width: float, metric: dict, style: _DeltaStyle,
                 *, render: bool = True) -> int:
    """Label (mono), BEFORE → AFTER (after in marigold), optional delta bars, period.
    Returns the block height; with ``render=False`` only measures."""
    d = c.draw
    y = y_top
    label = str(metric.get("label") or "").strip()
    if label:
        lf, llines = fit_text(d, label.upper(), MONO, width, 10_000, style.label_size, style.label_size - 4,
                              1.4, max_lines=style.label_lines, tracking=0.1)
        if render:
            draw_text_block(d, x, y, llines, lf, MUTED_ON_NAVY, 1.4, tracking=0.1)
        y += text_block_height(lf, len(llines), 1.4) + style.gap_label

    before = str(metric.get("before") or "").strip()
    after = str(metric.get("after") or "").strip()
    v = _fit_values(c, before, after, width, style.value_max, style.value_min)
    cap, _ = _vmetrics(v.font.path, v.font.size)
    baseline = y + cap
    stroke = max(2.0, v.font.size * 0.055)
    vx = x
    if v.before:
        if render:
            _draw_line(d, vx, baseline, v.before, v.font, PAPER)
        if v.stacked:
            baseline += round(v.font.size * 1.15)
        else:
            vx += _measure(d, v.before, v.font) + v.gap
        if render:
            c.arrow(vx, vx + v.arrow_w, baseline - cap * 0.5, stroke, v.font.size * 0.17, MUTED_ON_NAVY)
        vx += v.arrow_w + v.gap
    if render:
        _draw_line(d, vx, baseline, v.after, v.font, MARIGOLD)
    y = baseline + round(v.font.size * 0.06)
    if v.unit:
        uf, unit = _single_line(c, v.unit, SANS_MEDIUM, width, style.period_size + 4, style.period_size)
        y += round(style.gap_period * 0.75) + _vmetrics(uf.path, uf.size)[0]
        if render:
            _draw_line(d, x, y, unit, uf, PAPER)
        y += _vmetrics(uf.path, uf.size)[1]

    scale = _bar_scale(before, after) if (style.bars and v.before) else None
    if scale:
        bar_h, bar_gap = 10, 8
        y += round(style.gap_period * 1.1)
        if render:
            for i, (frac, colour) in enumerate(((scale[0], MUTED_ON_NAVY), (scale[1], MARIGOLD))):
                by = y + i * (bar_h + bar_gap)
                c.rect(x, by, x + max(bar_h, frac * width), by + bar_h, colour)
        y += 2 * bar_h + bar_gap

    period = str(metric.get("period") or "").strip()
    if period:
        y += style.gap_period
        pf, plines = fit_text(d, period, SANS_MEDIUM, width, 10_000, style.period_size, style.period_size - 4,
                              1.3, max_lines=style.period_lines)
        if render:
            draw_text_block(d, x, y, plines, pf, MUTED_ON_NAVY, 1.3)
        y += text_block_height(pf, len(plines), 1.3)
    return int(round(y - y_top))


# --------------------------------------------------------------------------------------------
# Slides
# --------------------------------------------------------------------------------------------


def _slide_cover(f: _Frame, ct: _Content, index: int, total: int) -> Image.Image:
    c = _Canvas((f.width, f.height), NAVY)
    x, w = f.left, f.content_w
    head_top = _label_and_rule(c, f, ct.section_label, MUTED_ON_NAVY) + f.g(64)
    zone_bottom = _footer(c, f, ct, index, total) - f.g(88)

    head_bottom = zone_bottom
    if ct.metric:
        style = _DeltaStyle(label_size=24, value_max=128, value_min=56, period_size=28,
                            gap_label=f.g(26), gap_period=f.g(26), bars=True)
        h = _delta_block(c, x, 0, w, ct.metric, style, render=False)
        _delta_block(c, x, zone_bottom - h, w, ct.metric, style)
        head_bottom = zone_bottom - h - f.g(80)
    elif ct.dek and ct.dek != ct.hook:
        font, lines = fit_text(c.draw, ct.dek, SANS_MEDIUM, w, (zone_bottom - head_top) * 0.3, 36, 28, 1.4,
                               max_lines=4)
        top = zone_bottom - text_block_height(font, len(lines), 1.4)
        draw_text_block(c.draw, x, top, lines, font, MUTED_ON_NAVY, 1.4)
        head_bottom = top - f.g(80)

    font, lines = fit_text(c.draw, ct.hook, SERIF_SEMIBOLD, w, head_bottom - head_top, 88, 42, 1.1,
                           max_lines=6, wrap="balance")
    draw_text_block(c.draw, x, head_top, lines, font, PAPER, 1.1)
    return c.img


def _slide_point(f: _Frame, ct: _Content, text: str, n: int, index: int, total: int) -> Image.Image:
    c = _Canvas((f.width, f.height), NAVY)
    x, w = f.left, f.content_w
    num_baseline = _label(c, x, f.top, f"{n:02d}", MARIGOLD, 30, w, tracking=0.08)
    rule_top = num_baseline + f.g(34)
    c.rect(x, rule_top, x + 40, rule_top + 4, MARIGOLD)
    area_top = rule_top + 4 + f.g(72)
    area_bottom = _footer(c, f, ct, index, total) - f.g(96)
    font, lines = fit_text(c.draw, text, SERIF_MEDIUM, w, area_bottom - area_top, 60, 38, 1.3, wrap="pretty")
    h = text_block_height(font, len(lines), 1.3)
    y = area_top + max(0, (area_bottom - area_top - h) * 0.42)
    draw_text_block(c.draw, x, y, lines, font, PAPER, 1.3)
    return c.img


def _slide_caveat(f: _Frame, ct: _Content, index: int, total: int) -> Image.Image:
    c = _Canvas((f.width, f.height), PAPER)
    x, w = f.left, f.content_w
    area_top = _label_and_rule(c, f, "The caveat", AMBER_TEXT) + f.g(72)
    footer_top = _footer(c, f, ct, index, total, light=True)

    note_font, note = fit_text(c.draw, "We publish the limits of every story.", SANS_MEDIUM, w, 200, 32, 24, 1.35)
    note_top = footer_top - f.g(88) - text_block_height(note_font, len(note), 1.35)
    draw_text_block(c.draw, x, note_top, note, note_font, NAVY, 1.35)
    c.rect(x, note_top - f.g(40) - 2, x + w, note_top - f.g(40), NAVY)

    area_bottom = note_top - f.g(40) - f.g(72)
    font, lines = fit_text(c.draw, ct.caveat, SERIF_ITALIC, w, area_bottom - area_top, 54, 34, 1.32, wrap="pretty")
    h = text_block_height(font, len(lines), 1.32)
    y = area_top + max(0, (area_bottom - area_top - h) * 0.38)
    draw_text_block(c.draw, x, y, lines, font, NAVY, 1.32)
    return c.img


_BADGE_SIZE, _BADGE_TRACK, _BADGE_PAD, _BADGE_H = 17, 0.1, 16, 34


def _badge_width(role: str) -> float:
    font = load_font(MONO, _BADGE_SIZE)
    return _tracked_width(clean_text(role.upper(), MONO), font, _BADGE_TRACK) + 2 * _BADGE_PAD


def _badge(c: _Canvas, x1: float, baseline: float, cap: float, role: str) -> float:
    """Teal evidence badge, right-aligned at ``x1`` and centred on a text line. Returns its width."""
    font = load_font(MONO, _BADGE_SIZE)
    text = clean_text(role.upper(), MONO)
    bw = _badge_width(role)
    cy = baseline - cap / 2
    c.pill(x1 - bw, cy - _BADGE_H / 2, x1, cy + _BADGE_H / 2, TEAL)
    bcap, _ = _vmetrics(str(MONO), _BADGE_SIZE)
    _draw_line(c.draw, x1 - bw + _BADGE_PAD, round(cy + bcap / 2), text, font, PAPER, _BADGE_TRACK * font.size)
    return bw


def _slide_sources(f: _Frame, ct: _Content, index: int, total: int) -> Image.Image:
    c = _Canvas((f.width, f.height), NAVY)
    x, w = f.left, f.content_w
    y = _label_and_rule(c, f, "Sources", MUTED_ON_NAVY) + f.g(64)
    hf, hlines = fit_text(c.draw, "Where this comes from", SERIF_SEMIBOLD, w, 200, 64, 40, 1.1, max_lines=2)
    y = draw_text_block(c.draw, x, y, hlines, hf, PAPER, 1.1) + f.g(56)
    zone_bottom = _footer(c, f, ct, index, total) - f.g(88)

    # Bottom: CTA in marigold, tagline muted.
    bottom = zone_bottom
    if ct.tagline:
        tf, tlines = fit_text(c.draw, ct.tagline, SANS_MEDIUM, w, 200, 28, 22, 1.4, max_lines=3, wrap="balance")
        bottom -= text_block_height(tf, len(tlines), 1.4)
        draw_text_block(c.draw, x, bottom, tlines, tf, MUTED_ON_NAVY, 1.4)
        bottom -= f.g(36)
    cta = f"Get {ct.newsletter} free — link in bio"
    cf, clines = fit_text(c.draw, cta, SANS_SEMIBOLD, w, 200, 50, 32, 1.2, max_lines=2, wrap="balance")
    bottom -= text_block_height(cf, len(clines), 1.2)
    draw_text_block(c.draw, x, bottom, clines, cf, MARIGOLD, 1.2)

    # Top: publishers, primary first, each with a teal evidence badge, all at one size.
    list_bottom = bottom - f.g(80)
    row = f.g(104)
    rows = ct.sources or [("Listed in the full story", "")]
    badge_ws = [_badge_width(role) + 32 if role else 0 for _, role in rows]
    name_size = min(_single_line(c, name, SANS_SEMIBOLD, w - bw, 46, 32)[0].size
                    for (name, _), bw in zip(rows, badge_ws))
    cap = _vmetrics(str(SANS_SEMIBOLD), name_size)[0]
    for i, ((name, role), bw) in enumerate(zip(rows, badge_ws)):
        baseline = y + cap + i * row
        if baseline + 12 > list_bottom:
            break
        if role:
            _badge(c, f.x1, baseline, cap, role)
        font, line = _single_line(c, name, SANS_SEMIBOLD, w - bw, name_size, name_size)
        _draw_line(c.draw, x, baseline, line, font, PAPER)
        if i < len(rows) - 1:
            rule_y = baseline + (row - cap) / 2
            c.rect(x, rule_y, x + w, rule_y + 1, MUTED_ON_NAVY)
    return c.img


def _render_slides(story: dict, brand: dict, frame: _Frame, section_name: str | None) -> list[Image.Image]:
    ct = _prepare(story, brand, section_name)
    total = len(ct.points) + 3
    slides = [_slide_cover(frame, ct, 1, total)]
    for n, text in enumerate(ct.points, start=1):
        slides.append(_slide_point(frame, ct, text, n, n + 1, total))
    slides.append(_slide_caveat(frame, ct, total - 1, total))
    slides.append(_slide_sources(frame, ct, total, total))
    return slides


def _story_slug(story: dict) -> str:
    slug = str(story.get("slug") or "").strip()
    if slug:
        return slug
    from gnn.util import slugify
    return slugify(str(story.get("title") or "story"))


# --------------------------------------------------------------------------------------------
# Public renderers
# --------------------------------------------------------------------------------------------


def render_carousel(story: dict, brand: dict, out_dir: Path, section_name: str | None = None) -> list[Path]:
    """Render an Instagram portrait carousel (1080x1350 PNG) for ``story``.

    Slides: a navy cover (section label, hook or title, the key-metric delta if present); one
    slide per ``social.slides`` entry (derived from the dek and why-it-matters if missing); "The
    caveat" on paper, with the first limitation; and the sources with the newsletter CTA.

    Files are written as ``out_dir/{slug}-01.png`` ... ; leftover higher-numbered slides from an
    earlier render of the same slug are removed. Returns the paths in order.
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    slug = _story_slug(story)
    paths: list[Path] = []
    for i, img in enumerate(_render_slides(story, brand, CAROUSEL_FRAME, section_name), start=1):
        path = out_dir / f"{slug}-{i:02d}.png"
        img.save(path, "PNG", optimize=True)
        paths.append(path)
    stale = re.compile(rf"^{re.escape(slug)}-(\d{{2}})\.png$")
    for old in out_dir.glob(f"{slug}-*.png"):
        m = stale.match(old.name)
        if m and int(m.group(1)) > len(paths):
            old.unlink()
    return paths


FFMPEG_FALLBACK = Path("/usr/bin/ffmpeg")


def _ffmpeg() -> str:
    exe = shutil.which("ffmpeg") or (str(FFMPEG_FALLBACK) if FFMPEG_FALLBACK.exists() else None)
    if not exe:
        raise RuntimeError("ffmpeg is required to render story videos but was not found on PATH. "
                           "Install it (e.g. `apt-get install ffmpeg` or `brew install ffmpeg`) and retry.")
    return exe


def render_story_video(slides: list[Path] | None, story: dict, brand: dict, out_path: Path,
                       section_name: str | None = None, seconds_per_slide: float = 3.5,
                       fade: float = 0.5) -> Path:
    """Render a vertical 1080x1920 MP4 (H.264, yuv420p, 30 fps, no audio) for Reels, Shorts
    and TikTok, with fade transitions between slides.

    The carousel layouts are re-rendered at 1080x1920 (not letterboxed), keeping text clear of
    the platform UI: the top 220 px, bottom 380 px and the right-hand action rail. ``slides`` may
    be pre-rendered 1080x1920 frames to use as they are; anything else (including carousel PNGs,
    or None) means the frames are rendered from ``story``.

    Each slide holds for ``seconds_per_slide`` with ``fade`` seconds of cross-fade; the per-slide
    time is shortened if needed to keep the video under 30 seconds. Raises RuntimeError if
    ffmpeg is missing or fails.
    """
    ffmpeg = _ffmpeg()
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="gnn-video-") as tmp:
        frames: list[Path] = []
        given = [Path(p) for p in (slides or [])]
        if given and all(_image_size(p) == VIDEO_SIZE for p in given):
            frames = given
        else:
            for i, img in enumerate(_render_slides(story, brand, VIDEO_FRAME, section_name), start=1):
                path = Path(tmp) / f"frame-{i:02d}.png"
                img.save(path, "PNG")
                frames.append(path)

        n = len(frames)
        hold = max(0.5, float(seconds_per_slide))
        fade = max(0.0, min(float(fade), hold / 2))
        if n * hold + fade > MAX_VIDEO_SECONDS:
            hold = (MAX_VIDEO_SECONDS - fade) / n
            fade = min(fade, hold / 2)
        clip = hold + fade if n > 1 and fade > 0 else hold

        cmd = [ffmpeg, "-y", "-hide_banner", "-loglevel", "error"]
        for p in frames:
            # Decode each still at 2 fps and let the fps filter repeat frames: far cheaper than
            # decoding a 1080x1920 PNG thirty times a second.
            cmd += ["-loop", "1", "-framerate", "2", "-t", f"{clip + 1:.3f}", "-i", str(p)]
        graph = [f"[{i}:v]scale={VIDEO_SIZE[0]}:{VIDEO_SIZE[1]},setsar=1,format=yuv420p,fps={VIDEO_FPS},"
                 f"trim=duration={clip:.3f},setpts=PTS-STARTPTS,settb=AVTB[s{i}]" for i in range(n)]
        last = "s0"
        if n > 1 and fade > 0:
            for k in range(1, n):
                graph.append(f"[{last}][s{k}]xfade=transition=fade:duration={fade:.3f}:offset={k * hold:.3f}[x{k}]")
                last = f"x{k}"
        elif n > 1:
            graph.append("".join(f"[s{i}]" for i in range(n)) + f"concat=n={n}:v=1:a=0[cat]")
            last = "cat"
        cmd += ["-filter_complex", ";".join(graph), "-map", f"[{last}]", "-an",
                "-c:v", "libx264", "-preset", "medium", "-tune", "stillimage", "-crf", "20",
                "-profile:v", "high", "-pix_fmt", "yuv420p", "-r", str(VIDEO_FPS),
                "-movflags", "+faststart", str(out_path)]
        try:
            subprocess.run(cmd, check=True, capture_output=True, text=True)
        except subprocess.CalledProcessError as exc:
            tail = (exc.stderr or "").strip().splitlines()[-5:]
            raise RuntimeError("ffmpeg failed to render the story video: " + " | ".join(tail)) from exc
    return out_path


def _image_size(path: Path) -> tuple[int, int] | None:
    try:
        with Image.open(path) as im:
            return im.size
    except (OSError, ValueError):
        return None


def render_og_image(story: dict, brand: dict, out_path: Path, section_name: str | None = None) -> Path:
    """Render a 1200x630 Open Graph link-preview card: section label, title, the key-metric
    delta in the right third (if present), brand name and domain bottom-left and a 10 px
    marigold bar along the bottom edge. Returns ``out_path``."""
    ct = _prepare(story, brand, section_name)
    W, H = OG_SIZE
    margin, top, bar = 72, 60, 10
    c = _Canvas(OG_SIZE, NAVY)
    d = c.draw

    label_baseline = _label(c, margin, top, ct.section_label, MUTED_ON_NAVY, 20, W - 2 * margin)
    c.rect(0, H - bar, W, H, MARIGOLD)

    footer_baseline = H - bar - 46
    footer_x = margin
    if ct.brand_name:
        bf, name = _single_line(c, ct.brand_name, SANS_SEMIBOLD, 560, 26, 20)
        _draw_line(d, footer_x, footer_baseline, name, bf, PAPER)
        footer_x += _measure(d, name, bf) + 22
    if ct.domain:
        mf = load_font(MONO, 20)
        dom = clean_text(ct.domain, MONO)
        if footer_x + _tracked_width(dom, mf, 0.04) <= W - margin:
            _draw_line(d, footer_x, footer_baseline, dom, mf, MUTED_ON_NAVY, 0.04 * mf.size)
    footer_top = footer_baseline - _vmetrics(str(SANS_SEMIBOLD), 26)[0]

    area_top = label_baseline + 44
    area_bottom = footer_top - 48
    title_w = W - 2 * margin
    split = round(W * 2 / 3)
    style = _DeltaStyle(label_size=17, value_max=88, value_min=36, period_size=20,
                        gap_label=18, gap_period=16, bars=False, label_lines=3, period_lines=2)
    metric_h = 0
    if ct.metric:
        title_w = split - margin - 56
        metric_h = _delta_block(c, split + 28, 0, W - margin - split - 28, ct.metric, style, render=False)

    font, lines = fit_text(d, ct.title, SERIF_SEMIBOLD, title_w, area_bottom - area_top, 68, 34, 1.1,
                           max_lines=4, wrap="balance")
    title_h = text_block_height(font, len(lines), 1.1)
    # Title (and metric) sit a little above the optical centre of the space they share.
    top = area_top + max(0, (area_bottom - area_top - max(title_h, metric_h)) * 0.3)
    draw_text_block(d, margin, top, lines, font, PAPER, 1.1)
    if ct.metric:
        _delta_block(c, split + 28, top, W - margin - split - 28, ct.metric, style)
        c.rect(split, top - 4, split + 4, top + min(metric_h, area_bottom - top) + 4, MARIGOLD)

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    c.img.save(out_path, "PNG", optimize=True)
    return out_path


# --------------------------------------------------------------------------------------------
# Demo
# --------------------------------------------------------------------------------------------

SAMPLE_STORY: dict = {
    "id": "layout-sample",
    "slug": "layout-sample-wind-record",
    "title": "Layout sample: wind supplied a record share of electricity",
    "dek": "Layout sample only. Illustrative figures: wind met 47% of demand in a month, up from 38%.",
    "section": "planet",
    "key_metric": {"label": "Wind share of electricity (illustrative)", "before": "38%", "after": "47%",
                   "period": "Sample period, not real data"},
    "why_it_matters": "This is placeholder copy to check the layout. Real stories carry two or three "
                      "sentences on why the change matters.",
    "limitations": ["Layout sample: these numbers are illustrative, and a single windy month says "
                    "little about the long-term trend."],
    "sources": [
        {"publisher": "Sample Corroborating Source", "url": "https://example.org/b", "role": "corroborating"},
        {"publisher": "Sample Grid Operator", "url": "https://example.org/a", "role": "primary"},
        {"publisher": "Sample Newswire", "url": "https://example.org/c", "role": "lead"},
    ],
    "social": {
        "hook": "Layout sample: wind supplied a record share of the grid’s electricity",
        "slides": [
            "Layout sample. In this illustrative month, wind turbines met 47% of electricity demand, "
            "up from 38% a year earlier.",
            "Placeholder copy: longer slides wrap by measured pixel width and shrink to fit, so a "
            "full 220-character point still sits comfortably inside the margins.",
            "Unicode check: “curly quotes”, €1.2bn and Áine Ó Súilleabháin all render cleanly.",
        ],
        "caption": "Layout sample. Source credit goes here.",
    },
}


def _demo_brand() -> dict:
    brand = {"name": "Good News News", "tagline": "What got better today. Checked, sourced, and honest "
             "about the limits.", "newsletter_name": "The Better Brief", "url": "https://example.com"}
    try:
        from gnn import config
        site = config.site()
        brand.update({k: v for k, v in (site.get("brand") or {}).items() if v})
        brand["url"] = (site.get("site") or {}).get("base_url") or brand["url"]
    except Exception:  # noqa: BLE001 - the demo must run without a valid config
        pass
    return brand


if __name__ == "__main__":
    out = ROOT / ".cache" / "social-demo"
    brand = _demo_brand()
    paths = render_carousel(SAMPLE_STORY, brand, out)
    paths.append(render_og_image(SAMPLE_STORY, brand, out / f"{SAMPLE_STORY['slug']}-og.png"))
    try:
        paths.append(render_story_video(None, SAMPLE_STORY, brand, out / f"{SAMPLE_STORY['slug']}.mp4"))
    except RuntimeError as exc:
        print(f"video skipped: {exc}")
    for p in paths:
        print(p.relative_to(ROOT))
