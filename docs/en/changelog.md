# Changelog

Format: date — what changed and why. Maintained manually, as significant
architectural decisions happen (not every minor commit).

## 2026-07-14 (v14) — tests + refactoring

**Tests:** 98 tests cover the core functionality. Infrastructure:
`pytest.ini`, `requirements-dev.txt`, `tests/conftest.py` (sys.path to
`backend/` and `parsers/`). Tests run in a sandbox (hermes-sandbox).

Coverage:
- `mal_seasons`: current_season, shift_season, all_seasons (12 tests)
- `parser`: _derive_year_season, extract_fields (16 tests)
- `mal_scraper`: utilities, extract_slug, parse_season/anime/characters (30 tests)
- `graphrag`: _extract_cypher, ask() with LLM+Neo4j mocks (10 tests)
- `db`: migration, chat CRUD, messages, logs (12 tests)
- `main`: health, metrics, endpoints, ask with mocks (9 tests)

**Refactoring:** functions split to < 45 lines, no behavior changes.
- `graphrag.py`: `ask()` 170 → 40 lines. Extracted 11 helper functions.
- `main.py`: `api_ask` and metrics split up, `_STATUS_METRIC_MAP` replaces if/elif.
- `mal_scraper.py`: `parse_anime_page` 85 → 20 lines, `_parse_characters`
  100+ → 10 lines. Extracted 15 helper functions.

## 2026-07-14 (v13) — enriched logging, logs page, Prometheus metrics

**Problem:** Cypher query logs (query_logs in SQLite) contained only
question, cypher, status, rows, attempts — it was impossible to tell which
LLM model ran, which URL was used, how long the request took, what the raw
LLM output was, or what the answer was. Logs could only be viewed via
raw JSON at /api/logs.

**Solutions:**
- `db.py`: query_logs migration — added columns `model`, `llm_base_url`,
  `answer`, `duration_sec`, `cypher_raw`. Auto-migration via ALTER TABLE
  for existing databases.
- `graphrag.py`: `ask()` tracks `time.time()`, `raw` LLM output, `LLM_MODEL`,
  `LLM_BASE_URL`. All return paths carry the new fields. `log_query()`
  is called with the full set (including answer — the answer is formulated
  before logging, not via UPDATE).
- `main.py`: `/api/health` returns `model` + `llm_base_url`. `/api/ask`
  response includes `model`, `llm_base_url`, `duration_sec`, `cypher_raw`.
  `/metrics` — Prometheus text exposition format (counters: requests_total,
  requests_ok/error/invalid/clarify, cypher_attempts_total, rows_returned_total;
  summary: duration_sec).
- `frontend/logs.html` + `logs.css` + `logs.js`: logs web page
  (`/logs`). Table: time, status badge, model, question, Cypher, answer
  (markdown), raw LLM output, attempts, rows, duration, chat ID.
  Health bar at top (model + URL + status dot). Stats cards (total/ok/
  empty/error/invalid/clarify/llm_error/avg duration/total rows). Status
  filter, limit selector, refresh button.

## 2026-07-12 (v12) — CLARIFY, LIMIT fix

**Problem:** The LLM added an arbitrary `LIMIT 5` to Cypher queries,
truncating results (Re:Zero — 14 titles, but the model only saw 5).
There was no mechanism for clarifying questions — on an ambiguous query
the system returned an empty/incomplete answer instead of asking.

**Solutions:**
- `graphrag.py`: the LIMIT rule in CYPHER_SYSTEM_PROMPT rewritten —
  LIMIT only on explicit count requests ("top 5"), the backend caps
  at 100 rows for the LLM.
- `graphrag.py`: new rule 11 — on an ambiguous question the LLM
  returns `CLARIFY: <clarifying question>`. Handling in `ask()`:
  status="clarify", _run_cypher is not called.
- `frontend/app.js`: the "clarify" status is shown in the answer meta.

## 2026-07-12 (v11) — GraphRAG prompt fixes, markdown rendering

- `graphrag.py`: CYPHER_SYSTEM_PROMPT — relationship directions specified
  explicitly `(Anime)-[:STAFF]->(Person)`, banned searching by Russian
  titles, exact match for roles. Empty Cypher is handled as invalid
  (not 3 attempts).
- `frontend/`: markdown rendering of answers via marked.js, CSS styles
  for tables, headings, lists.

## 2026-07-12 (v10) — GraphRAG frontend

**Added:** a web interface for querying the Neo4j graph in natural
language. A separate `graphrag` container (port 8666).

- `backend/` — FastAPI server:
  - `graphrag.py`: question → LLM generates Cypher → Neo4j →
    LLM formulates the answer. Self-check: on a Cypher syntax error —
    retry up to 3 attempts.
  - `main.py`: chat API (`/api/chats`), queries (`/api/chats/{id}/ask`),
    logs (`/api/logs`), health (`/api/health`). Serves `frontend/` static files.
  - `db.py`: SQLite for chats, messages, and Cypher query logs.
  - LLM — OpenAI-compatible, configured via `.env`:
    `GRAPHRAG_LLM_BASE_URL`, `GRAPHRAG_LLM_MODEL` (default `glm-5.2`),
    `OLLAMA_API_KEY`, `GRAPHRAG_LLM_MAX_TOKENS`.
- `frontend/` — static files (index.html, style.css, app.js). ChatGPT-like UI:
  chat list on the left, input field, answers with collapsible Cypher.
- `docker-compose.yml` — new `graphrag` service: `depends_on neo4j`,
  port 8666, `graphrag_data` volume for SQLite.

The Neo4j data model is unchanged — GraphRAG only reads the graph.

## 2026-07-12 (v9) — removed :Season, text checkpoint for bootstrap

**Problem:** `:Season {year, season, bootstrapped: true}` nodes stored a
season-closed marker in Neo4j — their only purpose was a bootstrap resume
checkpoint. A redundant entity in the graph, unconnected to anything else.

**Solutions:**
- `graph_state.py`: removed `season_bootstrapped()`, `mark_season_bootstrapped()`,
  and the `seasons_bootstrapped` counter from `get_stats()`.
- `bootstrap.py`: checkpoint via the file `parsers/bootstrap_progress.txt`
  (one line: `2024 spring`). On startup — skips seasons up to and including
  the checkpoint. No file — starts at 1917/winter. Delete the file = start over.
- The file is in `.gitignore` and survives container restart via the `./parsers` volume.
- Title-level resumability is preserved via `title IS NULL`.
- `/status` no longer returns `seasons_bootstrapped`.

## 2026-07-11 (v8) — removed SQLite, single DB (Neo4j)

**Problem:** SQLite (state.db) stored the task queue, retry logic, and
metadata — duplicating data already in Neo4j (mal_status, year, season).
An immortal cache hid updates. Retry logic complicated the code without
real benefit.

**Solutions:**
- `db.py`, `rules.py` — removed.
- `graph_state.py` — new module: queue/state in Neo4j.
  - "Not processed" = `:Anime` with `title IS NULL` (stub).
  - `select_due_anime` — `mal_status IN ['Currently Airing', 'Not yet aired']`.
  - `select_due_for_season` — `title IS NULL` for a specific season.
  - `:Season {year, season, bootstrapped: true}` — replaces seasons_bootstrapped.
- `processing.py` — retry logic, mark_failed, mark_parsed removed.
  Error → log, title stays a stub (title IS NULL), scheduler retries.
- `scheduler_logic.py` — selects due from Neo4j via graph_state.
- `discover.py` — MERGE stubs into Neo4j via graph_state.upsert_anime_stub.
- `bootstrap.py` — works through Neo4j (graph_state).
- `check_missing.py` — cross-checks MAL ↔ Neo4j directly.
- `update_staff.py` — removed db dependency.
- `app.py`:
  - `/refresh/{mal_id}` — calls process_one directly (no queue).
  - `/status` — statistics from Neo4j (total, parsed, stubs, airing, seasons).
  - `/stubs` — new endpoint: incomplete nodes (title IS NULL).
  - `/failed`, `/failed/retry` — removed.
- `docker-compose.yml` — removed `./parsers/data` volume.
- `parsers/data/` — no longer needed.
- Index: `CREATE INDEX FOR (a:Anime) ON (a.mal_status)`.

**Architecture:** one DB (Neo4j), one source of truth.
  - Domain: :Anime, :Person, :Character, :Genre, :Studio, :Producer, :Manga, :Season.
  - State: title IS NULL = not processed, :Season.bootstrapped = season closed.
  - Scheduler: updates Currently Airing + Not yet aired every cycle.

## 2026-07-11 (v7) — removed file cache

**Problem:** the HTML page file cache (5.9 GB, 45k files) had no TTL —
an immortal cache hid MAL updates. Discover read stale season pages and
missed new titles. SQLite (state.db) already tracked processed titles,
making the cache redundant for resumability.

**Solutions:**
- `fetcher.py`: removed cached_get_html, CACHE_DIR, get_cache_stats,
  clear_cache, cleanup_cache_if_over_limit. Added get_html (direct HTTP).
- `app.py`: removed /cache/stats, /cache/clear endpoints, cache from /config.
- `config.py`, `config.yaml`: removed cache_max_mb.
- `docker-compose.yml`: removed CACHE_MAX_MB and the ./parsers/cache volume.
- `scheduler_logic.py`: removed cleanup_cache_if_over_limit.
- `update_staff.py`, `bootstrap.py`, `check_missing.py`: removed
  cache dependencies and --force flags.
- `processing.py`: removed the force parameter from process_one.
- The parsers/cache/ folder (5.9 GB) — delete manually (see operations.md).

**Result:** discover now hits MAL directly every cycle and sees current
data. Resumability is preserved via SQLite (status='ok', next_check_at
in the future).

## 2026-07-11 (v6) — Neo4j indexes and constraints, memory tuning

**Problem:** Neo4j ran without property indexes or constraints —
only default LOOKUP indexes on internal IDs. Every `MERGE
(a:Anime {mal_id: $mal_id})` did a full node scan (O(n)).
At 187k nodes and 520k relationships — already slow; at millions of users
with ratings — a disaster. Neo4j memory — defaults (~512MB heap), 905MB of data.

**Solutions:**
- Created 7 uniqueness constraints (index + uniqueness guarantee):
  `Anime.mal_id`, `Person.mal_id`, `Character.mal_id`, `Manga.mal_id`,
  `Genre.name`, `Studio.name`, `Producer.name`.
- Created 1 regular index: `ExternalLink.url`.
- `docker-compose.yml`: added Neo4j memory settings:
  `heap_initial=1G`, `heap_max=4G`, `pagecache=2G`.
- All MERGEs in loader.py now go through an index — O(log n) instead of O(n).

**Current DB size:** 187,621 nodes, 520,449 relationships, 905 MB on disk.

## 2026-07-11 (v5) — staff fix, scheduler control, DB backfill

**Problems:**
1. Staff was parsed incompletely: fetcher.py used the short URL
   `/anime/{id}/characters`, which MAL redirects to the main anime page
   (without /characters), where staff is limited to 2-4 people. The full URL
   with slug (`/anime/{id}/{slug}/characters`) returns a separate page
   with the full staff list (up to 100+ people).
2. No API for manually triggering a scheduler cycle or changing the interval.
3. Some titles were missed during initial population (MAL adds to
   season pages over time).

**Solutions:**
- `mal_scraper.py`: added `extract_slug_from_url(html)` — extracts the
  slug from the canonical/og:url of the main page.
- `fetcher.py`: `get_anime_full()` now builds the full URL
  `/anime/{id}/{slug}/characters` instead of the short one.
- `loader.py`: added `upsert_staff_only(mal_id, staff)` — targeted
  staff update without overwriting other fields.
- `update_staff.py`: new script to backfill staff for already-processed
  anime (iterates all with <=4 staff, updates via the correct URL).
- `check_missing.py`: new script to cross-check season pages against
  SQLite and add missing titles to the queue.
- `app.py`: added endpoints:
  - `POST /trigger-cycle` — run a scheduler cycle now
  - `PUT /schedule` — change cycle_interval_sec (automatic cycle interval)

## 2026-07-10 (v4) — switch to direct MyAnimeList HTML parsing

**Problems:**
1. Jikan API — a third-party proxy to MyAnimeList, frequently unstable
   (429, 504, SSL errors). The site myanimelist.net is stable.
2. Jikan `/anime/{id}/full` returns a limited set of fields — no characters,
   voice actors, staff, related entries, resources, streaming platforms.
3. `ValueError: could not convert string to float: '.'` for titles
   without scores (score = "N/A", not yet aired) — the parser crashed.

**Solutions:**
- `fetcher.py`: fully rewritten — instead of the Jikan API (JSON) it now
  makes direct HTTP requests to myanimelist.net (HTML). Cache in `.html`
  files instead of `.json`. Same limits (0.5s interval, 55 req/min).
- `mal_scraper.py`: new module — HTML parser via BeautifulSoup. Three
  functions:
  - `parse_season_page()` — season title list (180 for Summer 2026)
  - `parse_anime_page()` — all fields from the anime page (titles, info, stats,
    synopsis, background, related entries, resources, streaming platforms)
  - `parse_characters_page()` — characters + voice actors + staff
- `parser.py`: normalizes data from the scraper (instead of Jikan JSON).
  Year/season fallback from aired for older titles.
- `loader.py`: extended to write the full data model to Neo4j:
  - Nodes: Anime, Genre, Studio, Producer, Character, Person, ExternalLink,
    StreamingPlatform, Manga (related)
  - Relationships: HAS_GENRE, HAS_THEME, HAS_DEMOGRAPHIC, PRODUCED_BY, PRODUCER_OF,
    LICENSED_BY, HAS_CHARACTER, STAFF (with roles), VOICE_ACTED (with language),
    RELATED_TO (with relation type), AVAILABLE_AT, HAS_RESOURCE, STREAMING_ON
  - `a.title` (alias of `title_original`) — for correct display in
    Neo4j Browser (since `aired` came before `title_original` alphabetically)
  - `a.mal_url` — direct link to the anime page
- `requirements.txt`: added `beautifulsoup4==4.12.3`
- `docker-compose.yml`: `JIKAN_BASE_URL` → `MAL_BASE_URL`
- `app.py`: added `POST /failed/retry` endpoint — reset all failed titles
  in one request.
- N/A scores: score/ranked/scored_by correctly return None for titles
  without scores (instead of crashing).
- `from __future__ import annotations` in all modules — compatibility with
  Python 3.9+ (host) and 3.12 (Docker).

**Data:** one title — two HTTP requests (main page + characters/staff).
~27 titles/min at the 55 req/min limit.

## 2026-07-10 (v3) — resilience and API limits

**Problems:**
1. `TypeError: mark_failed() missing 2 required positional arguments` —
   `processing.py` called `mark_failed(mal_id, retry_after_iso)`, but `db.py`
   after v2 required 4 arguments.
2. `bootstrap.py` stopped at the first `process_one()` error.
3. Jikan `/anime/{id}/full` often returns `year=null, season=null` for
   older titles.
4. `SSLEOFError` on Jikan API requests — the fetcher did a single
   `requests.get` with no retries.
5. The scheduler was limited to `batch_size=50` titles per cycle.
6. The `parsers/cache/` and `parsers/data/` caches were empty: docker-compose
   used named volumes over host folders.

**Solutions:**
- `processing.py`: fixed the `mark_failed()` call. `process_one()` never
  throws exceptions outward.
- `bootstrap.py`: the processing loop is wrapped in `try/except` per mal_id.
- `parser.py`: added `_derive_year_season()` — fallback via `aired`.
- `fetcher.py`: rate limiter rewritten — two limits simultaneously.
  Retries with exponential backoff.
- `scheduler_logic.py`: processes all due titles per cycle.
- `docker-compose.yml`: named volumes replaced with bind mounts.

## 2026-07-10 (v2) — retry with attempt limit

**Problem:** a title with an error was postponed 6 hours ahead, a season
could be marked as processed before the title reached Neo4j.

**Solution:** `attempts` + `status` in `anime_progress`. Short retry (5 min),
after 3 failures — `failed`. `GET /failed` endpoint.

## 2026-07-10 (v1) — first MVP version

- Jikan API (no HTML parsing). Eight fields per title.
- File cache + SQLite for queue state.
- Split into bootstrap (archive) and scheduler (current seasons).
- Minimal FastAPI: `/refresh/{mal_id}`, `/status`.
- `docker compose up` (Neo4j + parsers).