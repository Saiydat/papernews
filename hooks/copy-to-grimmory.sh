#!/usr/bin/env bash
#
# copy-to-grimmory.sh — POST_INGEST_HOOK that drops the freshly built digest
# PDF into a Grimmory (or any folder-scanning book manager) library directory.
#
# papernews invokes the hook as:   copy-to-grimmory.sh <pdf-path>
# where <pdf-path> ($1) is the current edition PDF that web.py just built in
# the cache (/data/archive/cache/<hash>.pdf). We name the copy by date so each
# day is a distinct, human-readable entry in the library.
#
# It is a pure side effect: it never fails the ingest. On any problem it logs
# to stdout and exits 0.
#
# Configuration (env, all optional — sensible defaults below):
#   GRIMMORY_DIR   destination library dir mounted in the container  (/grimmory-library)
#   PAPERNEWS_CACHE cache dir, used only for the fallback lookup      (/data/archive/cache)
#   GRIMMORY_UID/GRIMMORY_GID  owner of the written file              (99:100 = nobody:users on UnRaid)
#
set -uo pipefail

LIBRARY_DIR="${GRIMMORY_DIR:-/grimmory-library}"
CACHE_DIR="${PAPERNEWS_CACHE:-/data/archive/cache}"
OWNER_UID="${GRIMMORY_UID:-99}"
OWNER_GID="${GRIMMORY_GID:-100}"

log() { echo "[copy-to-grimmory] $*"; }

# 1) Source PDF: prefer the path papernews passed ($1); otherwise fall back to
#    the most recently modified *.pdf in the cache.
src="${1:-}"
if [[ -z "$src" || ! -f "$src" ]]; then
    src="$(ls -1t "$CACHE_DIR"/*.pdf 2>/dev/null | head -n1 || true)"
fi
if [[ -z "$src" || ! -f "$src" ]]; then
    log "no digest PDF found yet (cache empty?) — nothing to copy, exiting cleanly."
    exit 0
fi

# 2) Destination dir must be mounted; if not, skip without failing the ingest.
if [[ ! -d "$LIBRARY_DIR" ]]; then
    log "ERROR: library dir '$LIBRARY_DIR' is not mounted — skipping copy."
    exit 0
fi

# 3) Date-stamped, readable name. Same day overwrites (idempotent: re-ingest
#    on the same day updates the file instead of duplicating it).
dest="$LIBRARY_DIR/Papernews - $(date +%F).pdf"

# 4) Copy atomically (temp + mv) so the scanner never sees a half-written file.
#    The temp is a dotfile so it isn't picked up mid-copy.
tmp="$(mktemp "$LIBRARY_DIR/.papernews.XXXXXX" 2>/dev/null || true)"
if [[ -z "$tmp" ]]; then
    log "ERROR: cannot create temp file in '$LIBRARY_DIR' (permissions?) — skipping."
    exit 0
fi
if ! cp -f "$src" "$tmp"; then
    log "ERROR: copy failed ($src -> $tmp) — skipping."
    rm -f "$tmp"
    exit 0
fi

# 5) UnRaid permissions: the file must be readable by Grimmory (nobody:users).
#    We run as root, so chown works; tolerate failure if run as non-root.
chown "$OWNER_UID:$OWNER_GID" "$tmp" 2>/dev/null || log "note: chown to ${OWNER_UID}:${OWNER_GID} skipped (not root?)"
chmod 0644 "$tmp" 2>/dev/null || true

mv -f "$tmp" "$dest"
log "copied: $src -> $dest (owner ${OWNER_UID}:${OWNER_GID})"
exit 0
