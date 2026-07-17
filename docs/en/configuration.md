# Configuration

Settings are split across three levels: `.env` (credentials, ports, API
limits), `parsers/config.yaml` (scheduler parameters), `docker-compose.yml`
(Neo4j memory). All `.env` and `config.yaml` parameters can be changed
without rebuilding the image — just `docker compose restart`.

## `.env` (project root)

| Variable | Default | Meaning |
|---|---|---|
| `NEO4J_PASSWORD` | — | Neo4j password (login is fixed — `neo4j`) |
| `PARSERS_PORT` | `8567` | FastAPI port on the host |
| `API_MIN_INTERVAL_SEC` | `0.5` | Minimum interval between requests (2/sec, margin under 3) |
| `API_RATE_WINDOW_SEC` | `60` | Sliding limit window (seconds) |
| `API_RATE_WINDOW_MAX` | `55` | Max requests per window (margin under 60) |
| `API_MAX_RETRIES` | `4` | Number of retries on error |
| `API_RETRY_BASE_DELAY` | `1.5` | Base backoff delay (seconds) |
| `API_RETRY_MAX_DELAY` | `30` | Backoff ceiling (seconds) |
| `API_HTTP_TIMEOUT` | `20` | HTTP request timeout (seconds) |
| `MAL_BASE_URL` | `https://myanimelist.net` | Base site URL |
| `OLLAMA_API_KEY` | — | API key for the GraphRAG LLM (OpenAI-compatible) |
| `GRAPHRAG_LLM_BASE_URL` | `https://ollama.com/v1` | Base URL of the LLM API for GraphRAG |
| `GRAPHRAG_LLM_MODEL` | `glm-5.2` | LLM model for Cypher generation and answers |
| `GRAPHRAG_LLM_MAX_TOKENS` | `8192` | Token limit for the LLM response |

`.env` is in `.gitignore` — credentials never go into git.

## `parsers/config.yaml`

| Parameter | Env variable | Default | Meaning |
|---|---|---|---|
| `batch_size` | `BATCH_SIZE` | `50` | DB query batch size in bootstrap |
| `cycle_interval_sec` | `CYCLE_INTERVAL_SEC` | `86400` | Pause between scheduler cycles (once a day) |
| `request_delay_sec` | `REQUEST_DELAY_SEC` | `1.2` | Passed to the fetcher but unused (rate limiting is handled by internal mechanisms) |

## `docker-compose.yml` — Neo4j Memory

| Variable | Value | Meaning |
|---|---|---|
| `NEO4J_server_memory_heap_initial__size` | `1G` | Initial heap size (transactions, queries) |
| `NEO4J_server_memory_heap_max__size` | `4G` | Max heap (grows under load) |
| `NEO4J_server_memory_pagecache_size` | `2G` | On-disk data cache |

Changes apply on `docker compose restart neo4j`. No image rebuild needed.

## FastAPI Endpoints

| Method | Path | Meaning |
|---|---|---|
| GET | `/status` | statistics (total, parsed, stubs, airing, seasons) |
| GET | `/stubs` | incomplete nodes — Anime with `title IS NULL` |
| POST | `/refresh/{mal_id}` | force-update a single title (direct `process_one` call) |
| POST | `/trigger-cycle` | run a scheduler cycle now (discover + due queue) |
| PUT | `/schedule` | change the automatic cycle interval (`cycle_interval_sec`) |
| GET | `/config` | current limits and intervals |
| GET | `/health` | health check |

### POST /trigger-cycle

Triggers a scheduler cycle immediately. Runs discover (current/next/
previous season) and processes all due titles. If a cycle is already
running — returns 409.

```bash
curl -X POST http://localhost:8567/trigger-cycle
```

### PUT /schedule

Changes `cycle_interval_sec`. Minimum 60 seconds. The change takes effect
immediately and persists until container restart (for a permanent change —
edit `config.yaml`).

```bash
curl -X PUT http://localhost:8567/schedule \
  -H "Content-Type: application/json" \
  -d '{"cycle_interval_sec": 3600}'
```

### POST /refresh/{mal_id}

Updates a title immediately (direct `process_one` call), bypassing the queue.
Useful when: an archived title's rating changed; a title is stuck as a stub;
data is clearly stale.

```bash
curl -X POST http://localhost:8567/refresh/5249
```

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