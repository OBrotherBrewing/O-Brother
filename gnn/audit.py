"""Append-only, hash-chained audit log (data/audit.jsonl).

Each line records one decision (published, approved, rejected, corrected, model call
summary...). Every entry carries the hash of the previous line, so editing history
breaks the chain and `verify()` finds it. This is the evidence a buyer's due
diligence, a regulator or a court would ask for.
"""
from __future__ import annotations

import json
import os

from . import ROOT
from .util import iso, sha256

LOG = ROOT / "data" / "audit.jsonl"
GENESIS = "0" * 64


def _last_hash() -> str:
    if not LOG.exists():
        return GENESIS
    last = None
    with open(LOG, encoding="utf-8") as f:
        for line in f:
            if line.strip():
                last = line
    return json.loads(last)["hash"] if last else GENESIS


def record(action: str, **fields) -> dict:
    """Appends one entry. GNN_NO_AUDIT=1 skips writing (used on review branches, so parallel
    pull requests never conflict on this file; the approval is recorded on main at merge)."""
    entry = {"at": iso(), "action": action, **fields}
    if os.environ.get("GNN_NO_AUDIT"):
        return entry
    LOG.parent.mkdir(parents=True, exist_ok=True)
    entry["prev"] = _last_hash()
    body = json.dumps(entry, sort_keys=True, ensure_ascii=False)
    entry["hash"] = sha256(body)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n")
    return entry


def verify() -> tuple[bool, int, str]:
    """Returns (ok, entries checked, message)."""
    if not LOG.exists():
        return True, 0, "no audit log yet"
    prev = GENESIS
    n = 0
    with open(LOG, encoding="utf-8") as f:
        for n, line in enumerate((l for l in f if l.strip()), 1):
            entry = json.loads(line)
            claimed = entry.pop("hash")
            if entry.get("prev") != prev:
                return False, n, f"line {n}: chain broken (prev hash mismatch)"
            if sha256(json.dumps(entry, sort_keys=True, ensure_ascii=False)) != claimed:
                return False, n, f"line {n}: entry altered"
            prev = claimed
    return True, n, "audit chain intact"
