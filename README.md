# papernews

![papernews on a reMarkable, next to a cup of coffee](assets/hero.jpg)

> **This is a fork of [marcj/papernews](https://github.com/marcj/papernews).**
> What differs from upstream:
> - output is rewritten in **French** (upstream targets English);
> - a published container image on **GHCR** — `ghcr.io/saiydat/papernews`;
> - a web **`/admin`** dashboard to watch the pipeline and add / enable /
>   disable / delete sources without editing files;
> - the sources config is persisted on the `/data` volume (seeded on first run).
>
> All credit for the original project goes to its author; the code stays MIT —
> see [LICENSE](LICENSE).

Every news site looks different. Hacker News, MacRumors, Quanta, my
favourite ML blog, my favourite math blog — each one its own layout, fonts,
colors, ads. To read anything I had to wade through somebody's design
choices first and focus past the visual noise.

I much prefer reading the way a LaTeX paper or an old magazine looks: quiet
typography, generous margins, no color, nothing competing for attention.

**papernews** is the fix. A script pulls all those feeds, has an LLM (Claude,
or a local model via Ollama) clean up, translate to **French**, and rewrite
the article bodies — the **full text**, not just summaries — and renders the
result into one consistently
typeset LaTeX PDF. Every article is *in* the PDF; you read entirely
offline, no clicking through, no opening tabs.

A side benefit I didn't expect to like but very much do: one place to read
the day's news instead of five tabs being refreshed all day. One or two
issues per day, no more.

Designed for an e-ink reader like the reMarkable, but it works just as well
in any browser's PDF viewer.

**👉 [See `sample-2026-06-04.pdf` for a real day's output.](sample-2026-06-04.pdf)**

## Status

Hobby project; works. Things will move. Expect rough edges.

## How to use

You need: a machine that can run Docker (your laptop, a NAS, a $5/mo VPS,
anything), an LLM backend (Anthropic API key **or** a local
[Ollama](https://ollama.com) instance), and ~2 GB of disk for the image.

```bash
# 1) Pull
git clone https://github.com/saiydat/papernews
cd papernews

# 2) Configure
cp .env.example .env
$EDITOR .env             # set LLM_BACKEND=ollama + OLLAMA_HOST/OLLAMA_MODEL,
                         # or paste ANTHROPIC_API_KEY=sk-ant-...

# 3) (Optional) Tweak the look
$EDITOR papernews/template.tex.j2

# 4) Run — pulls ghcr.io/saiydat/papernews; add --build to build locally
docker compose up -d

# Open http://localhost:8000   (admin dashboard at /admin)
# Pick your sources from /admin — no file editing needed.
# First PDF builds on demand and is cached. Background ingest runs every 4h.
```

Two things you'll typically change:

- **Sources** — manage them live from the **`/admin`** dashboard (add, enable,
  disable, delete, with an RSS feed test before adding) — no restart needed.
  They're stored in `sources.toml` on the `/data` volume, seeded on first boot.
  Two source kinds: `kind = "hn"` (Hacker News, top-by-points via the Algolia
  API) and `kind = "rss"` (any Atom/RSS feed via feedparser).
- **`papernews/template.tex.j2`** — the LaTeX template. Page size, fonts,
  colors, layout, what goes on the cover, everything. Edit, restart the
  container, refresh `/digest.pdf`.

Optional but useful:

- **`papernews/summarize.py`** + **`papernews/rewrite.py`** — the LLM
  system prompts. When using Anthropic, change `ANTHROPIC_MODEL` to
  `claude-sonnet-4-6` for fancier rewrites at ~10× the cost; adjust
  `_SYSTEM` to change the editorial voice or the output language (this fork
  emits **French** — change the `Output language` rule to switch).
- **`papernews/wiki.py`** — what goes into the World news block and the
  Quote-of-the-day source.

### Getting the PDF onto a reMarkable

A few different ways, no special script needed:

- **Manual** — open `http://your-machine:8000/digest.pdf` in a browser on
  your phone/laptop and upload it to your reMarkable from there (drag-and-
  drop on `my.remarkable.com`, or the reMarkable mobile app, or the USB Web
  Interface at `http://10.11.99.1` while connected by USB).
- **[`rmapi`](https://github.com/ddvk/rmapi)** — a third-party CLI that
  pushes files to your reMarkable cloud account. Pair once, then:
  ```bash
  curl -s http://your-machine:8000/digest.pdf -o today.pdf
  rmapi put today.pdf /Papernews
  ```
  Stick that two-liner in cron on the host and the device picks it up on
  next sync automatically.
- **[Remailable](https://github.com/remailable/remailable)** — a third-party
  email-to-reMarkable bridge ([remailable.getneutrality.org](https://remailable.getneutrality.org)).
  You email the PDF as an attachment to your assigned address and it appears
  on the device. Useful if your papernews host can `mail`/`mutt` but can't
  reach the reMarkable directly. (reMarkable has no first-party
  email-to-device; do not believe earlier versions of this README that
  implied otherwise.)

No native push is built-in because everyone's setup is different and you
probably don't want me poking your reMarkable cloud account with your token.

## Quick start

```bash
git clone https://github.com/saiydat/papernews
cd papernews
cp .env.example .env
# set LLM_BACKEND=ollama (+ OLLAMA_HOST/OLLAMA_MODEL), or paste your
# ANTHROPIC_API_KEY (https://console.anthropic.com/settings/keys)
docker compose up -d        # add --build to build locally instead of pulling GHCR
```

Then visit `http://localhost:8000` — landing page with a preview image and a
link to `/digest.pdf`. The first PDF builds on demand, takes ~1–2 minutes the
first time and is then cached until new content arrives.

State lives in `./data/state.db` (bind-mounted from the host) so it survives
container restarts.

## LLM backends

papernews routes all LLM calls through `papernews/llm.py`. Switch backends
with the `LLM_BACKEND` env var.

### Anthropic (default)

```bash
# .env
LLM_BACKEND=anthropic
ANTHROPIC_API_KEY=sk-ant-...
```

Uses `claude-haiku-4-5` by default. Override with `ANTHROPIC_MODEL=claude-sonnet-4-6`
for higher quality at ~10× the cost.

### Ollama (local)

Run any model locally — no API key, no per-token cost, nothing leaves your
machine.

```bash
# .env
LLM_BACKEND=ollama
OLLAMA_HOST=http://your-ollama-host:11434   # default: http://localhost:11434
OLLAMA_MODEL=qwen2.5:3b                    # default: mistral
OLLAMA_TIMEOUT=1800                        # seconds; increase for slow hardware
PAPERNEWS_WORKERS=1                        # set to 1 for CPU inference
```

**Model recommendations:** The rewrite step is token-heavy — aim for a model
that balances speed and quality for your hardware.

| Model | VRAM | Notes |
|-------|------|-------|
| `qwen2.5:3b` | ~2 GB | Fast, fits on most GPUs |
| `mistral:7b` | ~5 GB | Better quality, needs a discrete GPU |
| `qwen2.5:7b` | ~5 GB | Good quality/speed balance |

CPU inference works but is slow. A discrete GPU with ROCm (AMD) or CUDA
(NVIDIA) support makes a significant difference. Set `PAPERNEWS_WORKERS=1`
when running on CPU to avoid hammering Ollama with concurrent requests.

## What it produces

A 100–200 page PDF with:

- **Cover page**: title + date + article count, quote of the day from
  Wikiquote, a "World news" block (5 tech headlines + 2 Western items from
  Wikipedia's Current Events portal, each compressed to a single sentence).
- **Contents**: every article grouped by source, with dot-leaders to its
  publication date.
- **"Did you know…"** trivia nuggets from Wikipedia's Main Page.
- **The articles themselves**, set in two-column Latin Modern with proper
  paragraph indents, hyphenation, microtypography. Math (`$x = y$`,
  `$$\int f$$`, `\(...\)`, `\[...\]`) is rendered as real LaTeX math. Code
  blocks (fenced or inline) come through in monospace.
- Article bodies and summaries are produced in **French**, translated from
  whatever the source language is during the rewrite step. (The cover's Quote
  of the day and "Did you know…" come straight from English Wikipedia and stay
  in English; the World-news bullets are translated.)

### Cover page

[📄 See the full sample PDF →](sample-2026-06-04.pdf)

[![Cover page: title, quote of the day, world news, table of contents](assets/cover.png)](sample-2026-06-04.pdf)

### Article body

[📄 See the full sample PDF →](sample-2026-06-04.pdf)

[![A typical two-column article page, set in Latin Modern](assets/article.png)](sample-2026-06-04.pdf)

## Architecture

```
                   sources.toml
                       │
            ┌──────────┴──────────┐
            │                     │
            ▼                     ▼
       ┌────────┐            ┌────────┐
       │ gather │            │ wiki/  │
       │  HN +  │            │ news + │
       │  RSS   │            │  QOTD  │
       └───┬────┘            └───┬────┘
           ▼                     │
       ┌────────┐                │
       │extract │                │
       │ (traf- │                │
       │  ilatura)               │
       └───┬────┘                │
           ▼                     │
       ┌─────────┐               │
       │summarize│ ─── LLM       │
       └───┬─────┘               │
           ▼                     │
       ┌─────────┐               │
       │ rewrite │ ─── LLM       │
       └───┬─────┘               │
           ▼                     ▼
       SQLite store (state.db)   in-memory
           │                     │
           └──────────┬──────────┘
                     ▼
              ┌──────────┐
              │  render  │ ── xelatex
              └────┬─────┘
                   ▼
             archive/cache/<hash>.pdf
```

Four stages, each idempotent and resumable:

1. **gather** — pulls new items from each source, runs `trafilatura` to
   extract the article body, stores the raw text. Pure I/O — no LLM cost.
2. **summarize** — batches up to 8 articles per LLM call and produces a
   ≤50-word two-sentence French summary for each (used as the lede in the
   front matter and in the contents listing).
3. **rewrite** — batches up to 8 articles per LLM call and produces a
   clean, properly-paragraphed, translated-to-French version of each
   article body for the renderer. Preserves code fences and `$math$` exactly.
4. **render** — pulls the latest N articles per source from the store,
   plus fresh world news + quote + DYK, and runs them through a Jinja
   template into xelatex → PDF. Results are cached by a hash of "what's in
   the store" + "what's in sources.toml". Same content + same config → same
   cached PDF served instantly.

A background `APScheduler` job runs steps 1–3 every 4 hours (configurable).
The render step is on-demand; the first hit to `/digest.pdf` after an ingest
builds the PDF and caches it.

## HTTP endpoints

| route          | what it does                                            |
|----------------|---------------------------------------------------------|
| `GET /`        | minimal landing page, cover preview + Read PDF link     |
| `GET /digest.pdf` | the current edition (built on demand, then cached)   |
| `GET /preview.png` | page 1 rasterized at 180 DPI                        |
| `GET /sources` | JSON list of configured sources + latest `fetched_at`   |
| `GET /admin`   | dashboard: pipeline state, recent articles, source management |
| `POST /admin/sources` | add a source (`/test`, `/<name>/toggle`, `/<name>/delete` for the rest) |
| `GET /healthz` | liveness probe (returns `ok`)                           |
| `POST /ingest` | manual kick of the gather → summarize → rewrite cycle   |

> **No authentication.** The `/admin` routes can mutate sources and trigger
> ingests, so keep the service on a trusted network (LAN/VPN) — don't expose
> it directly to the internet.

## Configuring sources

Manage sources two ways: from the **`/admin`** dashboard (add / enable /
disable / delete, with an RSS feed test) — no restart needed — or by editing
`sources.toml` directly. In Docker the live file is on the `/data` volume
(`/data/sources.toml`), seeded on first boot from the copy in this repo —
that's the exact file used to produce
[the sample PDF](sample-2026-06-04.pdf). A source carries an optional
`enabled` flag (default `true`); disabled sources are skipped at ingest and
render.

The order of `[[source]]` blocks in the file is the order they'll appear in
the PDF — sources at the top come first. World news, quote of the day, and
the "Did you know…" nuggets are not configured here — they're cover
decorations, fetched fresh on every render.

### `kind = "hn"` — Hacker News via the Algolia search API

Ranks stories by points within a time window. No URL needed; the API is
hardcoded.

| field          | type | default | meaning |
|----------------|------|---------|---------|
| `name`         | string | required | display label (also the contents-page heading) |
| `kind`         | string | required | must be `"hn"` |
| `limit`        | int  | `10`     | how many top stories to keep |
| `since_hours`  | int  | `48`     | only consider stories submitted in the last N hours |
| `min_points`   | int  | `50`     | story must have at least this many points to qualify |

```toml
[[source]]
name        = "Hacker News"
kind        = "hn"
limit       = 10
since_hours = 48
min_points  = 100
```

### `kind = "rss"` — any Atom/RSS feed

Parsed with [feedparser](https://feedparser.readthedocs.io/), so it accepts
RSS 0.9/1.0/2.0 and Atom 1.0 — every blog and most news sites work.

| field   | type   | default  | meaning |
|---------|--------|----------|---------|
| `name`  | string | required | display label (also the contents-page heading) |
| `kind`  | string | required | must be `"rss"` |
| `url`   | string | required | feed URL |
| `limit` | int    | `20`     | take at most N most-recent items |

```toml
[[source]]
name  = "Quanta Magazine"
kind  = "rss"
url   = "https://www.quantamagazine.org/feed/"
limit = 8
```

### Per-source ordering and limits in practice

The `limit` is applied **twice**, on purpose:

- At **fetch** time: gather doesn't pull more than `limit` items from the
  feed (saves bandwidth and trafilatura time).
- At **render** time: even if the store accumulates more than `limit` items
  for a source across multiple ingests (it will — items don't get deleted),
  only the latest `limit` per source make it into a given PDF.

So if you want Quanta to have at most 8 articles in the issue, regardless of
how many they've published this week → set `limit = 8`. If you want Hacker
News to show only the top 5 by points in the last 24h → set `limit = 5,
since_hours = 24`.

> **On the totals.** Adding up every `limit` in `sources.toml` gives you the
> maximum article count per issue. Aim for **30–60 articles** for a
> comfortable 30–60 minute read. Claude's summaries are dense; volume isn't
> quality. An empty section on a slow day is cleaner than padding.

## Scheduling ingests

Two modes; pick whichever fits your routine. Set the env var in `.env`.

### Every N hours (default)

```bash
# .env
INGEST_INTERVAL_SECONDS=14400   # 4 hours (the default)
```

### Cron-style fixed times — "morning and evening edition"

```bash
# .env
INGEST_SCHEDULE=07:00,18:00     # comma-separated HH:MM
INGEST_TIMEZONE=Europe/London   # any IANA tz; default UTC
```

If both are set, `INGEST_SCHEDULE` wins. The render is still on-demand —
hitting `/digest.pdf` between scheduled runs gives you the cached PDF
instantly.

You can also kick a manual ingest any time:

```bash
curl -X POST http://localhost:8000/ingest
```

## Delivery — push the PDF wherever you want

A built-in hook fires after every successful ingest. Point
`POST_INGEST_HOOK` at any executable on the container's filesystem — either a
**bundled** hook under `/app/hooks/` (versioned in the image) or your own
script dropped into `./data/hooks/` (survives rebuilds via the bind mount).
The hook receives the freshly-built PDF path as its first argument.

```bash
# .env
POST_INGEST_HOOK=/data/hooks/push-to-remarkable.sh
POST_INGEST_HOOK_TIMEOUT=300    # optional; default 300s
```

Hook failures are non-fatal — a broken hook logs an error but doesn't
crash the ingest loop.

### Bundled: copy into a Grimmory (or any folder-scanning) library

[`hooks/copy-to-grimmory.sh`](hooks/copy-to-grimmory.sh) is shipped in the
image at `/app/hooks/copy-to-grimmory.sh`. It copies the freshly-built digest
into a library folder, named by date (`Papernews - YYYY-MM-DD.pdf`, same-day
re-ingest overwrites), so a book manager like **Grimmory** that scans a folder
picks it up. On UnRaid it writes the file as `99:100` (nobody:users) so the
reader can access it.

Enable it by setting the env var **and mounting the library folder** — keep the
mount in your own compose/override, not in the repo's generic `docker-compose.yml`:

```yaml
# docker-compose.override.yml (your stack)
services:
  papernews:
    environment:
      - POST_INGEST_HOOK=/app/hooks/copy-to-grimmory.sh
      # - GRIMMORY_DIR=/grimmory-library   # optional; this is the default
    volumes:
      - /mnt/user/data/media/books:/grimmory-library   # your Grimmory library
```

Both containers just share that folder on disk — pure file copy, nothing else.
The hook is idempotent and exits cleanly (logging to the container's stdout) if
no PDF exists yet or the folder isn't mounted.

### Sample: push to a reMarkable 2 over WiFi

Drop this in `./data/hooks/push-to-remarkable.sh` and `chmod +x` it:

```bash
#!/usr/bin/env bash
# Push the latest issue to a reMarkable 2 via SSH.
# Usage: push-to-remarkable.sh <pdf-path>
set -euo pipefail

PDF="$1"
REMARKABLE="root@10.11.99.1"            # adjust to your device's IP
SSH_KEY=/data/hooks/remarkable_id_ed25519

scp -i "$SSH_KEY" -o StrictHostKeyChecking=accept-new \
    "$PDF" "$REMARKABLE:/home/root/papernews.pdf"

# Refresh the UI so the file appears immediately.
ssh -i "$SSH_KEY" "$REMARKABLE" 'systemctl restart xochitl'
```

Generate a passwordless key (`ssh-keygen -t ed25519 -f
data/hooks/remarkable_id_ed25519 -N ""`), add the `.pub` to the
reMarkable's `/home/root/.ssh/authorized_keys` once, and from then on
every ingest pushes the new paper to your device.

The same pattern works for Kindle (`scp` over USB networking), a network
printer (`lp -d papernews "$PDF"`), an email (`mutt -a "$PDF"`), or
anything else you can script.

## Tests

Modest, no-network unittest suite for the web/scheduling/hook behaviour:

```bash
python -m unittest discover -s tests
```

## Local development

You don't have to use Docker — the CLI works directly:

```bash
python3 -m venv .venv
.venv/bin/pip install -e .
export ANTHROPIC_API_KEY=sk-ant-...   # or: export LLM_BACKEND=ollama OLLAMA_HOST=...

.venv/bin/python -m papernews gather       # fetch + extract
.venv/bin/python -m papernews summarize    # LLM pass 1 (batched)
.venv/bin/python -m papernews rewrite      # LLM pass 2 (batched)
.venv/bin/python -m papernews render       # xelatex → PDF
# or all of the above in sequence:
.venv/bin/python -m papernews build
```

Requirements: Python 3.11+, `xelatex` (TeX Live with `texlive-xetex`,
`texlive-latex-extra`, `lmodern`), `pdftoppm` (poppler).

## Customizing the typography

Everything visual lives in one file: [`papernews/template.tex.j2`](papernews/template.tex.j2).

- Page size: `paperwidth=157mm, paperheight=210mm` (tuned for reMarkable Pro)
- Body font: Latin Modern Roman 10pt
- Two-column body for any article over 2000 characters; single-column
  otherwise
- First-line paragraph indent instead of vertical `\parskip` (classic
  magazine convention)
- Microtype protrusion + expansion
- Letter-spacing on small-caps source labels via fontspec's `LetterSpace`

Customize whatever you like — the Jinja delimiters are LaTeX-safe
(`((* ... *))` for blocks, `((( ... )))` for variables) so your `{`, `}` and
`\` don't fight each other.

## Cost

**With Ollama:** free — all inference runs locally.

**With Anthropic (Claude Haiku 4.5, default):** roughly per ingest cycle
with ~50 articles:

- Summarize: 6 batched calls (~8 articles each)
- Rewrite: 6 batched calls
- World-news compress: 1 call

Order-of-magnitude: a few cents to a few tens of cents per cycle depending on
article lengths. At 6 cycles/day that's well under $1/day. Going to Sonnet or
Opus multiplies the bill ~10–30×.

Set a spend cap at
https://console.anthropic.com/settings/billing → Spend limits — the run-loop
can't surprise you above whatever you set.

## Privacy

- All data lives on your machine (`./data/state.db` + `./data/archive/cache/`).
- With `LLM_BACKEND=anthropic`: article text is sent to the Anthropic API
  for summarization and rewriting. That's the only outbound destination for
  content (besides fetching the feeds themselves).
- With `LLM_BACKEND=ollama`: nothing leaves your machine. All inference
  runs locally.
- No analytics, no telemetry, no third-party scripts in the landing page.

## Project layout

```
papernews/
├── papernews/
│   ├── fetch.py          # HN Algolia + RSS feedparser
│   ├── extract.py        # trafilatura
│   ├── llm.py            # LLM backend router (Anthropic or Ollama)
│   ├── summarize.py      # summarization prompts + batching
│   ├── rewrite.py        # rewrite prompts + batching
│   ├── wiki.py           # World news / Quote / DYK / tech feeds
│   ├── store.py          # SQLite article store + queries
│   ├── render.py         # Jinja + xelatex
│   ├── preview.py        # PDF → PNG via pdftoppm
│   ├── cache.py          # On-disk cache by content hash
│   ├── config.py         # sources.toml read/write (powers the /admin UI)
│   ├── cli.py            # papernews command
│   ├── web.py            # Flask + APScheduler + /admin dashboard
│   └── template.tex.j2   # the magazine
├── hooks/                # bundled POST_INGEST_HOOK scripts (copy-to-grimmory.sh)
├── sources.toml          # configured feeds
├── pyproject.toml
├── Dockerfile
├── docker-compose.yml
└── data/                 # gitignored — SQLite, cached PDFs, live sources.toml
```

## Contributing

Open an issue first if you're planning something non-trivial — happy to talk
about direction. The codebase is small enough that you can read it end to
end in an hour.

## License

MIT — see [LICENSE](LICENSE).

## Why "papernews"

Working name; happy to take suggestions. The vibe is: an old-fashioned daily
paper, not a feed. You read it once, then you put it down.
