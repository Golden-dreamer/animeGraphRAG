# Конфигурация

Настройки разделены на два уровня: `.env` (креды, порты, лимиты) и
`parsers/config.yaml` (правила обновления тайтлов). Все параметры `.env`
можно поменять без пересборки образа — достаточно `docker compose restart`.

## `.env` (корень проекта)

| Переменная | По умолчанию | Смысл |
|---|---|---|
| `NEO4J_PASSWORD` | — | пароль Neo4j (логин фиксирован — `neo4j`) |
| `PARSERS_PORT` | `8567` | порт FastAPI на хосте |
| `API_MIN_INTERVAL_SEC` | `0.5` | минимальный интервал между запросами (2/сек, запас от 3) |
| `API_RATE_WINDOW_SEC` | `60` | окно скользящего лимита (сек) |
| `API_RATE_WINDOW_MAX` | `55` | макс. запросов за окно (запас от 60) |
| `API_MAX_RETRIES` | `4` | количество ретраев при ошибке |
| `API_RETRY_BASE_DELAY` | `1.5` | базовая задержка бэкоффа (сек) |
| `API_RETRY_MAX_DELAY` | `30` | потолок бэкоффа (сек) |
| `API_HTTP_TIMEOUT` | `20` | таймаут HTTP-запроса (сек) |
| `MAL_BASE_URL` | `https://myanimelist.net` | базовый URL сайта |

`.env` в `.gitignore` — креды не попадают в git.

## `parsers/config.yaml`

| Параметр | Env-переменная | По умолчанию | Смысл |
|---|---|---|---|
| `batch_size` | `BATCH_SIZE` | `50` | размер порции для запроса к БД (не лимитирует API) |
| `cycle_interval_sec` | `CYCLE_INTERVAL_SEC` | `86400` | пауза между циклами (1 раз в день) |
| `max_attempts` | `MAX_ATTEMPTS` | `3` | retry до пометки `failed` |
| `retry_backoff_minutes` | `RETRY_BACKOFF_MINUTES` | `5` | пауза перед повтором после ошибки |
| `refresh_current_days` | — | `1` | как часто обновлять текущий/следующий сезон |
| `refresh_previous_days` | — | `7` | как часто обновлять прошлый сезон |
| `refresh_recent_years` | — | `3` | тайтлы младше — "относительно новые" |
| `refresh_recent_days` | — | `365` | как часто обновлять "относительно новые" |

## FastAPI эндпоинты

| Метод | Путь | Смысл |
|---|---|---|
| GET | `/status` | статистика (total, parsed, stubs, airing, seasons) |
| GET | `/stubs` | неполные узлы — Anime с title IS NULL |
| POST | `/refresh/{mal_id}` | принудительно обновить один тайтл (прямой вызов) |
| POST | `/trigger-cycle` | запустить цикл scheduler прямо сейчас (discover + due-очередь) |
| PUT | `/schedule` | изменить интервал автоматического цикла (`cycle_interval_sec`) |
| GET | `/config` | текущие лимиты, интервалы |
| GET | `/health` | проверка живости |

### POST /trigger-cycle

Запускает цикл scheduler немедленно, не дожидаясь таймера. Выполняет
discover (текущий/следующий/прошлый сезон) и обрабатывает все due-тайтлы.
Влияет на все три актуальных сезона, включая прошлый (который обычно
обновляется раз в неделю). Если цикл уже выполняется — возвращает 409.

```bash
curl -X POST http://localhost:8567/trigger-cycle
```

### PUT /schedule

Меняет `cycle_interval_sec` — как часто scheduler запускается автоматически.
Минимум 60 секунд. Изменение применяется немедленно и действует до
перезапуска контейнера (для постоянного изменения — отредактируйте `config.yaml`).

```bash
curl -X PUT http://localhost:8567/schedule \
  -H "Content-Type: application/json" \
  -d '{"cycle_interval_sec": 3600}'
```

## Настройки Neo4j (docker-compose.yml)

| Переменная | Значение | Смысл |
|---|---|---|
| `NEO4J_server_memory_heap_initial__size` | `1G` | Начальный размер heap (транзакции, запросы) |
| `NEO4J_server_memory_heap_max__size` | `4G` | Максимум heap (растёт при нагрузке) |
| `NEO4J_server_memory_pagecache_size` | `2G` | Кэш данных на диске (весь 905MB граф помещается с запасом) |

Изменения применяются при `docker compose restart neo4j` (или полном
`docker compose up -d`). Не требуют пересборки образа.

### Индексы и констрейнты

Созданы через Cypher (см. [data-model.md](data-model.md)). Констрейнты
хранятся в самой БД Neo4j — не теряются при перезапуске. Для проверки:

```bash
docker compose exec neo4j cypher-shell -u neo4j -p "$NEO4J_PASSWORD" \
  "SHOW CONSTRAINTS YIELD name, type, labelsOrTypes, properties
   RETURN name, type, labelsOrTypes, properties ORDER BY labelsOrTypes;"
```

## Кэш

Файловый кэш HTML-страниц удалён в v7 (бессмертный кэш без TTL скрывал
обновления MAL). Все HTTP-запросы идут напрямую на myanimelist.net с
ретраями и лимитами (0.5s интервал, 55 req/мин). Резюмируемость
обеспечивается SQLite (status='ok', next_check_at).

Папка parsers/cache/ (5.9 GB) — удалить вручную:
```bash
chmod -R u+w parsers/cache && rm -rf parsers/cache
```