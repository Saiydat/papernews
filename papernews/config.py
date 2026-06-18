"""Centralized read/write of the sources config (sources.toml).

The web admin mutates the source list at runtime, so the config must live on a
writable volume (see PAPERNEWS_CONFIG / the /data seeding in web.py). Reads use
the stdlib `tomllib`; writes go through `tomli_w` with an atomic
temp-file-then-replace under a lock so a concurrent read never sees a partial
file.

A source entry is a dict with: name (str), kind ("rss"|"hn"), and kind-specific
fields — rss needs `url`; both accept `limit`; hn accepts `since_hours`,
`min_points`. An optional `enabled` flag (default True) lets the UI disable a
source without deleting it.
"""
from __future__ import annotations

import os
import tempfile
import threading
import tomllib
from pathlib import Path
from typing import Any

import tomli_w


_write_lock = threading.Lock()

VALID_KINDS = ("rss", "hn")


class SourceError(ValueError):
    """Raised on invalid source input (bad kind, missing field, duplicate)."""


# --- read ------------------------------------------------------------------

def load_sources(path: str | Path) -> list[dict]:
    """All configured sources, each with an explicit `enabled` bool."""
    p = Path(path)
    if not p.exists():
        return []
    with open(p, "rb") as f:
        sources = tomllib.load(f).get("source", [])
    for s in sources:
        s.setdefault("enabled", True)
    return sources


def active_sources(path: str | Path) -> list[dict]:
    """Sources the pipeline should actually ingest/render (enabled only)."""
    return [s for s in load_sources(path) if s.get("enabled", True)]


# --- write -----------------------------------------------------------------

def save_sources(path: str | Path, sources: list[dict]) -> None:
    """Atomically write the source list back to `path`."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with _write_lock:
        fd, tmp = tempfile.mkstemp(dir=str(p.parent), suffix=".toml.tmp")
        try:
            with os.fdopen(fd, "wb") as f:
                tomli_w.dump({"source": sources}, f)
            os.replace(tmp, p)
        except BaseException:
            if os.path.exists(tmp):
                os.unlink(tmp)
            raise


# --- validation + mutation -------------------------------------------------

def _normalize(entry: dict) -> dict:
    """Validate a new source entry and coerce its fields to clean types."""
    name = str(entry.get("name", "")).strip()
    if not name:
        raise SourceError("le nom est obligatoire")
    kind = str(entry.get("kind", "rss")).strip().lower()
    if kind not in VALID_KINDS:
        raise SourceError(f"type inconnu : {kind!r} (attendu : {', '.join(VALID_KINDS)})")

    out: dict[str, Any] = {"name": name, "kind": kind, "enabled": bool(entry.get("enabled", True))}

    if kind == "rss":
        url = str(entry.get("url", "")).strip()
        if not url:
            raise SourceError("une source RSS exige une URL")
        out["url"] = url
        out["limit"] = int(entry.get("limit") or 20)
    elif kind == "hn":
        out["limit"] = int(entry.get("limit") or 10)
        out["since_hours"] = int(entry.get("since_hours") or 48)
        out["min_points"] = int(entry.get("min_points") or 50)
    return out


def add_source(path: str | Path, entry: dict) -> dict:
    """Validate and append a source. Raises SourceError on a duplicate name."""
    normalized = _normalize(entry)
    sources = load_sources(path)
    if any(s["name"].strip().lower() == normalized["name"].lower() for s in sources):
        raise SourceError(f"une source nommée {normalized['name']!r} existe déjà")
    sources.append(normalized)
    save_sources(path, sources)
    return normalized


def set_enabled(path: str | Path, name: str, enabled: bool) -> bool:
    """Toggle a source's enabled flag. Returns True if a source was found."""
    sources = load_sources(path)
    found = False
    for s in sources:
        if s["name"] == name:
            s["enabled"] = bool(enabled)
            found = True
    if found:
        save_sources(path, sources)
    return found


def remove_source(path: str | Path, name: str) -> bool:
    """Delete a source by name. Returns True if one was removed."""
    sources = load_sources(path)
    kept = [s for s in sources if s["name"] != name]
    if len(kept) == len(sources):
        return False
    save_sources(path, kept)
    return True


# --- feed test -------------------------------------------------------------

def validate_feed(url: str) -> dict:
    """Probe an RSS/Atom URL with feedparser so the UI can preview a feed
    before adding it. Never raises — returns {ok, title, n_entries,
    sample_titles, error}."""
    import feedparser

    try:
        d = feedparser.parse(url)
    except Exception as e:  # feedparser is lenient, but be safe
        return {"ok": False, "error": str(e), "title": "", "n_entries": 0, "sample_titles": []}

    entries = getattr(d, "entries", []) or []
    # bozo=1 with no entries means the URL didn't yield a usable feed.
    if not entries:
        reason = ""
        if getattr(d, "bozo", 0):
            reason = str(getattr(d, "bozo_exception", "")) or "flux illisible"
        return {"ok": False, "error": reason or "aucune entrée trouvée",
                "title": "", "n_entries": 0, "sample_titles": []}

    feed = getattr(d, "feed", {})
    return {
        "ok": True,
        "error": "",
        "title": getattr(feed, "title", "") if feed else "",
        "n_entries": len(entries),
        "sample_titles": [getattr(e, "title", "") for e in entries[:3]],
    }
