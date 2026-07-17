# Architecture

## Overview

```
┌──────────────────────────── parsers container ─────────────────────────────┐
│                                                                              │
│   app.py (FastAPI + background asyncio loop)                                │
│        │                                                                    │
│        ├── every cycle_interval_sec: scheduler_logic.run_cycle()          │
│        │        │                                                          │
│        │        ├── discover.discover_recent()  — registers titles        │
│        │        │     of the current/next/previous season as stubs         │
│        │        │                                                          │
│        │        └── graph_state.select_due_anime() → processing.process_one() × N   │
│        │                                                                    │
│        └── HTTP endpoints: /refresh/{id}, /trigger-cycle, /schedule,       │
│            /status, /stubs, /config, /health                               │
│                                                                              │
│   bootstrap.py — runs SEPARATELY, manually, once                            │
│        └── across all historical seasons (1917 → now, except the current 3)   │
│            graph_state.select_due_for_season() → processing.process_one() × N   │
│                                                                              │
└──────────────────────────────────────────────────────────────────────────┘
        │
        ▼
 Neo4j (neo4j container)
   graph: Anime/Genre/Studio/Producer/Person/Character/
         ExternalLink/StreamingPlatform/Manga
   queue state: title IS NULL = not processed
   bootstrap progress: bootstrap_progress.txt file (on host via volume)
```

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

| Module | Responsibility |
|---|---|
| `fetcher.py` | HTTP requests to MyAnimeList, rate limiting (0.5s + 55 req/min), retries with backoff |
| `mal_scraper.py` | HTML parsing → dict (BeautifulSoup). Three functions: `parse_season_page`, `parse_anime_page`, `parse_characters_page` |
| `parser.py` | Normalizes data from the scraper for the loader. Year/season fallback from `aired` |
| `loader.py` | dict → Cypher MERGE into Neo4j (all nodes and relationships). `upsert_anime`, `upsert_staff_only` |
| `graph_state.py` | Queue state in Neo4j. Stubs, due selection, season markers, statistics |
| `processing.py` | Glues fetcher → parser → loader for a single title. Shared code for scheduler and bootstrap |
| `discover.py` | Registers new titles for the 3 current seasons in the graph (via `graph_state.upsert_anime_stub`) |
| `scheduler_logic.py` | One cycle: `discover_recent` + `select_due_anime` → `process_one` for each |
| `bootstrap.py` | Manual pass over all historical seasons (1917→), except the 3 current ones |
| `app.py` | FastAPI + background scheduler loop, HTTP management endpoints |
| `update_staff.py` | Manual script: backfills staff for anime with <=4 records |
| `check_missing.py` | Manual script: cross-checks MAL ↔ Neo4j, adds missing titles |
| `mal_seasons.py` | Season utilities: `current_season`, `shift_season`, `all_seasons` |
| `config.py` | Loads `config.yaml` + env variables into a `Config` object |

## Scheduler

`app.py` starts a background asyncio task on startup. Each cycle:

1. **discover** — queries MAL season pages for the current, next, and
   previous season. New titles are registered as stubs
   (`:Anime {mal_id, year, season}` with `title IS NULL`).
2. **select_due_anime** — a single Neo4j query: all titles with
   `mal_status IN ['Currently Airing', 'Not yet aired']` OR `title IS NULL`.
   Priority: stubs (0) → airing (1) → upcoming (2).
3. **process_one** for each mal_id — fetch, parse, load into Neo4j.
4. The cycle completes. The next-run timer starts from the end of
   processing.

`select_due_anime` is called once (no `while True`) — after processing,
titles with `title IS NOT NULL` and `mal_status = 'Finished Airing'` drop
out of the selection, so no re-scanning occurs.

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
`graphrag` container (port 8666), independent of `parsers`.

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
│        3. LLM formulates the answer in Russian from the results            │
│        Self-check: on a syntax error — retry up to 3 attempts              │
│        LIMIT — only on explicit count requests ("top 5"), the backend     │
│        caps at 100 rows for the LLM                                        │
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