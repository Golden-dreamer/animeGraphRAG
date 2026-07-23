# Configuration

Settings are split across two levels: `.env` (credentials, ports, API
limits) and `docker-compose.yml` (Neo4j memory, service ports). All `.env`
parameters can be changed without rebuilding the image — just
`docker compose restart`. `config.yaml` has been removed — all parser
settings are via env variables.

## `.env` (project root)

| Variable | Default | Meaning |
|---|---|---|
| `NEO4J_PASSWORD` | — | Neo4j password (login is fixed — `neo4j`) |
| `PARSERS_PORT` | `8567` | airing-parser port on the host |
| `USER_ANIME_PORT` | `8568` | user-anime port on the host |
| `USER_USER_PORT` | `8569` | user-user port on the host |
| `COORDINATOR_PORT` | `8570` | coordinator port on the host |
| `API_MIN_INTERVAL_SEC` | `0.5` | Minimum interval between requests (2/sec, margin under 3) |
| `API_RATE_WINDOW_SEC` | `60` | Sliding limit window (seconds) |
| `API_RATE_WINDOW_MAX` | `55` | Max requests per window (margin under 60) |
| `API_MAX_RETRIES` | `4` | Number of retries on error |
| `API_RETRY_BASE_DELAY` | `1.5` | Base backoff delay (seconds) |
| `API_RETRY_MAX_DELAY` | `30` | Backoff ceiling (seconds) |
| `API_HTTP_TIMEOUT` | `20` | HTTP request timeout (seconds) |
| `MAL_BASE_URL` | `https://myanimelist.net` | Base site URL |
| `TZ` | `Europe/Moscow` | Timezone for all containers |
| `OLLAMA_API_KEY` | — | API key for the GraphRAG LLM (OpenAI-compatible) |
| `GRAPHRAG_LLM_BASE_URL` | `https://ollama.com/v1` | Base URL of the LLM API for GraphRAG |
| `GRAPHRAG_LLM_MODEL` | `glm-5.2` | LLM model for Cypher generation and answers |
| `GRAPHRAG_LLM_MAX_TOKENS` | `8192` | Token limit for the LLM response |

`.env` is in `.gitignore` — credentials never go into git.

## `docker-compose.yml` — Neo4j Memory

| Variable | Value | Meaning |
|---|---|---|
| `NEO4J_server_memory_heap_initial__size` | `1G` | Initial heap size (transactions, queries) |
| `NEO4J_server_memory_heap_max__size` | `4G` | Max heap (grows under load) |
| `NEO4J_server_memory_pagecache_size` | `2G` | On-disk data cache |

Changes apply on `docker compose restart neo4j`. No image rebuild needed.

## Airing-parser (port 8567)

| Variable | Default | Meaning |
|---|---|---|
| `BATCH_SIZE` | `50` | DB query batch size |
| `CYCLE_INTERVAL_SEC` | `86400` | Pause between cycles (not used by coordinator) |
| `COORDINATOR_URL` | `http://coordinator:8000` | Coordinator URL (inside Docker) |
| `PYTHONPATH` | `/shared` | Path to base modules (mounted `/shared`) |

## User-anime (port 8568)

| Variable | Default | Meaning |
|---|---|---|
| `USER_STATS_BATCH_SIZE` | `50` | Batch size for stats pages |
| `COORDINATOR_URL` | `http://coordinator:8000` | Coordinator URL |
| `PYTHONPATH` | `/shared` | Path to base modules |

## User-user (port 8569)

| Variable | Default | Meaning |
|---|---|---|
| `USER_REFRESH_BATCH_SIZE` | `50` | Batch size for user refresh |
| `COORDINATOR_URL` | `http://coordinator:8000` | Coordinator URL |
| `PYTHONPATH` | `/shared` | Path to base modules |

## Coordinator (port 8570)

| Variable | Default | Meaning |
|---|---|---|
| `ANIME_PARSER_URL` | `http://airing-parser:8000` | airing-parser URL (inside Docker) |
| `USER_ANIME_URL` | `http://user-anime:8000` | user-anime URL (inside Docker) |
| `USER_USER_URL` | `http://user-user:8000` | user-user URL (inside Docker) |
| `ANIME_PARSER_TIME` | `03:00` | airing-parser start time (HH:MM) |
| `COORDINATOR_USER_SLICE_SEC` | `1800` | slice duration (sec, 30 min) |
| `COORDINATOR_IDLE_WAIT_SEC` | `300` | idle wait (sec) — fallback if DB gives no answer |
| `COORDINATOR_BATCH_SIZE` | `5` | batch size (how many items to send) |

## Airing-parser Endpoints (port 8567)

| Method | Path | Meaning |
|---|---|---|
| GET | `/status` | statistics (total, parsed, stubs, airing, seasons) |
| GET | `/stubs` | incomplete nodes — Anime with `title IS NULL` |
| POST | `/refresh/{mal_id}` | force-update a single title (direct `process_one` call) |
| POST | `/trigger-cycle` | run a cycle (mal_ids in body from coordinator) |
| GET | `/config` | current limits and intervals |
| GET | `/health` | health check |
| POST | `/pause` | stop after current item |
| POST | `/resume` | clear pause |
| GET | `/cycle-running` | cycle status |

### POST /refresh/{mal_id}

Updates a title immediately (direct `process_one` call), bypassing the queue.

```bash
curl -X POST http://localhost:8567/refresh/5249
```

## User-anime Endpoints (port 8568)

| Method | Path | Meaning |
|---|---|---|
| GET | `/status` | statistics (total_users, active_users, archived_users, total_ratings, anime_stats_checked, anime_stats_pending) |
| POST | `/trigger-cycle` | manually trigger a cycle (accepts mal_ids in body) |
| POST | `/scan-anime/{mal_id}` | scan a specific anime |
| POST | `/pause` | stop after current item |
| POST | `/resume` | clear pause |
| GET | `/cycle-running` | cycle status |
| GET | `/health` | health check |

## User-user Endpoints (port 8569)

| Method | Path | Meaning |
|---|---|---|
| GET | `/status` | statistics (total_users, active_users, archived_users, total_ratings) |
| POST | `/trigger-cycle` | manually trigger a cycle (accepts usernames in body) |
| POST | `/refresh-user/{username}` | refresh a specific user |
| POST | `/pause` | stop after current item |
| POST | `/resume` | clear pause |
| GET | `/cycle-running` | cycle status |
| GET | `/health` | health check |

## Coordinator Endpoints (port 8570)

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

## GraphRAG Endpoints (port 8666)

| Method | Path | Meaning |
|---|---|---|
| GET | `/api/chats` | list chats |
| POST | `/api/chats` | create a chat (`{"title": "..."}`) |
| DELETE | `/api/chats/{id}` | delete a chat |
| PUT | `/api/chats/{id}` | rename a chat |
| GET | `/api/chats/{id}/messages` | chat messages |
| POST | `/api/chats/{id}/ask` | ask the graph (`{"message": "..."}`) — returns `answer`, `cypher`, `status` (ok/empty/error/invalid/clarify), `rows`, `attempts`, `model`, `llm_base_url`, `duration_sec`, `cypher_raw` |
| GET | `/api/logs?limit=100` | Cypher query logs (model, answer, duration_sec, cypher_raw) |
| GET | `/api/health` | health check + current LLM model + llm_base_url |
| GET | `/logs` | logs web page (table with filter, stats) |
| GET | `/metrics` | Prometheus metrics (counters + duration summary) |

## Checking Neo4j Indexes

```bash
docker compose exec neo4j cypher-shell -u neo4j -p "$NEO4J_PASSWORD" \
  "SHOW CONSTRAINTS YIELD name, type, labelsOrTypes, properties
   RETURN name, type, labelsOrTypes, properties ORDER BY labelsOrTypes;"
```