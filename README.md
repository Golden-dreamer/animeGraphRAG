[English](README.md) | [Русский](README.ru.md)

# Anime GraphRAG

A [MyAnimeList](https://myanimelist.net) parser (HTML scraping) that loads data
into a Neo4j graph database, with a web interface for natural-language queries.

A scheduler keeps the current, next, and previous seasons up to date.
A bootstrap script walks the archive all the way back to 1917.

212K+ nodes, 899K+ relationships — anime, studios, genres, characters, voice
actors, directors.

## What it looks like

**GraphRAG UI** — a chat for natural-language queries:

![GraphRAG UI](docs/img/graphrag-chat.png)

**Neo4j Browser** — graph visualization:

![Neo4j Graph](docs/img/neo4j-graph.png)

## Example questions

GraphRAG UI accepts questions in Russian, generates a Cypher query against
Neo4j, and formulates an answer. Examples:

- Who directed Fullmetal Alchemist: Brotherhood, and what else have they directed?
- Top-10 anime from Kyoto Animation by score
- What genres does One Piece have?
- Voice actors with the most roles
- Which anime aired in Spring 2024?
- Who voiced Goku?
- Studios with the highest average score
- Which anime are in both Action and Comedy genres?
- Where to watch Demon Slayer (streaming)?
- All Evangelion characters

More in [`docs/en/popular_cypher_commands.md`](docs/en/popular_cypher_commands.md).

## Getting started

1. Open `.env` in the project root and change `NEO4J_PASSWORD` to your own.
2. Start the project:

   ```bash
   docker compose up -d --build
   ```

3. Verify:
   - **Neo4j Browser:** `http://localhost:7474` (login `neo4j`, password from `.env`)
   - **FastAPI:** `http://localhost:8567/docs`
   - **GraphRAG UI:** `http://localhost:8666`
   - **Query logs:** `http://localhost:8666/logs`
   - **Prometheus metrics:** `http://localhost:8666/metrics`

The scheduler starts automatically. By default it runs once a day
(configurable via `cycle_interval_sec`). MyAnimeList rate limits
(0.5s between requests, 55 req/min) are respected automatically.

## Initial archive backfill (optional, one-time)

Load all anime from 1917 onward (except the three current seasons — the
scheduler handles those):

```bash
docker compose run --rm parsers python bootstrap.py
```

Progress is persisted in Neo4j (`title IS NULL` for unprocessed entries)
and in the file `parsers/bootstrap_progress.txt`. If interrupted, just run
it again — it resumes from where it stopped. Throughput is ~27 titles/min
(two requests per title: main page + characters/staff).

## API management

```bash
# Status
curl http://localhost:8567/status

# Incomplete nodes (title IS NULL)
curl http://localhost:8567/stubs

# Force-refresh a single title
curl -X POST http://localhost:8567/refresh/{mal_id}

# Trigger a scheduler cycle right now
curl -X POST http://localhost:8567/trigger-cycle

# Change the automatic cycle interval (seconds, minimum 60)
curl -X PUT http://localhost:8567/schedule \
  -H "Content-Type: application/json" \
  -d '{"cycle_interval_sec": 3600}'
```

Full list of endpoints — [`docs/configuration.md`](docs/configuration.md).

## Utility scripts

```bash
# Backfill staff for anime with <=4 entries
docker compose run --rm parsers python update_staff.py

# Reconcile seasonal pages against the DB and add missing ones
docker compose run --rm parsers python check_missing.py          # current seasons
docker compose run --rm parsers python check_missing.py --all    # all seasons (1917→)
```

## Architecture

```
parsers/                                      backend/
  app.py             — FastAPI + scheduler     main.py      — FastAPI, serves UI
  scheduler_logic.py — one cycle               graphrag.py  — question → Cypher → answer
  discover.py        — season registration     db.py        — SQLite: chats, logs
  processing.py      — per-title processing
  fetcher.py         — HTTP to MAL + rate-limit frontend/
  mal_scraper.py     — HTML scraper              index.html  — ChatGPT-like UI
  parser.py          — normalization             style.css
  loader.py          — writes to Neo4j          app.js
  graph_state.py     — queue/state in Neo4j     logs.html   — logs table
  config.py          — config.yaml + env        logs.css
  bootstrap.py       — manual archive backfill   logs.js
  update_staff.py    — staff backfill
  check_missing.py   — reconcile with MAL       Neo4j
  config.yaml        — settings                  graph: Anime, Genre, Studio, Producer,
                                              Person, Character, ExternalLink,
                                              StreamingPlatform, Manga
```

More in [`docs/en/architecture.md`](docs/en/architecture.md).

## Documentation

| File | Description |
|---|---|
| [`docs/en/architecture.md`](docs/en/architecture.md) | Architecture, data flow, modules |
| [`docs/en/data-model.md`](docs/en/data-model.md) | Neo4j nodes, relationships, indexes |
| [`docs/en/operations.md`](docs/en/operations.md) | Operations, monitoring, errors |
| [`docs/en/configuration.md`](docs/en/configuration.md) | Configuration, API endpoints |
| [`docs/en/popular_cypher_commands.md`](docs/en/popular_cypher_commands.md) | 20 example Cypher queries |
| [`docs/en/changelog.md`](docs/en/changelog.md) | Changelog |

## Tech stack

- **Neo4j 5** — graph database
- **FastAPI** — parser API and GraphRAG backend
- **Docker Compose** — orchestration (3 containers: neo4j, parsers, graphrag)
- **BeautifulSoup** — MyAnimeList HTML scraping
- **OpenAI-compatible LLM** — text-to-Cypher pipeline (default: `glm-5.2`)
- **SQLite** — stores GraphRAG chats and logs