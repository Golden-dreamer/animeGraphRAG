# Архитектура

## Общая схема

Структура репозитория: `parsers/anime/` — airing-parser, `parsers/user_anime/`
— user-anime (anime-centric), `parsers/user_user/` — user-user (user-centric),
`parsers/base_parser.py` — базовый класс всех парсеров, `parsers/coordinator_app.py`
— координатор в корне `parsers/`.

**Координатор — главный управляющий.** Парсеры пассивны: не запускают
свой scheduler_loop при старте. Запуск только через координатор
(`POST /trigger-cycle`). Координатор — умный: сам запрашивает Neo4j,
формирует списки mal_ids / usernames и передаёт парсерам через body
запроса. Парсеры — глупые: просто парсят то, что дали. Если работы нет
(список пуст) — координатор не дёргает парсер, а ждёт и проверяет снова.
Координатор чередует: user-anime (слайс) → airing-parser (по времени) →
user-user (слайс) → airing-parser → ...

```
┌──────────────────────────── контейнер coordinator (порт 8570) ────────────┐
│                                                                              │
│   coordinator_app.py (FastAPI)                                            │
│        ├── авто-режим: чередование user-anime/user-user (слайсы) ↔         │
│        │   airing-parser (по времени, по умолчанию 03:00)                   │
│        ├── координатор сам запрашивает Neo4j (_select_*), формирует батчи   │
│        ├── PUT /auto/slice — длительность слайса (сек, USER_SLICE_SEC)      │
│        ├── PUT /auto/batch-size — размер батча (BATCH_SIZE)                 │
│        ├── PUT /anime-time — время запуска airing-parser (HH:MM)            │
│        ├── PUT /auto/idle-wait — idle wait (сек)                            │
│        └── HTTP-эндпоинты: /, /start/anime, /start/user-anime,              │
│            /start/user-user, /pause, /auto, /auto/stop, /auto/status        │
│                                                                              │
│   Dockerfile + requirements.txt — для координатора                         │
│                                                                              │
└──────────────────────────────────────────────────────────────────────────┘
        │ управляет
        ▼
┌─────────────────── контейнер airing-parser (порт 8567) ─────────────────┐
│   app.py (FastAPI, BaseParser) — пассивен, запускает цикл только по       │
│   /trigger-cycle (принимает mal_ids от координатора в body)                │
│        └── HTTP-эндпоинты: /refresh/{id}, /trigger-cycle, /status,         │
│            /stubs, /config, /health, /pause, /resume, /cycle-running       │
│   bootstrap.py — запускается ОТДЕЛЬНО, вручную, один раз                   │
│        └── по всем историческим сезонам (1917 → сейчас, кроме текущих 3)   │
│            graph_state.select_due_for_season() → processing.process_one() × N   │
└──────────────────────────────────────────────────────────────────────────┘
        │
        ▼
Neo4j (контейнер neo4j)
  граф: Anime/Genre/Studio/Producer/Person/Character/
        ExternalLink/StreamingPlatform/Manga/User
  состояние очереди: title IS NULL = не обработан
  прогресс bootstrap: файл bootstrap_progress.txt (на хосте через volume)
```

## User Parsers (контейнер user-anime `parsers/user_anime/` порт 8568, user-user `parsers/user_user/` порт 8569)

```
┌──────────────────────────── контейнер user-anime ──────────────────────────┐
│   user_anime/app.py (FastAPI, BaseParser) — пассивен, запускает цикл       │
│   только по /trigger-cycle (принимает mal_ids от координатора в body)       │
│        ├── scheduler.run_cycle(mal_ids, cfg, is_paused)                     │
│        │   для каждого mal_id:                                              │
│        │     фетчит stats-страницы → парсит → loader.upsert                  │
│        │     → сбор "Recently Updated By" (75 юзеров/стр, ~100 стр)          │
│        │     → MERGE :User + RATED, адаптивный backoff                       │
│        │     pause проверяется между элементами и между страницами stats     │
│        │                                                                    │
│        └── HTTP-эндпоинты: /status, /trigger-cycle, /scan-anime/{id},        │
│            /pause, /resume, /cycle-running, /health                         │
│                                                                              │
│   config.py — настройки из env                                              │
│   state.py — adaptive backoff, get_user_stats                               │
│   schema.py — USER_FIELDS, RATING_FIELDS, backoff константы               │
└──────────────────────────────────────────────────────────────────────────┘

┌──────────────────────────── контейнер user-user ──────────────────────────┐
│   user_user/app.py (FastAPI, BaseParser) — пассивен, запускает цикл        │
│   только по /trigger-cycle (принимает usernames от координатора в body)     │
│        ├── scheduler.run_cycle(usernames, cfg, is_paused)                   │
│        │   для каждого username:                                            │
│        │     фетчит animelist (JSON) → парсит → loader.upsert_ratings       │
│        │     → cleanup stale, archive 404, адаптивный backoff               │
│        │     pause проверяется между элементами                              │
│        │                                                                    │
│        └── HTTP-эндпоинты: /status, /trigger-cycle, /refresh-user/{user},   │
│            /pause, /resume, /cycle-running, /health                         │
│                                                                              │
│   config.py — настройки из env                                              │
│   state.py — adaptive backoff, get_user_stats                               │
└──────────────────────────────────────────────────────────────────────────┘
        │
        ▼
Neo4j — та же БД, что и для parsers
```

Координатор чередует user-anime и user-user слайсами
(`COORDINATOR_USER_SLICE_SEC`, по умолчанию 1800 сек = 30 мин). Внутри
слайса координатор шлёт батчи по `COORDINATOR_BATCH_SIZE` (по умолчанию 5)
элементов: шлёт батч → ждёт завершения → следующий → пока слайс не истечёт.
Airing-parser запускается по времени (`ANIME_PARSER_TIME`, по умолчанию 03:00)
— если время airing наступило во время слайса, координатор прерывает слайс
(pause), запускает airing, затем продолжает. Адаптивный backoff: аниме
старт 10 дней, +10 при отсутствии изменений; юзеры старт 15 дней,
+15, кап 60 дней.

## Координатор (порт 8570)

`parsers/coordinator_app.py` — главный управляющий. Управляет тремя
парсерами через HTTP. Парсеры пассивны: не имеют собственного фонового
цикла. Координатор сам запрашивает Neo4j (`_select_*` функции перенесены
из state.py парсеров), формирует списки mal_ids / usernames и передаёт
парсерам через body `/trigger-cycle`.

**Авто-режим** (по умолчанию): чередование слайсов.
Каждый слайс: user-anime работает `COORDINATOR_USER_SLICE_SEC` (по
умолчанию 1800 = 30 мин), батчами по `COORDINATOR_BATCH_SIZE` (5).
Airing-parser запускается по времени `ANIME_PARSER_TIME` (03:00).
`_pause_all_others` останавливает все парсеры кроме указанного, ждёт
остановки. `_smart_wait` — если работы нет, координатор спрашивает БД
когда ближайший due, спит до него (без cap), каждые 60 сек проверяет airing.

**API (порт 8570):**

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

## Поток данных (один тайтл)

```
processing.process_one(mal_id)
  │
  ├── fetcher.get_anime_full(mal_id)
  │     ├── get_html("https://myanimelist.net/anime/{id}")
  │     │     └── mal_scraper.parse_anime_page(html) → dict
  │     ├── extract_slug_from_url(html_main) → slug
  │     ├── get_html("https://myanimelist.net/anime/{id}/{slug}/characters")
  │     │     └── mal_scraper.parse_characters_page(html) → {characters, staff}
  │     └── return объединённый dict
  │
  ├── parser.extract_fields(raw) → нормализованный dict
  │     └── _derive_year_season() — фоллбэк года/сезона из aired
  │
  └── loader.upsert_anime(data)
        ├── MERGE (:Anime {mal_id}) SET все свойства
        ├── MERGE (:Genre/:Studio/:Producer) + связи
        ├── MERGE (:Character) + HAS_CHARACTER
        ├── MERGE (:Person) + STAFF (с ролями) + VOICE_ACTED (с языком)
        ├── MERGE (:Anime/:Manga) + RELATED_TO (с типом relation)
        ├── MERGE (:ExternalLink) + AVAILABLE_AT / HAS_RESOURCE
        └── MERGE (:StreamingPlatform) + STREAMING_ON
```

После успешного `upsert_anime` узел `:Anime` получает `title` (не NULL),
что означает «обработан». При ошибке — `title` остаётся NULL, scheduler
подберёт тайтл в следующем цикле.

## Модули

### `parsers/anime/` (airing-parser, порт 8567)

| Модуль | Ответственность |
|---|---|
| `fetcher.py` | HTTP-запросы к MyAnimeList — обёртка над base_fetcher.MalFetcher |
| `mal_scraper.py` | Парсинг HTML → dict (BeautifulSoup). Три функции: `parse_season_page`, `parse_anime_page`, `parse_characters_page` |
| `parser.py` | Нормализация данных из scraper для loader. Фоллбэк year/season из `aired` |
| `loader.py` | dict → Cypher MERGE в Neo4j (все узлы и связи). `upsert_anime` |
| `graph_state.py` | Состояние очереди в Neo4j. Stub'ы, due-выборка, отметки сезонов, статистика |
| `processing.py` | Склеивает fetcher → parser → loader для одного тайтла. Общий код для scheduler и bootstrap |
| `discover.py` | Регистрирует новые тайтлы 3 актуальных сезонов в графе (через `graph_state.upsert_anime_stub`) |
| `scheduler_logic.py` | Один цикл: `discover_recent` + обработка mal_ids (от координатора) → `process_one` для каждого. `is_paused` callback проверяется между элементами |
| `bootstrap.py` | Ручной проход по всем историческим сезонам (1917→), кроме 3 актуальных |
| `app.py` | FastAPI (BaseParser) + цикл scheduler, HTTP-эндпоинты управления |
| `check_missing.py` | Ручной скрипт: сверка MAL ↔ Neo4j, добавление недостающих тайтлов |
| `mal_seasons.py` | Утилиты сезонов: `current_season`, `shift_season`, `all_seasons` |
| `config.py` | Настройки из env (config.yaml удалён) |

### `parsers/user_anime/` (user-anime, порт 8568)

| Модуль | Ответственность |
|---|---|
| `scraper.py` | Парсинг HTML stats-страниц → dict |
| `fetcher.py` | HTTP-клиент к MyAnimeList с rate limiter |
| `loader.py` | Neo4j: MERGE :User, :RATED, batch upsert, cleanup stale, archive |
| `state.py` | Adaptive backoff, get_user_stats |
| `scheduler.py` | Один цикл: mal_ids от координатора → stats-страницы → пользователи. `is_paused` между элементами и страницами |
| `app.py` | FastAPI (BaseParser) + цикл, HTTP-эндпоинты |
| `config.py` | Настройки из env |

### `parsers/user_user/` (user-user, порт 8569)

| Модуль | Ответственность |
|---|---|
| `scraper.py` | Парсинг JSON animelist → dict |
| `fetcher.py` | HTTP-клиент к MyAnimeList с rate limiter |
| `loader.py` | Neo4j: MERGE :RATED, cleanup stale, archive |
| `state.py` | Adaptive backoff, get_user_stats |
| `scheduler.py` | Один цикл: usernames от координатора → animelist → RATED. `is_paused` между элементами |
| `app.py` | FastAPI (BaseParser) + цикл, HTTP-эндпоинты |
| `config.py` | Настройки из env |

`:User` ≠ `:Person`. Person — staff/VA (создатели аниме). User — зритель,
который ставит оценки.

### `parsers/` (корень — базовые классы + координатор)

| Модуль | Ответственность |
|---|---|
| `base_fetcher.py` | MalFetcher — HTTP-клиент с рейт-лимитером, ретраями, kill switch (PauseRequested) |
| `base_parser.py` | BaseParser — базовый класс всех парсеров (FastAPI app, /trigger-cycle, /pause, /resume, /cycle-running, /status, /health) |
| `base_schema.py` | ANIME_FIELDS, DUE_STATUSES, AnimeStatus — единая схема данных |
| `base_scraper.py` | Утилиты парсинга: clean, clean_int — общие для всех скраперов |
| `coordinator_app.py` | Координатор — управляет 3 парсерами, авто-режим, чередование слайсов |

## Scheduler

`app.py` запускает цикл только по запросу (через координатор или
прямой `POST /trigger-cycle`). Координатор собирает due-тайтлы через
`_select_due_anime()` и передаёт их в body. Каждый цикл:

1. **discover** — запрашивает сезонные страницы MAL для текущего,
   следующего и прошлого сезона. Новые тайтлы регистрируются как stub'ы
   (`:Anime {mal_id, year, season}` с `title IS NULL`).
2. **обработка mal_ids** (от координатора) — для каждого mal_id:
   `process_one` — фетчит, парсит, льёт в Neo4j. `is_paused` callback
   проверяется между элементами — позволяет прервать цикл после текущего
   тайтла.
3. Цикл завершается. `trigger_cycle` с пустым списком → "no work", не
   запускает цикл.

Координатор вызывает `_select_due_anime` один раз (без `while True`) —
после обработки тайтлы с `title IS NOT NULL` и `mal_status = 'Finished
Airing'` выпадают из выборки, повторного сканирования не происходит.

## Bootstrap vs Scheduler

- **bootstrap.py**: долгая (часы/дни), запускается вручную, один раз.
  Идёт по всем сезонам 1917 → сейчас, кроме трёх актуальных. Возобновляемость:
  файл `bootstrap_progress.txt` отмечает последний обработанный сезон,
  `title IS NULL` — необработанные тайтлы внутри сезона. Ошибки отдельных
  тайтлов не роняют процесс.
- **scheduler_logic.py**: лёгкий цикл внутри `app.py`. Текущий/следующий/
  прошлый сезон + принудительные обновления через `/refresh/{mal_id}`.

Обе части используют общий `processing.process_one()` и пишут в одну БД
(Neo4j), поэтому нет риска параллельной обработки одного тайтла с разной
логикой.

## GraphRAG

Веб-интерфейс для запросов к графу Neo4j на естественном языке. Отдельный
контейнер `graphrag` (порт 8666), не зависит от парсеров.

```
┌──────────────────────────── контейнер graphrag ────────────────────────────┐
│                                                                              │
│   backend/                                                                   │
│     main.py (FastAPI, порт 8000 внутри контейнера → 8666 снаружи)          │
│        ├── раздаёт статику frontend/ (index.html, style.css, app.js,      │
│        │   logs.html, logs.css, logs.js)                                 │
│        ├── /api/chats — CRUD чатов (SQLite)                               │
│        ├── /api/chats/{id}/ask — основной пайплайн                         │
│        ├── /api/logs, /api/health                                         │
│        ├── /logs — веб-страница логов                                     │
│        └── /metrics — Prometheus text format                             │
│                                                                              │
│     graphrag.py — пайплайн question → answer:                              │
│        1. LLM генерирует Cypher (схема графа в system prompt)              │
│           Если данных не хватает — возвращает "CLARIFY: <вопрос>"          │
│           Если вопрос не подходит для графа — возвращает "INVALID"        │
│        2. Cypher выполняется в Neo4j                                        │
│        3. LLM формулирует ответ на языке пользователя из результатов       │
│           Самопроверка: при синтаксической ошибке — повтор до 3 попыток       │
│           LIMIT — только при явном запросе числа ("топ-5"), бэкенд           │
│           сам ограничивает до 100 строк для LLM                               │
│                                                                              │
│     db.py — SQLite: chats, messages, query_logs                             │
│           query_logs: model, llm_base_url, answer, duration_sec,           │
│           cypher_raw (с v13)                                               │
│                                                                              │
│   frontend/ (статика, монтируется в /frontend)                             │
│     ChatGPT-like UI: список чатов слева, поле ввода,                        │
│     ответы с раскрывающимся Cypher-запросом,                                │
│     markdown-рендеринг ответов (marked.js)                                 │
│     /logs — таблица логов: модель, статус, Cypher, ответ,                  │
│     сырой вывод LLM, длительность, фильтр по статусу                       │
│                                                                              │
└──────────────────────────────────────────────────────────────────────────┘
        │
        ▼
 Neo4j (контейнер neo4j) — та же БД, что и для парсеров
```

LLM — OpenAI-compatible API, настраивается через `.env`
(`GRAPHRAG_LLM_BASE_URL`, `GRAPHRAG_LLM_MODEL`). По умолчанию — `glm-5.2`.