# Architecture

## Overview

Repository structure: `parsers/anime/` — airing-parser, `parsers/user_anime/`
— user-anime (anime-centric), `parsers/user_user/` — user-user (user-centric),
`parsers/base_parser.py` — base class for all parsers, `parsers/coordinator_app.py`
— coordinator at the root of `parsers/`.

**The coordinator is the main controller.** Parsers are passive: they do
not start their own scheduler_loop on startup. Execution is only through
the coordinator (`POST /trigger-cycle`). The coordinator is smart: it
queries Neo4j itself, builds lists of mal_ids / usernames and passes them
to parsers via the request body. Parsers are dumb: they just parse what
they are given. If there is no work (empty list) — the coordinator does
not call the parser, it waits and checks again. The coordinator alternates:
user-anime (slice) → airing-parser (by time) → user-user (slice) →
airing-parser → ...

```
┌──────────────────────────── coordinator container (port 8570) ────────────┐
│                                                                              │
│   coordinator_app.py (FastAPI)                                            │
│        ├── auto-mode: alternating user-anime/user-user (slices) ↔           │
│        │   airing-parser (by time, default 03:00)                          │
│        ├── coordinator queries Neo4j itself (_select_*), builds batches     │
│        ├── PUT /auto/slice — slice duration (sec, USER_SLICE_SEC)           │
│        ├── PUT /auto/batch-size — batch size (BATCH_SIZE)                   │
│        ├── PUT /anime-time — airing-parser start time (HH:MM)              │
│        ├── PUT /auto/idle-wait — idle wait (sec)                            │
│        └── HTTP endpoints: /, /start/anime, /start/user-anime,             │
│            /start/user-user, /pause, /auto, /auto/stop, /auto/status        │
│                                                                              │
│   Dockerfile + requirements.txt — for the coordinator                      │
│                                                                              │
└──────────────────────────────────────────────────────────────────────────┘
        │ manages
        ▼
┌─────────────────── airing-parser container (port 8567) ──────────────────┐
│   app.py (FastAPI, BaseParser) — passive, runs a cycle only on             │
│   /trigger-cycle (accepts mal_ids from the coordinator in the body)         │
│        └── HTTP endpoints: /refresh/{id}, /trigger-cycle, /status,         │
│            /stubs, /config, /health, /pause, /resume, /cycle-running       │
│   bootstrap.py — runs SEPARATELY, manually, once                            │
│        └── across all historical seasons (1917 → now, except the current 3)   │
│            graph_state.select_due_for_season() → processing.process_one() × N   │
└──────────────────────────────────────────────────────────────────────────┘
        │
        ▼
Neo4j (neo4j container)
  graph: Anime/Genre/Studio/Producer/Person/Character/
        ExternalLink/StreamingPlatform/Manga/User
  queue state: title IS NULL = not processed
  bootstrap progress: bootstrap_progress.txt file (on host via volume)
```

## User Parsers (user-anime container `parsers/user_anime/` port 8568, user-user `parsers/user_user/` port 8569)

```
┌──────────────────────────── user-anime container ──────────────────────────┐
│   user_anime/app.py (FastAPI, BaseParser) — passive, runs a cycle          │
│   only on /trigger-cycle (accepts mal_ids from the coordinator in body)     │
│        ├── scheduler.run_cycle(mal_ids, cfg, is_paused)                     │
│        │   for each mal_id:                                                 │
│        │     fetch stats pages → parse → loader.upsert                      │
│        │     → collect "Recently Updated By" (75 users/page, ~100 pages)    │
│        │     → MERGE :User + RATED, adaptive backoff                         │
│        │     pause checked between items and between stats pages             │
│        │                                                                    │
│        └── HTTP endpoints: /status, /trigger-cycle, /scan-anime/{id},       │
│            /pause, /resume, /cycle-running, /health                         │
│                                                                              │
│   config.py — settings from env                                             │
│   state.py — adaptive backoff, get_user_stats                               │
│   schema.py — USER_FIELDS, RATING_FIELDS, backoff constants                │
└──────────────────────────────────────────────────────────────────────────┘

┌──────────────────────────── user-user container ───────────────────────────┐
│   user_user/app.py (FastAPI, BaseParser) — passive, runs a cycle           │
│   only on /trigger-cycle (accepts usernames from the coordinator in body)    │
│        ├── scheduler.run_cycle(usernames, cfg, is_paused)                   │
│        │   for each username:                                              │
│        │     fetch animelist (JSON) → parse → loader.upsert_ratings        │
│        │     → cleanup stale, archive 404, adaptive backoff                 │
│        │     pause checked between items                                    │
│        │                                                                    │
│        └── HTTP endpoints: /status, /trigger-cycle, /refresh-user/{user},   │
│            /pause, /resume, /cycle-running, /health                         │
│                                                                              │
│   config.py — settings from env                                             │
│   state.py — adaptive backoff, get_user_stats                               │
└──────────────────────────────────────────────────────────────────────────┘
        │
        ▼
Neo4j — same DB as for parsers
```

The coordinator alternates user-anime and user-user in slices
(`COORDINATOR_USER_SLICE_SEC`, default 1800 sec = 30 min). Within a slice,
the coordinator sends batches of `COORDINATOR_BATCH_SIZE` (default 5)
items: send batch → wait for completion → next → until the slice expires.
Airing-parser runs by time (`ANIME_PARSER_TIME`, default 03:00)
— if airing time arrives during a slice, the coordinator interrupts
the slice (pause), runs airing, then continues. Adaptive backoff: anime
start 10 days, +10 on no change; users start 15 days, +15, cap 60 days.

## Coordinator (port 8570)

`parsers/coordinator_app.py` — the main controller. Manages three
parsers via HTTP. Parsers are passive: they have no background loop of
their own. The coordinator queries Neo4j itself (`_select_*` functions
moved from the parsers' state.py), builds lists of mal_ids / usernames,
and passes them to parsers via the `/trigger-cycle` body.

**Auto-mode** (default): slice alternation.
Each slice: user-anime runs for `COORDINATOR_USER_SLICE_SEC` (default
1800 = 30 min), in batches of `COORDINATOR_BATCH_SIZE` (5). Airing-parser
runs by time `ANIME_PARSER_TIME` (03:00). `_pause_all_others` stops all
parsers except the specified one, waits for them to stop. `_smart_wait`
— if there is no work, the coordinator asks the DB when the next item is
due, sleeps until then (no cap), checking airing every 60 seconds.

**API (port 8570):**

| Method | Path | Meaning |
|---|---|---|
| GET | `/` | status of all parsers + auto_mode |
| POST | `/start/anime` | start airing-parser, stop others |
| POST | `/start/user-anime` | start user-anime, stop others |
| POST | `/start/user-user` | start user-user, stop others |
| POST | `/pause` | stop all |
| POST | `/auto` | auto-mode (slice alternation) |
| POST | `/auto/stop` | stop auto-mode |
| GET | `/auto/status` | auto-mode status |
| PUT | `/auto/slice` | change slice duration (sec) |
| PUT | `/auto/batch-size` | change batch size |
| PUT | `/anime-time` | change airing-parser start time (HH:MM) |
| PUT | `/auto/idle-wait` | change idle wait (sec) |

## Data Flow (single title)

```
processing.process_one(mal_id)
  │
  ├── fetcher.get_anime_full(mal_id)
  │     ├── get_html("https://myanimelist.net/anime/{id}")
  │     │     └── mal_scraper.parse_anime_page(html) → dict
  │     ├── extract_slug_from_url(html_main) → slug
  │     ├── get_html("https://myanimelist.net/anime/{id}/{slug}/characters")
  │     │     └── mal_scraper.parse_characters_page(html) → {characters, staff}
  │     └── return combined dict
  │
  ├── parser.extract_fields(raw) → normalized dict
  │     └── _derive_year_season() — year/season fallback from aired
  │
  └── loader.upsert_anime(data)
        ├── MERGE (:Anime {mal_id}) SET all properties
        ├── MERGE (:Genre/:Studio/:Producer) + relationships
        ├── MERGE (:Character) + HAS_CHARACTER
        ├── MERGE (:Person) + STAFF (with roles) + VOICE_ACTED (with language)
        ├── MERGE (:Anime/:Manga) + RELATED_TO (with relation type)
        ├── MERGE (:ExternalLink) + AVAILABLE_AT / HAS_RESOURCE
        └── MERGE (:StreamingPlatform) + STREAMING_ON
```

After a successful `upsert_anime`, the `:Anime` node gets a `title` (not NULL),
meaning "processed". On error, `title` remains NULL and the scheduler picks
the title up again in the next cycle.

## Modules

### `parsers/anime/` (airing-parser, port 8567)

| Module | Responsibility |
|---|---|
| `fetcher.py` | HTTP requests to MyAnimeList — thin wrapper over base_fetcher.MalFetcher |
| `mal_scraper.py` | HTML parsing → dict (BeautifulSoup). Three functions: `parse_season_page`, `parse_anime_page`, `parse_characters_page` |
| `parser.py` | Normalizes data from the scraper for the loader. Year/season fallback from `aired` |
| `loader.py` | dict → Cypher MERGE into Neo4j (all nodes and relationships). `upsert_anime` |
| `graph_state.py` | Queue state in Neo4j. Stubs, due selection, season markers, statistics |
| `processing.py` | Glues fetcher → parser → loader for a single title. Shared code for scheduler and bootstrap |
| `discover.py` | Registers new titles for the 3 current seasons in the graph (via `graph_state.upsert_anime_stub`) |
| `scheduler_logic.py` | One cycle: `discover_recent` + process mal_ids (from coordinator) → `process_one` for each. `is_paused` callback checked between items |
| `bootstrap.py` | Manual pass over all historical seasons (1917→), except the 3 current ones |
| `app.py` | FastAPI (BaseParser) + scheduler cycle, HTTP management endpoints |
| `check_missing.py` | Manual script: cross-checks MAL ↔ Neo4j, adds missing titles |
| `mal_seasons.py` | Season utilities: `current_season`, `shift_season`, `all_seasons` |
| `config.py` | Settings from env (config.yaml removed) |

### `parsers/user_anime/` (user-anime, port 8568)

| Module | Responsibility |
|---|---|
| `scraper.py` | Parses HTML stats pages → dict |
| `fetcher.py` | HTTP client to MyAnimeList with rate limiter |
| `loader.py` | Neo4j: MERGE :User, :RATED, batch upsert, cleanup stale, archive |
| `state.py` | Adaptive backoff, get_user_stats |
| `scheduler.py` | One cycle: mal_ids from coordinator → stats pages → users. `is_paused` between items and pages |
| `app.py` | FastAPI (BaseParser) + cycle, HTTP endpoints |
| `config.py` | Settings from env |

### `parsers/user_user/` (user-user, port 8569)

| Module | Responsibility |
|---|---|
| `scraper.py` | Parses JSON animelist → dict |
| `fetcher.py` | HTTP client to MyAnimeList with rate limiter |
| `loader.py` | Neo4j: MERGE :RATED, cleanup stale, archive |
| `state.py` | Adaptive backoff, get_user_stats |
| `scheduler.py` | One cycle: usernames from coordinator → animelist → RATED. `is_paused` between items |
| `app.py` | FastAPI (BaseParser) + cycle, HTTP endpoints |
| `config.py` | Settings from env |

`:User` ≠ `:Person`. Person — staff/VA (anime creators). User — a viewer
who rates anime.

### `parsers/` (root — base classes + coordinator)

| Module | Responsibility |
|---|---|
| `base_fetcher.py` | MalFetcher — HTTP client with rate limiter, retries, kill switch (PauseRequested) |
| `base_parser.py` | BaseParser — base class for all parsers (FastAPI app, /trigger-cycle, /pause, /resume, /cycle-running, /status, /health) |
| `base_schema.py` | ANIME_FIELDS, DUE_STATUSES, AnimeStatus — unified data schema |
| `base_scraper.py` | Parsing utilities: clean, clean_int — shared across all scrapers |
| `coordinator_app.py` | Coordinator — manages 3 parsers, auto-mode, slice alternation |

## Scheduler

`app.py` runs a cycle only on demand (via the coordinator or a direct
`POST /trigger-cycle`). The coordinator collects due titles via
`_select_due_anime()` and passes them in the body. Each cycle:

1. **discover** — queries MAL season pages for the current, next, and
   previous season. New titles are registered as stubs
   (`:Anime {mal_id, year, season}` with `title IS NULL`).
2. **process mal_ids** (from coordinator) — for each mal_id:
   `process_one` — fetch, parse, load into Neo4j. The `is_paused` callback
   is checked between items — allows interrupting the cycle after the
   current title.
3. The cycle completes. `trigger_cycle` with an empty list → "no work",
   does not start the cycle.

The coordinator calls `_select_due_anime` once (no `while True`) — after
processing, titles with `title IS NOT NULL` and `mal_status = 'Finished
Airing'` drop out of the selection, so no re-scanning occurs.

## Bootstrap vs Scheduler

- **bootstrap.py**: long-running (hours/days), launched manually, once.
  Iterates over all seasons 1917 → now, except the three current ones.
  Resumability: the `bootstrap_progress.txt` file marks the last processed
  season, `title IS NULL` marks unprocessed titles within a season. Errors
  on individual titles do not crash the process.
- **scheduler_logic.py**: a lightweight cycle inside `app.py`. Current/
  next/previous season + forced updates via `/refresh/{mal_id}`.

Both parts share `processing.process_one()` and write to the same database
(Neo4j), so there is no risk of processing the same title in parallel with
different logic.

## GraphRAG

A web interface for querying the Neo4j graph in natural language. A separate
`graphrag` container (port 8666), independent of the parsers.

```
┌──────────────────────────── graphrag container ────────────────────────────┐
│                                                                              │
│   backend/                                                                   │
│     main.py (FastAPI, port 8000 inside the container → 8666 outside)      │
│        ├── serves frontend/ static files (index.html, style.css, app.js,  │
│        │   logs.html, logs.css, logs.js)                                 │
│        ├── /api/chats — chat CRUD (SQLite)                               │
│        ├── /api/chats/{id}/ask — main pipeline                             │
│        ├── /api/logs, /api/health                                         │
│        ├── /logs — logs web page                                          │
│        └── /metrics — Prometheus text format                             │
│                                                                              │
│     graphrag.py — question → answer pipeline:                              │
│        1. LLM generates Cypher (graph schema in system prompt)             │
│           If data is insufficient — returns "CLARIFY: <question>"          │
│           If the question doesn't fit the graph — returns "INVALID"        │
│        2. Cypher is executed in Neo4j                                        │
│        3. LLM formulates the answer in the user's language from the results    │
│           Self-check: on a syntax error — retry up to 3 attempts              │
│           LIMIT — only on explicit count requests ("top 5"), the backend     │
│           caps at 100 rows for the LLM                                        │
│                                                                              │
│     db.py — SQLite: chats, messages, query_logs                             │
│           query_logs: model, llm_base_url, answer, duration_sec,           │
│           cypher_raw (since v13)                                           │
│                                                                              │
│   frontend/ (static files, mounted at /frontend)                         │
│     ChatGPT-like UI: chat list on the left, input field,                    │
│     answers with collapsible Cypher query,                                │
│     markdown rendering of answers (marked.js)                             │
│     /logs — logs table: model, status, Cypher, answer,                    │
│     raw LLM output, duration, status filter                               │
│                                                                              │
└──────────────────────────────────────────────────────────────────────────┘
        │
        ▼
 Neo4j (neo4j container) — same DB as for parsers
```

The LLM is an OpenAI-compatible API, configured via `.env`
(`GRAPHRAG_LLM_BASE_URL`, `GRAPHRAG_LLM_MODEL`). Defaults to `glm-5.2`.