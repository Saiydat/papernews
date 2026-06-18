"""Flask web service for papernews.

Routes:
  GET /              landing page (cover preview + 'Read today' link)
  GET /digest.pdf    current edition PDF (cached, built on demand)
  GET /preview.png   page-1 PNG of the current edition
  GET /sources       JSON list of configured sources + counts
  GET /healthz       liveness probe

Background:
  APScheduler runs `ingest` every INGEST_INTERVAL_SECONDS (default 4h).

Environment:
  PAPERNEWS_STATE        path to state.db          (default: state.db)
  PAPERNEWS_CONFIG       path to sources.toml      (default: sources.toml)
  PAPERNEWS_CACHE        path to cache dir         (default: archive/cache)
  PAPERNEWS_WORKERS      LLM workers               (default: 8)

  Scheduling — pick one:
    INGEST_INTERVAL_SECONDS    every N seconds         (default: 14400 = 4h)
    INGEST_SCHEDULE            "HH:MM,HH:MM,..." cron-style fixed times
    INGEST_TIMEZONE            IANA tz, used with INGEST_SCHEDULE (default: UTC)

  Post-ingest delivery hook:
    POST_INGEST_HOOK           executable on disk; receives the PDF path as $1
    POST_INGEST_HOOK_TIMEOUT   seconds (default: 300)

  ANTHROPIC_API_KEY      required for the Claude SDK
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import threading
from datetime import date
from pathlib import Path
from urllib.parse import quote

from apscheduler.schedulers.background import BackgroundScheduler
from flask import (
    Flask, abort, jsonify, redirect, render_template_string, send_file, request,
)

from .cache import edition_key, ensure_dir, pdf_path, preview_path
from .cli import (
    _collect_current_edition,
    _gather_decorations,
    cmd_ingest,
)
from . import config
from .preview import render_cover_png
from .render import build_pdf
from .store import Store


# --- Config helpers -------------------------------------------------------

def _cfg_path(env_var: str, default: str) -> Path:
    return Path(os.environ.get(env_var, default))


STATE_PATH    = _cfg_path("PAPERNEWS_STATE",  "state.db")
CONFIG_PATH   = _cfg_path("PAPERNEWS_CONFIG", "sources.toml")
CACHE_DIR     = _cfg_path("PAPERNEWS_CACHE",  "archive/cache")
WORKERS       = int(os.environ.get("PAPERNEWS_WORKERS", "8"))
INGEST_EVERY  = int(os.environ.get("INGEST_INTERVAL_SECONDS", str(4 * 3600)))
# Bundled config baked into the image; used only to seed CONFIG_PATH on a
# writable volume the first time (so admin edits persist across redeploys).
DEFAULT_CONFIG = _cfg_path("PAPERNEWS_DEFAULT_CONFIG", "/app/sources.toml")


def _seed_config() -> None:
    """If CONFIG_PATH lives on a fresh volume, seed it from the baked default."""
    if CONFIG_PATH.exists() or not DEFAULT_CONFIG.exists():
        return
    if DEFAULT_CONFIG.resolve() == CONFIG_PATH.resolve():
        return
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(DEFAULT_CONFIG, CONFIG_PATH)


_seed_config()


def _load_sources() -> list[dict]:
    """Sources the pipeline ingests/renders — enabled only."""
    return config.active_sources(CONFIG_PATH)


def _article_status(row) -> str:
    """Derive an article's pipeline stage from its row, mirroring the same
    NULL-column logic Store.counts() uses."""
    if row["text"] is None:
        return "unreadable"
    if row["summary"] is None:
        return "pending summary"
    if row["body"] is None:
        return "pending rewrite"
    if row["rendered_at"] is None:
        return "ready"
    return "rendered"


# --- Build pipeline -------------------------------------------------------

# Per-key lock so concurrent requests for the same cache key only build once.
_build_locks: dict[str, threading.Lock] = {}
_build_locks_guard = threading.Lock()


def _lock_for(key: str) -> threading.Lock:
    with _build_locks_guard:
        lock = _build_locks.get(key)
        if lock is None:
            lock = threading.Lock()
            _build_locks[key] = lock
        return lock


def _current_key(store: Store, sources: list[dict]) -> str:
    return edition_key(store.max_fetched_at(), sources)


def _build_pdf_for_key(key: str, store: Store, sources: list[dict]) -> Path:
    """Build the current-edition PDF into the cache, keyed by `key`."""
    out = pdf_path(CACHE_DIR, key)
    if out.exists():
        return out
    with _lock_for(key):
        if out.exists():
            return out
        ensure_dir(CACHE_DIR)
        articles = _collect_current_edition(store, sources)
        decorations = _gather_decorations()
        # Use the cache dir as build workdir so .build/ stays beside the PDF.
        tmp_pdf = build_pdf(
            date.today().isoformat(),
            articles,
            CACHE_DIR,
            decorations=decorations,
        )
        if tmp_pdf != out:
            tmp_pdf.replace(out)
    return out


def _build_preview_for_key(key: str, pdf: Path) -> Path:
    out = preview_path(CACHE_DIR, key)
    if out.exists():
        return out
    with _lock_for(f"preview:{key}"):
        if out.exists():
            return out
        render_cover_png(pdf, out, dpi=180)
    return out


# --- Background ingest ----------------------------------------------------

_ingest_lock = threading.Lock()


def _do_ingest() -> None:
    if not _ingest_lock.acquire(blocking=False):
        return  # one ingest at a time
    try:
        sources = _load_sources()
        store = Store(STATE_PATH)
        cmd_ingest(store, sources, WORKERS)

        # Optional post-ingest delivery hook. The hook is an executable on the
        # container's filesystem (usually dropped in via the bind volume) that
        # receives the freshly-built PDF path as its single argument. Useful
        # for SCP-ing to a reMarkable, mailing it somewhere, printing, etc.
        hook = os.environ.get("POST_INGEST_HOOK", "").strip()
        if hook:
            try:
                key = _current_key(store, sources)
                pdf = _build_pdf_for_key(key, store, sources)
                subprocess.run(
                    [hook, str(pdf)],
                    timeout=int(os.environ.get("POST_INGEST_HOOK_TIMEOUT", "300")),
                    check=False,
                )
            except Exception as e:
                sys.stderr.write(f"[post-ingest hook] {e}\n")
                sys.stderr.flush()
    finally:
        _ingest_lock.release()


# --- Flask app ------------------------------------------------------------

def create_app() -> Flask:
    app = Flask(__name__, static_folder=None)

    @app.get("/healthz")
    def healthz():
        return "ok", 200

    @app.get("/")
    def index():
        return _LANDING_HTML, 200, {"Content-Type": "text/html; charset=utf-8"}

    @app.get("/sources")
    def sources_endpoint():
        sources = _load_sources()
        store = Store(STATE_PATH)
        return jsonify({
            "sources": [
                {"name": s["name"], "kind": s.get("kind"), "limit": s.get("limit")}
                for s in sources
            ],
            "max_fetched_at": store.max_fetched_at(),
        })

    @app.get("/admin")
    def admin():
        sources = config.load_sources(CONFIG_PATH)  # include disabled ones
        store = Store(STATE_PATH)
        per_source = store.per_source_counts()
        recent = [
            {
                "source": r["source"],
                "title": r["title"],
                "url": r["url"],
                "status": _article_status(r),
                "date": (r["published"] or r["surfaced"] or r["fetched_at"] or "")[:10],
            }
            for r in store.recent(50)
        ]
        sources_view = [
            {
                "name": s["name"],
                "kind": s.get("kind", "rss"),
                "url": s.get("url", ""),
                "limit": s.get("limit"),
                "enabled": s.get("enabled", True),
                "count": per_source.get(s["name"], {}).get("total", 0),
                "rendered": per_source.get(s["name"], {}).get("rendered", 0),
            }
            for s in sources
        ]
        return render_template_string(
            _ADMIN_HTML,
            counts=store.counts(),
            sources=sources_view,
            recent=recent,
            last_ingest=(store.max_fetched_at() or "—")[:19].replace("T", " "),
            ingest_running=_ingest_lock.locked(),
            flash=request.args.get("msg", ""),
            error=request.args.get("err", ""),
        )

    @app.post("/admin/sources")
    def admin_add_source():
        f = request.form
        kind = f.get("kind", "rss")
        url = f.get("url", "").strip()
        # For RSS, confirm the feed is reachable/parseable before saving it.
        if kind == "rss" and url:
            probe = config.validate_feed(url)
            if not probe["ok"]:
                return redirect(f"/admin?err={quote('flux invalide : ' + probe['error'])}")
        try:
            config.add_source(CONFIG_PATH, {
                "name": f.get("name", ""),
                "kind": kind,
                "url": url,
                "limit": f.get("limit") or None,
                "since_hours": f.get("since_hours") or None,
                "min_points": f.get("min_points") or None,
            })
        except config.SourceError as e:
            return redirect(f"/admin?err={quote(str(e))}")
        return redirect(f"/admin?msg={quote('source ajoutée')}")

    @app.post("/admin/sources/test")
    def admin_test_feed():
        return jsonify(config.validate_feed(request.form.get("url", "").strip()))

    @app.post("/admin/sources/<name>/toggle")
    def admin_toggle_source(name: str):
        sources = {s["name"]: s for s in config.load_sources(CONFIG_PATH)}
        if name not in sources:
            return redirect(f"/admin?err={quote('source introuvable')}")
        config.set_enabled(CONFIG_PATH, name, not sources[name].get("enabled", True))
        return redirect("/admin")

    @app.post("/admin/sources/<name>/delete")
    def admin_delete_source(name: str):
        if not config.remove_source(CONFIG_PATH, name):
            return redirect(f"/admin?err={quote('source introuvable')}")
        return redirect(f"/admin?msg={quote('source supprimée')}")

    @app.get("/digest.pdf")
    def digest_pdf():
        sources = _load_sources()
        store = Store(STATE_PATH)
        key = _current_key(store, sources)
        pdf = _build_pdf_for_key(key, store, sources)
        return send_file(
            pdf,
            mimetype="application/pdf",
            as_attachment=False,
            download_name=f"papernews-{date.today().isoformat()}.pdf",
            max_age=300,
        )

    @app.get("/preview.png")
    def preview_png():
        sources = _load_sources()
        store = Store(STATE_PATH)
        key = _current_key(store, sources)
        pdf = _build_pdf_for_key(key, store, sources)
        png = _build_preview_for_key(key, pdf)
        return send_file(png, mimetype="image/png", max_age=300)

    @app.post("/ingest")
    def trigger_ingest():
        # Optional manual kick; for cron-style external triggers.
        if _ingest_lock.locked():
            return jsonify({"status": "already running"}), 202
        threading.Thread(target=_do_ingest, daemon=True).start()
        return jsonify({"status": "started"}), 202

    @app.get("/ingest")
    def ingest_get_hint():
        # Friendly 405 — easier than rediscovering you wanted POST.
        return (
            jsonify({
                "error": "POST required to trigger ingest",
                "hint": "curl -X POST http://localhost:8000/ingest",
                "note": "the background scheduler also runs ingest automatically",
            }),
            405,
        )

    return app


def start_scheduler() -> BackgroundScheduler:
    """Start the background ingest scheduler.

    Two modes (in priority order):
      INGEST_SCHEDULE=07:00,18:00   → cron-style at the listed HH:MM times
      INGEST_INTERVAL_SECONDS=14400 → every N seconds (default 4h)

    The cron mode also honours INGEST_TIMEZONE (an IANA tz, default UTC).
    """
    sched = BackgroundScheduler(daemon=True)
    schedule = os.environ.get("INGEST_SCHEDULE", "").strip()
    if schedule:
        tz = os.environ.get("INGEST_TIMEZONE", "UTC")
        for i, hm in enumerate(s.strip() for s in schedule.split(",") if s.strip()):
            try:
                h, m = hm.split(":")
                sched.add_job(
                    _do_ingest, "cron",
                    hour=int(h), minute=int(m),
                    id=f"ingest_cron_{i}",
                    timezone=tz,
                )
            except (ValueError, KeyError):
                sys.stderr.write(f"[scheduler] ignoring invalid time: {hm!r}\n")
                sys.stderr.flush()
    else:
        sched.add_job(_do_ingest, "interval",
                      seconds=INGEST_EVERY, id="ingest",
                      next_run_time=None)
    sched.start()
    return sched


_LANDING_HTML = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>papernews</title>
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <style>
    body { font-family: Georgia, "Times New Roman", serif; max-width: 720px;
           margin: 4rem auto; padding: 0 1.25rem; color: #222; }
    h1   { font-size: 2.4rem; margin: 0 0 0.2rem; }
    .sub { color: #777; margin: 0 0 2rem; font-size: 1rem; }
    a.cta { display: inline-block; padding: 0.7rem 1.4rem; border: 1px solid #222;
            text-decoration: none; color: #222; font-weight: bold; margin-top: 1rem;}
    a.cta:hover { background: #222; color: #fff; }
    img.cover { width: 100%; height: auto; border: 1px solid #eee;
                box-shadow: 0 2px 10px rgba(0,0,0,0.08); }
    .meta { color: #999; font-size: 0.85rem; margin-top: 3rem; }
  </style>
</head>
<body>
  <h1>papernews</h1>
  <p class="sub">A curated PDF you read on your reMarkable, not in a browser.</p>
  <img class="cover" src="/preview.png" alt="Cover preview">
  <p><a class="cta" href="/digest.pdf">Read today (PDF)</a></p>
  <p class="meta">Updated automatically every few hours. <a href="/sources">Sources</a>.</p>
</body>
</html>
"""


_ADMIN_HTML = """<!doctype html>
<html lang="fr">
<head>
  <meta charset="utf-8">
  <title>papernews · admin</title>
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <style>
    body { font-family: Georgia, "Times New Roman", serif; max-width: 960px;
           margin: 2.5rem auto; padding: 0 1.25rem; color: #222; }
    h1 { font-size: 2rem; margin: 0 0 0.2rem; }
    h2 { font-size: 1.15rem; margin: 2rem 0 0.6rem; border-bottom: 1px solid #eee;
         padding-bottom: 0.3rem; }
    .sub { color: #777; margin: 0 0 1.5rem; }
    .cards { display: flex; flex-wrap: wrap; gap: 0.75rem; }
    .card { border: 1px solid #e2e2e2; border-radius: 6px; padding: 0.6rem 1rem;
            min-width: 7rem; }
    .card .n { font-size: 1.6rem; font-weight: bold; }
    .card .l { color: #888; font-size: 0.8rem; text-transform: uppercase;
               letter-spacing: 0.03em; }
    table { border-collapse: collapse; width: 100%; font-size: 0.9rem; }
    th, td { text-align: left; padding: 0.35rem 0.5rem; border-bottom: 1px solid #eee;
             vertical-align: top; }
    th { color: #888; font-weight: normal; font-size: 0.78rem; text-transform: uppercase;
         letter-spacing: 0.03em; }
    a { color: #1a4f8b; text-decoration: none; }
    a:hover { text-decoration: underline; }
    .tag { display: inline-block; padding: 0.05rem 0.45rem; border-radius: 10px;
           font-size: 0.72rem; background: #eee; color: #555; }
    .tag.unreadable { background: #fde2e1; color: #a3261f; }
    .tag.rendered  { background: #e1f0e4; color: #226633; }
    .tag.ready     { background: #e3ecf7; color: #1a4f8b; }
    .muted { color: #aaa; }
    button { font: inherit; padding: 0.5rem 1rem; border: 1px solid #222;
             background: #fff; cursor: pointer; border-radius: 4px; }
    button:hover { background: #222; color: #fff; }
    button.small { padding: 0.2rem 0.55rem; font-size: 0.8rem; }
    button.danger { border-color: #a3261f; color: #a3261f; }
    button.danger:hover { background: #a3261f; color: #fff; }
    .bar { display: flex; align-items: center; gap: 1rem; margin: 0.5rem 0 0; }
    .running { color: #b8860b; }
    .ellip { max-width: 460px; overflow: hidden; text-overflow: ellipsis;
             white-space: nowrap; display: inline-block; }
    .flash { padding: 0.5rem 0.85rem; border-radius: 4px; margin: 0.75rem 0; }
    .flash.ok { background: #e1f0e4; color: #226633; }
    .flash.err { background: #fde2e1; color: #a3261f; }
    form.add { display: flex; flex-wrap: wrap; gap: 0.5rem; align-items: center;
               margin-top: 0.5rem; }
    form.add input, form.add select { font: inherit; padding: 0.35rem 0.5rem;
               border: 1px solid #ccc; border-radius: 4px; }
    form.inline { display: inline; }
    #testout { font-size: 0.85rem; color: #555; margin-top: 0.4rem; }
  </style>
</head>
<body>
  <h1>papernews · admin</h1>
  <p class="sub">Dernier ingest : {{ last_ingest }}
    {% if ingest_running %}<span class="running">· ingest en cours…</span>{% endif %}
  </p>
  {% if flash %}<div class="flash ok">{{ flash }}</div>{% endif %}
  {% if error %}<div class="flash err">{{ error }}</div>{% endif %}

  <h2>Pipeline</h2>
  <div class="cards">
    <div class="card"><div class="n">{{ counts.total }}</div><div class="l">total</div></div>
    <div class="card"><div class="n">{{ counts.unreadable }}</div><div class="l">illisibles</div></div>
    <div class="card"><div class="n">{{ counts.pending_summary }}</div><div class="l">à résumer</div></div>
    <div class="card"><div class="n">{{ counts.pending_rewrite }}</div><div class="l">à réécrire</div></div>
    <div class="card"><div class="n">{{ counts.pending_render }}</div><div class="l">prêts</div></div>
    <div class="card"><div class="n">{{ counts.rendered }}</div><div class="l">rendus</div></div>
  </div>

  <div class="bar">
    <form method="post" action="/ingest">
      <button type="submit">Lancer un ingest</button>
    </form>
    <a href="/digest.pdf">Voir le PDF du jour →</a>
  </div>

  <h2>Sources ({{ sources|length }})</h2>
  <table>
    <tr><th>Nom</th><th>Type</th><th>Limite</th><th>Articles</th><th>Rendus</th><th>État</th><th></th></tr>
    {% for s in sources %}
    <tr>
      <td>{{ s.name }}{% if s.url %}<br><a class="ellip muted" href="{{ s.url }}">{{ s.url }}</a>{% endif %}</td>
      <td>{{ s.kind }}</td>
      <td>{{ s.limit if s.limit is not none else "—" }}</td>
      <td>{{ s.count }}</td>
      <td>{{ s.rendered }}</td>
      <td>{% if s.enabled %}actif{% else %}<span class="muted">désactivé</span>{% endif %}</td>
      <td style="white-space:nowrap">
        <form class="inline" method="post" action="/admin/sources/{{ s.name|urlencode }}/toggle">
          <button class="small" type="submit">{{ "désactiver" if s.enabled else "activer" }}</button>
        </form>
        <form class="inline" method="post" action="/admin/sources/{{ s.name|urlencode }}/delete"
              onsubmit="return confirm('Supprimer {{ s.name }} ?');">
          <button class="small danger" type="submit">suppr.</button>
        </form>
      </td>
    </tr>
    {% endfor %}
  </table>

  <h3 style="margin:1.2rem 0 0.3rem;font-size:1rem;">Ajouter une source</h3>
  <form class="add" method="post" action="/admin/sources">
    <input name="name" placeholder="Nom" required>
    <select name="kind" id="kind" onchange="document.getElementById('urlf').style.display = this.value==='rss' ? '' : 'none';">
      <option value="rss">RSS / Atom</option>
      <option value="hn">Hacker News</option>
    </select>
    <input name="url" id="urlf" placeholder="URL du flux" size="38">
    <input name="limit" type="number" min="1" placeholder="Limite" style="width:5.5rem">
    <button type="button" onclick="testFeed()">Tester</button>
    <button type="submit">Ajouter</button>
  </form>
  <div id="testout"></div>

  <script>
    function testFeed() {
      var url = document.getElementById('urlf').value.trim();
      var out = document.getElementById('testout');
      if (!url) { out.textContent = 'Renseigne une URL.'; return; }
      out.textContent = 'Test en cours…';
      var body = new URLSearchParams(); body.append('url', url);
      fetch('/admin/sources/test', {method:'POST', body: body})
        .then(function(r){ return r.json(); })
        .then(function(d){
          out.textContent = d.ok
            ? '✓ ' + (d.title || 'flux valide') + ' — ' + d.n_entries + ' entrées (ex. : ' + (d.sample_titles[0]||'') + ')'
            : '✗ ' + d.error;
        })
        .catch(function(e){ out.textContent = '✗ ' + e; });
    }
  </script>

  <h2>Articles récents</h2>
  <table>
    <tr><th>Date</th><th>Source</th><th>Titre</th><th>Statut</th></tr>
    {% for a in recent %}
    <tr>
      <td class="muted">{{ a.date }}</td>
      <td>{{ a.source }}</td>
      <td><a class="ellip" href="{{ a.url }}">{{ a.title }}</a></td>
      <td><span class="tag {{ a.status.split(' ')[0] }}">{{ a.status }}</span></td>
    </tr>
    {% endfor %}
  </table>
</body>
</html>
"""


# WSGI entry point
app = create_app()
_scheduler = start_scheduler() if os.environ.get("PAPERNEWS_NO_SCHED") != "1" else None
