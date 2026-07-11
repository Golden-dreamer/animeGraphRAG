# Документация

- [architecture.md](./architecture.md) — из чего состоит проект, как модули друг с другом связаны
- [data-model.md](./data-model.md) — схема SQLite и графа Neo4j
- [operations.md](./operations.md) — как запускать, останавливать, диагностировать проблемы
- [configuration.md](./configuration.md) — справочник по всем параметрам `config.yaml`
- [changelog.md](./changelog.md) — история значимых изменений

Статус: **GraphRAG-парсер MyAnimeList**. Прямой HTML-скрапинг (без Jikan API),
полная модель данных (аниме, персонажи, voice actors, staff, related entries,
ресурсы, стриминг), работает локально через `docker compose up`.
API: ручной запуск цикла (`/trigger-cycle`), управление интервалом (`/schedule`),
скрипты дополнения staff (`update_staff.py`) и пропущенных тайтлов (`check_missing.py`).