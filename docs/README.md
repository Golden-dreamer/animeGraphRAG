# Документация

- [architecture.md](./architecture.md) — из чего состоит проект, как модули связаны
- [data-model.md](./data-model.md) — схема графа Neo4j: узлы, связи, индексы
- [configuration.md](./configuration.md) — справочник по параметрам (.env, config.yaml, docker-compose.yml) и API-эндпоинтам
- [operations.md](./operations.md) — запуск, мониторинг, диагностика, утилитные скрипты
- [changelog.md](./changelog.md) — история значимых изменений
- [popular_cypher_commands.md](./popular_cypher_commands.md) — примеры Cypher-запросов к графу

Стек: Python 3.12, FastAPI, Neo4j 5, BeautifulSoup4, Docker Compose.
Источник данных: MyAnimeList (прямой HTML-скрапинг, без сторонних API).
Единственная БД: Neo4j (граф + состояние очереди).