"""Loads the three YAML config files once per process."""
from __future__ import annotations

import functools
from pathlib import Path

import yaml

from . import ROOT


def _load(name: str) -> dict:
    with open(ROOT / "config" / name, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


@functools.lru_cache(maxsize=None)
def site() -> dict:
    return _load("site.yaml")


@functools.lru_cache(maxsize=None)
def pipeline() -> dict:
    return _load("pipeline.yaml")


@functools.lru_cache(maxsize=None)
def sources_file() -> dict:
    return _load("sources.yaml")


def sources() -> list[dict]:
    return sources_file().get("sources", [])


def source(source_id: str) -> dict:
    for s in sources():
        if s["id"] == source_id:
            return s
    raise KeyError(f"unknown source {source_id!r}; add it to config/sources.yaml")


def licence(licence_id: str) -> dict:
    return sources_file().get("licences", {}).get(licence_id, {})


def primary_domains() -> list[str]:
    return sources_file().get("primary_domains", [])


def section_names() -> dict[str, str]:
    return {s["slug"]: s["name"] for s in site().get("sections", [])}


def path(*parts: str) -> Path:
    p = ROOT.joinpath(*parts)
    return p
