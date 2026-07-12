# Конфигурация

Настройки разделены на три уровня: `.env` (креды, порты, лимиты API),
`parsers/config.yaml` (параметры scheduler), `docker-compose.yml` (память
Neo4j). Все параметры `.env` и `config.yaml` можно менять без пересборки
образа — достаточно `docker compose restart`.

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
| `OLLAMA_API_KEY` | — | API-ключ для LLM GraphRAG (OpenAI-compatible) |
| `GRAPHRAG_LLM_BASE_URL` | `https://ollama.com/v1` | базовый URL LLM API для GraphRAG |
| `GRAPHRAG_LLM_MODEL` | `glm-5.2` | модель LLM для генерации Cypher и ответов |
| `GRAPHRAG_LLM_MAX_TOKENS` | `8192` | лимит токенов для ответа LLM |

`.env` в `.gitignore` — креды не попадают в git.

## `parsers/config.yaml`

| Параметр | Env-переменная | По умолчанию | Смысл |
|---|---|---|---|
| `batch_size` | `BATCH_SIZE` | `50` | размер порции для запроса к БД в bootstrap |
| `cycle_interval_sec` | `CYCLE_INTERVAL_SEC` | `86400` | пауза между циклами scheduler (1 раз в день) |
| `request_delay_sec` | `REQUEST_DELAY_SEC` | `1.2` | параметр передаётся в fetcher, но не используется (рейт-лимит управляется внутренними механизмами) |

## `docker-compose.yml` — память Neo4j

| Переменная | Значение | Смысл |
|---|---|---|
| `NEO4J_server_memory_heap_initial__size` | `1G` | Начальный размер heap (транзакции, запросы) |
| `NEO4J_server_memory_heap_max__size` | `4G` | Максимум heap (растёт при нагрузке) |
| `NEO4J_server_memory_pagecache_size` | `2G` | Кэш данных на диске |

Изменения применяются при `docker compose restart neo4j`. Не требуют
пересборки образа.

## FastAPI эндпоинты

| Метод | Путь | Смысл |
|---|---|---|
| GET | `/status` | статистика (total, parsed, stubs, airing, seasons) |
| GET | `/stubs` | неполные узлы — Anime с `title IS NULL` |
| POST | `/refresh/{mal_id}` | принудительно обновить один тайтл (прямой вызов `process_one`) |
| POST | `/trigger-cycle` | запустить цикл scheduler прямо сейчас (discover + due-очередь) |
| PUT | `/schedule` | изменить интервал автоматического цикла (`cycle_interval_sec`) |
| GET | `/config` | текущие лимиты, интервалы |
| GET | `/health` | проверка живости |

### POST /trigger-cycle

Запускает цикл scheduler немедленно. Выполняет discover (текущий/
следующий/прошлый сезон) и обрабатывает все due-тайтлы. Если цикл уже
выполняется — возвращает 409.

```bash
curl -X POST http://localhost:8567/trigger-cycle
```

### PUT /schedule

Меняет `cycle_interval_sec`. Минимум 60 секунд. Изменение применяется
немедленно и действует до перезапуска контейнера (для постоянного
изменения — отредактируйте `config.yaml`).

```bash
curl -X PUT http://localhost:8567/schedule \
  -H "Content-Type: application/json" \
  -d '{"cycle_interval_sec": 3600}'
```

### POST /refresh/{mal_id}

Обновляет тайтл прямо сейчас (прямой вызов `process_one`), без очереди.
Полезно: изменился рейтинг у архивного тайтла; тайтл остался stub;
данные явно устарели.

```bash
curl -X POST http://localhost:8567/refresh/5249
```

## GraphRAG эндпоинты (порт 8666)

| Метод | Путь | Смысл |
|---|---|---|
| GET | `/api/chats` | список чатов |
| POST | `/api/chats` | создать чат (`{"title": "..."}`) |
| DELETE | `/api/chats/{id}` | удалить чат |
| PUT | `/api/chats/{id}` | переименовать чат |
| GET | `/api/chats/{id}/messages` | сообщения чата |
| POST | `/api/chats/{id}/ask` | вопрос к графу (`{"message": "..."}`) — возвращает `answer`, `cypher`, `status`, `rows`, `attempts` |
| GET | `/api/logs?limit=50` | логи Cypher-запросов |
| GET | `/api/health` | проверка живости + текущая модель LLM |

## Проверка индексов Neo4j

```bash
docker compose exec neo4j cypher-shell -u neo4j -p "$NEO4J_PASSWORD" \
  "SHOW CONSTRAINTS YIELD name, type, labelsOrTypes, properties
   RETURN name, type, labelsOrTypes, properties ORDER BY labelsOrTypes;"
```