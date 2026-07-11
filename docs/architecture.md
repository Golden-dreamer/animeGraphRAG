# Архитектура

## Общая схема

```
┌──────────────────────────── контейнер parsers ─────────────────────────────┐
│                                                                              │
│   app.py (FastAPI + фоновый asyncio-цикл)                                  │
│        │                                                                    │
│        ├── каждые cycle_interval_sec: scheduler_logic.run_cycle()          │
│        │        │                                                          │
│        │        ├── discover.discover_recent()  — регистрирует тайтлы      │
│        │        │     текущего/следующего/прошлого сезона как stub'ы       │
│        │        │                                                          │
│        │        └── graph_state.select_due_anime() → processing.process_one() × N   │
│        │                                                                    │
│        └── HTTP-эндпоинты: /refresh/{id}, /trigger-cycle, /schedule,       │
│            /status, /stubs, /config, /health                               │
│                                                                              │
│   bootstrap.py — запускается ОТДЕЛЬНО, вручную, один раз                   │
│        └── по всем историческим сезонам (1917 → сейчас, кроме текущих 3)   │
│            graph_state.select_due_for_season() → processing.process_one() × N   │
│                                                                              │
└──────────────────────────────────────────────────────────────────────────┘
        │
        ▼
 Neo4j (контейнер neo4j)
   граф: Anime/Genre/Studio/Producer/Person/Character/
         ExternalLink/StreamingPlatform/Manga
   состояние очереди: title IS NULL = не обработан
   прогресс bootstrap: файл bootstrap_progress.txt (на хосте через volume)
```

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

| Модуль | Ответственность |
|---|---|
| `fetcher.py` | HTTP-запросы к MyAnimeList, рейт-лимит (0.5s + 55 req/мин), ретраи с бэкоффом |
| `mal_scraper.py` | Парсинг HTML → dict (BeautifulSoup). Три функции: `parse_season_page`, `parse_anime_page`, `parse_characters_page` |
| `parser.py` | Нормализация данных из scraper для loader. Фоллбэк year/season из `aired` |
| `loader.py` | dict → Cypher MERGE в Neo4j (все узлы и связи). `upsert_anime`, `upsert_staff_only` |
| `graph_state.py` | Состояние очереди в Neo4j. Stub'ы, due-выборка, отметки сезонов, статистика |
| `processing.py` | Склеивает fetcher → parser → loader для одного тайтла. Общий код для scheduler и bootstrap |
| `discover.py` | Регистрирует новые тайтлы 3 актуальных сезонов в графе (через `graph_state.upsert_anime_stub`) |
| `scheduler_logic.py` | Один цикл: `discover_recent` + `select_due_anime` → `process_one` для каждого |
| `bootstrap.py` | Ручной проход по всем историческим сезонам (1917→), кроме 3 актуальных |
| `app.py` | FastAPI + фоновый цикл scheduler'а, HTTP-эндпоинты управления |
| `update_staff.py` | Ручной скрипт: дополнение staff для аниме с <=4 записями |
| `check_missing.py` | Ручной скрипт: сверка MAL ↔ Neo4j, добавление недостающих тайтлов |
| `mal_seasons.py` | Утилиты сезонов: `current_season`, `shift_season`, `all_seasons` |
| `config.py` | Загрузка `config.yaml` + env-переменных в объект `Config` |

## Scheduler

`app.py` запускает фоновый asyncio-задачу при старте. Каждый цикл:

1. **discover** — запрашивает сезонные страницы MAL для текущего,
   следующего и прошлого сезона. Новые тайтлы регистрируются как stub'ы
   (`:Anime {mal_id, year, season}` с `title IS NULL`).
2. **select_due_anime** — один запрос к Neo4j: все тайтлы с
   `mal_status IN ['Currently Airing', 'Not yet aired']` OR `title IS NULL`.
   Приоритет: stub'ы (0) → airing (1) → upcoming (2).
3. **process_one** для каждого mal_id — фетчит, парсит, льёт в Neo4j.
4. Цикл завершается. Таймер следующего запуска отсчитывается от конца
   обработки.

`select_due_anime` вызывается один раз (без `while True`) — после обработки
тайтлы с `title IS NOT NULL` и `mal_status = 'Finished Airing'` выпадают
из выборки, повторного сканирования не происходит.

## Bootstrap vs Scheduler

- **bootstrap.py**: долгая (часы/дни), запускается вручную, один раз.
  Идёт по всем сезонам 1917 → сейчас, кроме трёх актуальных. Резюмируема:
  файл `bootstrap_progress.txt` отмечает последний обработанный сезон,
  `title IS NULL` — необработанные тайтлы внутри сезона. Ошибки отдельных
  тайтлов не роняют процесс.
- **scheduler_logic.py**: лёгкий цикл внутри `app.py`. Текущий/следующий/
  прошлый сезон + принудительные обновления через `/refresh/{mal_id}`.

Обе части используют общий `processing.process_one()` и пишут в одну БД
(Neo4j), поэтому нет риска параллельной обработки одного тайтла с разной
логикой.