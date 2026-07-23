# Конфигурация

Настройки разделены на два уровня: `.env` (креды, порты, лимиты API) и
`docker-compose.yml` (память Neo4j, порты сервисов). Все параметры `.env`
можно менять без пересборки образа — достаточно `docker compose restart`.
`config.yaml` удалён — все настройки парсеров через env-переменные.

## `.env` (корень проекта)

| Переменная | По умолчанию | Смысл |
|---|---|---|
| `NEO4J_PASSWORD` | — | пароль Neo4j (логин фиксирован — `neo4j`) |
| `PARSERS_PORT` | `8567` | порт airing-parser на хосте |
| `USER_ANIME_PORT` | `8568` | порт user-anime на хосте |
| `USER_USER_PORT` | `8569` | порт user-user на хосте |
| `COORDINATOR_PORT` | `8570` | порт coordinator на хосте |
| `API_MIN_INTERVAL_SEC` | `0.5` | минимальный интервал между запросами (2/сек, запас от 3) |
| `API_RATE_WINDOW_SEC` | `60` | окно скользящего лимита (сек) |
| `API_RATE_WINDOW_MAX` | `55` | макс. запросов за окно (запас от 60) |
| `API_MAX_RETRIES` | `4` | количество ретраев при ошибке |
| `API_RETRY_BASE_DELAY` | `1.5` | базовая задержка бэкоффа (сек) |
| `API_RETRY_MAX_DELAY` | `30` | потолок бэкоффа (сек) |
| `API_HTTP_TIMEOUT` | `20` | таймаут HTTP-запроса (сек) |
| `MAL_BASE_URL` | `https://myanimelist.net` | базовый URL сайта |
| `TZ` | `Europe/Moscow` | таймзона для всех контейнеров |
| `OLLAMA_API_KEY` | — | API-ключ для LLM GraphRAG (OpenAI-compatible) |
| `GRAPHRAG_LLM_BASE_URL` | `https://ollama.com/v1` | базовый URL LLM API для GraphRAG |
| `GRAPHRAG_LLM_MODEL` | `glm-5.2` | модель LLM для генерации Cypher и ответов |
| `GRAPHRAG_LLM_MAX_TOKENS` | `8192` | лимит токенов для ответа LLM |

`.env` в `.gitignore` — креды не попадают в git.

## `docker-compose.yml` — память Neo4j

| Переменная | Значение | Смысл |
|---|---|---|
| `NEO4J_server_memory_heap_initial__size` | `1G` | Начальный размер heap (транзакции, запросы) |
| `NEO4J_server_memory_heap_max__size` | `4G` | Максимум heap (растёт при нагрузке) |
| `NEO4J_server_memory_pagecache_size` | `2G` | Кэш данных на диске |

Изменения применяются при `docker compose restart neo4j`. Не требуют
пересборки образа.

## Airing-parser (порт 8567)

| Переменная | По умолчанию | Смысл |
|---|---|---|
| `BATCH_SIZE` | `50` | размер порции для запроса к БД |
| `CYCLE_INTERVAL_SEC` | `86400` | пауза между циклами (не используется координатором) |
| `COORDINATOR_URL` | `http://coordinator:8000` | URL координатора (внутри Docker) |
| `PYTHONPATH` | `/shared` | путь к base-модулям (monтировка `/shared`) |

## User-anime (порт 8568)

| Переменная | По умолчанию | Смысл |
|---|---|---|
| `USER_STATS_BATCH_SIZE` | `50` | размер батча для stats-страниц |
| `COORDINATOR_URL` | `http://coordinator:8000` | URL координатора |
| `PYTHONPATH` | `/shared` | путь к base-модулям |

## User-user (порт 8569)

| Переменная | По умолчанию | Смысл |
|---|---|---|
| `USER_REFRESH_BATCH_SIZE` | `50` | размер батча для обновления юзеров |
| `COORDINATOR_URL` | `http://coordinator:8000` | URL координатора |
| `PYTHONPATH` | `/shared` | путь к base-модулям |

## Координатор (порт 8570)

| Переменная | По умолчанию | Смысл |
|---|---|---|
| `ANIME_PARSER_URL` | `http://airing-parser:8000` | URL airing-parser (внутри Docker) |
| `USER_ANIME_URL` | `http://user-anime:8000` | URL user-anime (внутри Docker) |
| `USER_USER_URL` | `http://user-user:8000` | URL user-user (внутри Docker) |
| `ANIME_PARSER_TIME` | `03:00` | время запуска airing-parser (HH:MM) |
| `COORDINATOR_USER_SLICE_SEC` | `1800` | длительность слайса (сек, 30 мин) |
| `COORDINATOR_IDLE_WAIT_SEC` | `300` | idle wait (сек) — fallback если БД не отвечает |
| `COORDINATOR_BATCH_SIZE` | `5` | размер батча (сколько элементов отправить) |

## Airing-parser эндпоинты (порт 8567)

| Метод | Путь | Смысл |
|---|---|---|
| GET | `/status` | статистика (total, parsed, stubs, airing, seasons) |
| GET | `/stubs` | неполные узлы — Anime с `title IS NULL` |
| POST | `/refresh/{mal_id}` | принудительно обновить один тайтл (прямой вызов `process_one`) |
| POST | `/trigger-cycle` | запустить цикл (mal_ids в body от координатора) |
| GET | `/config` | текущие лимиты, интервалы |
| GET | `/health` | проверка живости |
| POST | `/pause` | остановить после текущего элемента |
| POST | `/resume` | снять паузу |
| GET | `/cycle-running` | статус цикла |

### POST /refresh/{mal_id}

Обновляет тайтл прямо сейчас (прямой вызов `process_one`), без очереди.

```bash
curl -X POST http://localhost:8567/refresh/5249
```

## User-anime эндпоинты (порт 8568)

| Метод | Путь | Смысл |
|---|---|---|
| GET | `/status` | статистика (total_users, active_users, archived_users, total_ratings, anime_stats_checked, anime_stats_pending) |
| POST | `/trigger-cycle` | ручной запуск цикла (mal_ids в body) |
| POST | `/scan-anime/{mal_id}` | сканировать конкретное аниме |
| POST | `/pause` | остановить после текущего элемента |
| POST | `/resume` | снять паузу |
| GET | `/cycle-running` | статус цикла |
| GET | `/health` | проверка живости |

## User-user эндпоинты (порт 8569)

| Метод | Путь | Смысл |
|---|---|---|
| GET | `/status` | статистика (total_users, active_users, archived_users, total_ratings) |
| POST | `/trigger-cycle` | ручной запуск цикла (usernames в body) |
| POST | `/refresh-user/{username}` | обновить конкретного пользователя |
| POST | `/pause` | остановить после текущего элемента |
| POST | `/resume` | снять паузу |
| GET | `/cycle-running` | статус цикла |
| GET | `/health` | проверка живости |

## Координатор эндпоинты (порт 8570)

| Метод | Путь | Смысл |
|---|---|---|
| GET | `/` | статус всех парсеров + auto_mode |
| POST | `/start/anime` | запустить airing-parser, остановить остальные |
| POST | `/start/user-anime` | запустить user-anime, остановить остальные |
| POST | `/start/user-user` | запустить user-user, остановить остальные |
| POST | `/pause` | остановить все |
| POST | `/auto` | авто-режим (чередование по слайсам) |
| POST | `/auto/stop` | остановить авто-режим |
| GET | `/auto/status` | статус авто-режима |
| PUT | `/auto/slice` | изменить длительность слайса (сек) |
| PUT | `/auto/batch-size` | изменить размер батча |
| PUT | `/anime-time` | изменить время запуска airing-parser (HH:MM) |
| PUT | `/auto/idle-wait` | изменить idle wait (сек) |

## GraphRAG эндпоинты (порт 8666)

| Метод | Путь | Смысл |
|---|---|---|
| GET | `/api/chats` | список чатов |
| POST | `/api/chats` | создать чат (`{"title": "..."}`) |
| DELETE | `/api/chats/{id}` | удалить чат |
| PUT | `/api/chats/{id}` | переименовать чат |
| GET | `/api/chats/{id}/messages` | сообщения чата |
| POST | `/api/chats/{id}/ask` | вопрос к графу (`{"message": "..."}`) — возвращает `answer`, `cypher`, `status` (ok/empty/error/invalid/clarify), `rows`, `attempts`, `model`, `llm_base_url`, `duration_sec`, `cypher_raw` |
| GET | `/api/logs?limit=100` | логи Cypher-запросов (model, answer, duration_sec, cypher_raw) |
| GET | `/api/health` | проверка живости + текущая модель LLM + llm_base_url |
| GET | `/logs` | веб-страница логов (таблица с фильтром, статами) |
| GET | `/metrics` | Prometheus-метрики (counters + duration summary) |

## Проверка индексов Neo4j

```bash
docker compose exec neo4j cypher-shell -u neo4j -p "$NEO4J_PASSWORD" \
  "SHOW CONSTRAINTS YIELD name, type, labelsOrTypes, properties
   RETURN name, type, labelsOrTypes, properties ORDER BY labelsOrTypes;"
```