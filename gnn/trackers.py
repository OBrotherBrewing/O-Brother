"""Progress trackers: long-run open-data series rendered as small charts on /trackers/.

Data comes from Our World in Data grapher CSVs (CC BY 4.0, credit required). Definitions
live in data/trackers/trackers.yaml; `python -m gnn trackers` refreshes the JSON files.
"""
from __future__ import annotations

import csv
import io

import yaml

from . import ROOT
from .fetch import Fetcher
from .util import iso, write_json

DIR = ROOT / "data" / "trackers"
OWID_CSV = "https://ourworldindata.org/grapher/{slug}.csv?v=1&csvType=full"


def _fmt(v: float, unit: str) -> str:
    if unit == "%":
        return f"{v:.1f}%"
    if abs(v) >= 100:
        return f"{v:,.0f}{(' ' + unit) if unit else ''}"
    return f"{v:.2f}{(' ' + unit) if unit else ''}".rstrip()


def summarise(series: list[list], unit: str, direction: str) -> str:
    (y0, v0), (y1, v1) = series[0], series[-1]
    verb = "fell" if v1 < v0 else "rose"
    better = (v1 < v0) == (direction == "down")
    tail = "" if better else " (moving the wrong way)"
    return f"{verb.capitalize()} from {_fmt(v0, unit)} in {int(y0)} to {_fmt(v1, unit)} in {int(y1)}{tail}."


def update(fetcher: Fetcher | None = None) -> list[str]:
    fetcher = fetcher or Fetcher()
    defs = yaml.safe_load((DIR / "trackers.yaml").read_text(encoding="utf-8"))["trackers"]
    log = []
    for t in defs:
        status, body, _ = fetcher.raw(OWID_CSV.format(slug=t["owid_slug"]))
        if status != 200:
            log.append(f"FAIL {t['id']}: HTTP {status}")
            continue
        rows = list(csv.reader(io.StringIO(body.decode("utf-8", "ignore"))))
        head, data = rows[0], rows[1:]
        col = head.index(t["column"]) if t.get("column") in head else 3
        series = []
        for r in data:
            if r[0] == t.get("entity", "World") and len(r) > col and r[col]:
                try:
                    series.append([int(r[2]), float(r[col])])
                except ValueError:
                    continue
        start = t.get("start_year")
        series = [p for p in series if not start or p[0] >= start]
        if len(series) < 2:
            log.append(f"FAIL {t['id']}: no data for {t.get('entity', 'World')}")
            continue
        out = {
            "id": t["id"], "title": t["title"], "topic": t["topic"], "unit": t.get("unit", ""),
            "series": series, "summary": summarise(series, t.get("unit", ""), t.get("better", "down")),
            "source": "Our World in Data", "source_url": f"https://ourworldindata.org/grapher/{t['owid_slug']}",
            "licence": "CC BY 4.0", "updated_at": iso(),
        }
        write_json(DIR / f"{t['id']}.json", out)
        log.append(f"OK   {t['id']}: {len(series)} points")
    return log
