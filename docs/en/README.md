# Documentation

- [architecture.md](./architecture.md) — project structure, how modules connect
- [data-model.md](./data-model.md) — Neo4j graph schema: nodes, relationships, indexes
- [configuration.md](./configuration.md) — reference for parameters (.env, config.yaml, docker-compose.yml) and API endpoints
- [operations.md](./operations.md) — startup, monitoring, diagnostics, utility scripts
- [changelog.md](./changelog.md) — history of significant changes
- [popular_cypher_commands.md](./popular_cypher_commands.md) — example Cypher queries for the graph

Stack: Python 3.12, FastAPI, Neo4j 5, BeautifulSoup4, Docker Compose.
Data source: MyAnimeList (direct HTML scraping, no third-party API).
Single DB: Neo4j (graph + queue state).

Modules:
- `parsers/anime/` — airing anime parser (`airing-parser` container, port 8567)
- `parsers/user_anime/` — MyAnimeList user parser, anime-centric (stats pages) (`user-anime` container, port 8568)
- `parsers/user_user/` — MyAnimeList user parser, user-centric (animelist refresh) (`user-user` container, port 8569)
- `parsers/coordinator_app.py` — coordinator (`coordinator` container, port 8570)
- `backend/` — GraphRAG (`graphrag` container, port 8666)