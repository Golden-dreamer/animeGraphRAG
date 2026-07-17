# Operations

## Normal Startup

```bash
docker compose up -d --build
```

The scheduler runs on its own: every `cycle_interval_sec` (default 86400 —
once a day) it registers new titles for the 3 current seasons and processes
all due titles. MyAnimeList rate limits (0.5s between requests, 55 req/min)
are enforced inside `fetcher.py` automatically.

The default FastAPI port is `8567` (changeable via `PARSERS_PORT` in `.env`).

## Initial Archive Population (bootstrap)

Iterates over all seasons from 1917 to the current one (except current/
next/previous — those are handled by the scheduler). Season progress is
tracked in the file `bootstrap_progress.txt` (last processed season).
Titles within a season are tracked via `title IS NULL`: unprocessed ones
go into `select_due_for_season`, processed ones are skipped.

```bash
docker compose run --rm parsers python bootstrap.py
```

In the background (so you can close the terminal):

```bash
docker compose run -d --name bootstrap parsers python bootstrap.py
```

Watch progress:
```bash
docker logs -f bootstrap
```

Stop (safely — progress is saved):
```bash
docker stop bootstrap && docker rm bootstrap
```

To resume from the same point — run the same command again. It will not:
- re-process titles that already have `title` set;
- re-scan seasons before the checkpoint in `bootstrap_progress.txt`.

To start bootstrap from scratch — delete `parsers/bootstrap_progress.txt`.

If `:Season` nodes from previous versions (pre-v9) remain in the database,
they can be removed:
```cypher
MATCH (s:Season) DETACH DELETE s
```

## Monitoring

```bash
curl http://localhost:8567/status
```

Example response:

```json
{
  "total_anime": 20392,
  "parsed": 20243,
  "unprocessed_stubs": 149,
  "currently_airing": 257,
  "not_yet_aired": 57
}
```

- `total_anime` — total `:Anime` nodes in the graph.
- `parsed` — processed (`title IS NOT NULL`).
- `unprocessed_stubs` — not processed (`title IS NULL`). The scheduler
  will retry them in the next cycle.
- `currently_airing` / `not_yet_aired` — `mal_status` from MAL.

### Incomplete Nodes (stubs)

```bash
curl http://localhost:8567/stubs
```

Force-update a single title:

```bash
curl -X POST http://localhost:8567/refresh/{mal_id}
```

Or trigger a full scheduler cycle:

```bash
curl -X POST http://localhost:8567/trigger-cycle
```

## GraphRAG

A web interface for querying the graph in natural language. Port 8666.

```bash
docker compose up -d graphrag
```

URL: `http://localhost:8666`

Pipeline: question → LLM generates Cypher → Neo4j → LLM formulates the answer.
The Cypher query is shown in the UI (collapsible block under the answer).

Logs of all queries (model, status, Cypher, answer, raw LLM output,
duration, number of attempts) — via the web interface:

```
http://localhost:8666/logs
```

Or via the API:

```bash
curl http://localhost:8666/api/logs?limit=100
```

Health check (model + LLM URL):

```bash
curl http://localhost:8666/api/health
```

Prometheus metrics (request counters, duration):

```bash
curl http://localhost:8666/metrics
```

## What Happens on Error

1. An error while processing a title (network, parsing, anything) → logged,
   the title remains a stub (`title IS NULL`).
2. The scheduler picks up all Currently Airing + Not yet aired + stubs
   again in the next cycle and retries.
3. For archived titles: `/refresh/{mal_id}` for a forced update,
   or restart bootstrap.
4. No retry counters, failed statuses, or next_check_at —
   simply "no title → try again".

## Viewing the Graph

Neo4j Browser: `http://<IP>:7474` (login `neo4j`, password from `.env`).
Ports in `docker-compose.yml` are not bound to `127.0.0.1` — access works
from any device on the local network.

On `:Anime` nodes, the `title` property (an alias of `title_original`) is
displayed. For existing nodes without `title` — a one-time command in
Neo4j Browser:

```cypher
MATCH (a:Anime) WHERE a.title IS NULL AND a.title_original IS NOT NULL
SET a.title = a.title_original
```

## Changing Parameters Without Rebuilding the Image

`docker-compose.yml` mounts `./parsers` into the container as a volume,
so edits to `config.yaml` (or any `.py` file) only require a container
restart, without `--build`:

```bash
docker compose restart parsers
```

## Managing Scheduler Cycles via API

### Trigger a Cycle Now

```bash
curl -X POST http://localhost:8567/trigger-cycle
```

If a cycle is already running — returns 409 Conflict.

### Change the Automatic Cycle Interval

```bash
curl -X PUT http://localhost:8567/schedule \
  -H "Content-Type: application/json" \
  -d '{"cycle_interval_sec": 3600}'
```

Minimum 60 seconds. Takes effect until container restart. For a permanent
change — edit `config.yaml`.

## Utility Scripts

### Backfilling Staff

If the database was populated before v5, staff for most anime is incomplete:

```bash
docker compose run --rm parsers python update_staff.py
docker compose run --rm parsers python update_staff.py --limit 100
docker compose run --rm parsers python update_staff.py --threshold 4
```

Iterates over all anime with staff <= threshold (default 4), fetches the
`/characters` page with the correct URL, updates relationships via
`upsert_staff_only`.

### Backfilling Missed Titles

```bash
docker compose run --rm parsers python check_missing.py          # current seasons
docker compose run --rm parsers python check_missing.py --all    # all seasons
docker compose run --rm parsers python check_missing.py --season 2006 summer
```

Cross-checks MAL season pages against Neo4j and adds missing titles as stubs.
The scheduler will process them in the next cycle (or immediately via
`POST /trigger-cycle`).