# papernews

A self-hosted, slow web. Pulls a curated set of feeds every few hours, runs
them through Claude for cleanup + summarization, and serves the result as a
single beautifully-typeset LaTeX PDF — designed for an e-ink reader like the
reMarkable, but it works just as well in your browser's PDF viewer.

The idea: stop refreshing Hacker News, MacRumors, Quanta, your favourite
ML blog and your favourite math blog separately. Have one PDF appear every
few hours that contains the new, cleaned-up, English-language version of all
of it, plus today's quote of the day and a bit of world news.

**See [`sample-2026-06-04.pdf`](sample-2026-06-04.pdf) for what a real day's
output looks like.**

## Status

Hobby project; works. Things will move. Expect rough edges.

## Quick start

```bash
git clone https://github.com/yourname/papernews
cd papernews
cp .env.example .env
# paste your ANTHROPIC_API_KEY into .env (get one at
# https://console.anthropic.com/settings/keys)
docker compose up --build
```

Then visit `http://localhost:8000` — landing page with a preview image and a
link to `/digest.pdf`. The first PDF builds on demand, takes ~1–2 minutes the
first time and is then cached until new content arrives.

State lives in `./data/state.db` (bind-mounted from the host) so it survives
container restarts.

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
- All non-English source content (heise, etc.) is translated to English
  during the rewrite step. You can disable that in the prompt if you don't
  want it.

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
       │summarize│ ─── Claude    │
       └───┬─────┘               │
           ▼                     │
       ┌─────────┐               │
       │ rewrite │ ─── Claude    │
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
2. **summarize** — batches up to 8 articles per Claude call and produces a
   ≤40-word two-sentence summary for each (used as the lede in the front
   matter and in the contents listing).
3. **rewrite** — batches up to 8 articles per Claude call (streamed because
   the output is long) and produces a clean, properly-paragraphed,
   translated-to-English version of each article body for the renderer.
   Preserves code fences and `$math$` exactly.
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
| `GET /healthz` | liveness probe (returns `ok`)                           |
| `POST /ingest` | manual kick of the gather → summarize → rewrite cycle   |

## Configuring sources

Edit `sources.toml`. Two kinds are supported:

```toml
# Hacker News via Algolia search — top by points within a time window.
[[source]]
name = "Hacker News"
kind = "hn"
limit = 10          # render at most N items from this source
since_hours = 48    # surfacing window (ignored for limit, used for ranking)
min_points = 100

# Any Atom/RSS feed via feedparser.
[[source]]
name = "Quanta Magazine"
kind = "rss"
url   = "https://www.quantamagazine.org/feed/"
limit = 8
```

The order of `[[source]]` blocks in the file is the order they'll appear in
the PDF — sources at the top come first.

World news (Wikipedia's Current Events portal) and the quote of the day
(Wikiquote) are not configured here — they're decorations on the cover page
and they're fetched fresh on every render.

## Local development

You don't have to use Docker — the CLI works directly:

```bash
python3 -m venv .venv
.venv/bin/pip install -e .
export ANTHROPIC_API_KEY=sk-ant-...

.venv/bin/python -m papernews gather       # fetch + extract
.venv/bin/python -m papernews summarize    # claude pass 1 (batched)
.venv/bin/python -m papernews rewrite      # claude pass 2 (batched, streamed)
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

Roughly per ingest cycle, with Claude Haiku 4.5 (default model):

- ~50 articles
- Summarize: 6 batched calls (~8 articles each)
- Rewrite: 6 batched calls, streamed
- World-news compress: 1 call

Order-of-magnitude: a few cents to a few tens of cents per cycle depending on
article lengths. At 6 cycles/day that's well under $1/day. Going to Sonnet or
Opus multiplies the bill ~10–30×.

Set a spend cap at
https://console.anthropic.com/settings/billing → Spend limits — the run-loop
can't surprise you above whatever you set.

## Privacy

- All data lives on your machine (`./data/state.db` + `./data/archive/cache/`).
- Article text is sent to the Anthropic API for summarization and rewriting.
  That's the only outbound destination for content (besides fetching the
  feeds themselves).
- No analytics, no telemetry, no third-party scripts in the landing page.

## Project layout

```
papernews/
├── papernews/
│   ├── fetch.py          # HN Algolia + RSS feedparser
│   ├── extract.py        # trafilatura
│   ├── summarize.py      # Anthropic SDK, batched
│   ├── rewrite.py        # Anthropic SDK, batched + streamed
│   ├── wiki.py           # World news / Quote / DYK / tech feeds
│   ├── store.py          # SQLite article store + queries
│   ├── render.py         # Jinja + xelatex
│   ├── preview.py        # PDF → PNG via pdftoppm
│   ├── cache.py          # On-disk cache by content hash
│   ├── cli.py            # papernews command
│   ├── web.py            # Flask + APScheduler
│   └── template.tex.j2   # the magazine
├── sources.toml          # configured feeds
├── pyproject.toml
├── Dockerfile
├── docker-compose.yml
└── data/                 # gitignored — your SQLite + cached PDFs
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
