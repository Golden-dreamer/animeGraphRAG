# Operations

## Normal Startup

```bash
docker compose up -d --build
```

The coordinator manages the parsers. By default, auto-mode starts:
alternating user-anime/user-user (30 min slices) and airing-parser
(by time, 03:00). Parsers are passive — they do not start their own
background loops. MyAnimeList rate limits (0.5s between requests, 55
req/min) are enforced inside `fetcher.py` automatically.

Ports: airing-parser — 8567, user-anime — 8568, user-user — 8569,
coordinator — 8570 (changeable via `PARSERS_PORT`, `USER_ANIME_PORT`,
`USER_USER_PORT`, `COORDINATOR_PORT` in `.env`).

## Initial Archive Population (bootstrap)

Iterates over all seasons from 1917 to the current one (except current/
next/previous — those are handled by the scheduler). Season progress is
tracked in the file `parsers/anime/bootstrap_progress.txt` (last processed
season). Titles within a season are tracked via `title IS NULL`:
unprocessed ones go into `select_due_for_season`, processed ones are skipped.

```bash
docker compose run --rm airing-parser python bootstrap.py
```

In the background (so you can close the terminal):

```bash
docker compose run -d --name bootstrap airing-parser python bootstrap.py
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

To start bootstrap from scratch — delete `parsers/anime/bootstrap_progress.txt`.

If `:Season` nodes from previous versions (pre-v9) remain in the database,
they can be removed:
```cypher
MATCH (s:Season) DETACH DELETE s
```

## Monitoring

### Airing-parser (port 8567)

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

Incomplete nodes (stubs):

```bash
curl http://localhost:8567/stubs
```

Force-update a single title:

```bash
curl -X POST http://localhost:8567/refresh/{mal_id}
```

### Coordinator (port 8570)

Status of all parsers + auto_mode:

```bash
curl http://localhost:8570/
```

Start auto-mode (alternating user-anime ↔ user-user ↔ airing-parser):

```bash
curl -X POST http://localhost:8570/auto
```

Stop auto-mode:

```bash
curl -X POST http://localhost:8570/auto/stop
```

Auto-mode status:

```bash
curl http://localhost:8570/auto/status
```

Change slice duration (sec):

```bash
curl -X PUT http://localhost:8570/auto/slice \
  -H "Content-Type: application/json" \
  -d '{"slice_sec": 900}'
```

Manual control (without auto-mode):

```bash
curl -X POST http://localhost:8570/start/anime        # airing-parser, stop others
curl -X POST http://localhost:8570/start/user-anime   # user-anime, stop others
curl -X POST http://localhost:8570/start/user-user    # user-user, stop others
curl -X POST http://localhost:8570/pause               # stop all
```

### User-anime (port 8568)

MyAnimeList user parser (anime-centric: stats pages). Runs as a separate
`user-anime` container.

```bash
docker compose up -d user-anime
```

Monitoring:

```bash
curl http://localhost:8568/status
```

Manual cycle trigger:

```bash
curl -X POST http://localhost:8568/trigger-cycle
```

Scan a specific anime:

```bash
curl -X POST http://localhost:8568/scan-anime/5249
```

### User-user (port 8569)

MyAnimeList user parser (user-centric: animelist refresh). Runs as a
separate `user-user` container.

```bash
docker compose up -d user-user
```

Monitoring:

```bash
curl http://localhost:8569/status
```

Manual cycle trigger:

```bash
curl -X POST http://localhost:8569/trigger-cycle
```

Refresh a specific user:

```bash
curl -X POST http://localhost:8569/refresh-user/someuser
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
2. The coordinator picks up all Currently Airing + Not yet aired + stubs
   again in the next cycle and passes them to the parser.
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

`docker-compose.yml` mounts `./parsers/anime` (airing-parser) and
`./parsers/user_anime` + `./parsers/user_user` (user parsers) into the
containers as volumes, so edits to any `.py` file only require a container
restart, without `--build`:

```bash
docker compose restart airing-parser
docker compose restart user-anime
docker compose restart user-user
```

## Utility Scripts

### Backfilling Missed Titles

```bash
docker compose run --rm airing-parser python check_missing.py          # current seasons
docker compose run --rm airing-parser python check_missing.py --all    # all seasons (1917→)
docker compose run --rm airing-parser python check_missing.py --season 2006 summer
```

Cross-checks MAL season pages against Neo4j and adds missing titles as stubs.
The coordinator will process them in the next cycle (or immediately via
`POST /trigger-cycle`).