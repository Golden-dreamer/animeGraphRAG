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
│        │        │     текущего/следующего/прошлого сезона в очереди        │
│        │        │                                                          │
│        │        └── db.select_due_anime() → processing.process_one() × N   │
│        │                                                                    │
│        └── HTTP-эндпоинты: /refresh/{id}, /trigger-cycle, /schedule, /status, /failed, /failed/retry  │
│                                                                              │
│   bootstrap.py — запускается ОТДЕЛЬНО, вручную, один раз                   │
│        └── по всем историческим сезонам (1917 → сейчас, кроме текущих 3)   │
│            db.select_due_for_season() → processing.process_one() × N       │
│                                                                              │
└──────────────────────────────────────────────────────────────────────────┘
        │                          │                         │
        ▼                          ▼                         ▼
 cache/ (файлы .html,       data/state.db (SQLite:    Neo4j (граф:
 HTML-страницы MAL)         очередь, что и когда      Anime/Genre/Studio/
                            обновлять)                Person/Character/...)
```

## Поток данных (один тайтл)

```
processing.process_one(mal_id)
  │
│  ├── fetcher.get_anime_full(mal_id)
  │     ├── cached_get_html("https://myanimelist.net/anime/{id}")
  │     │     └── mal_scraper.parse_anime_page(html) → dict
  │     ├── extract_slug_from_url(html_main) → slug (например "Mitsume_ga_Tooru")
  │     ├── cached_get_html("https://myanimelist.net/anime/{id}/{slug}/characters")
  │     │     └── mal_scraper.parse_characters_page(html) → {characters, staff}
  │     └── return объединённый dict
  │
  ├── parser.extract_fields(raw) → нормализованный dict
  │     └── _derive_year_season() — фоллбэк года/сезона из aired
  │
  ├── loader.upsert_anime(data)
  │     ├── MERGE (:Anime {mal_id}) SET все свойства
  │     ├── MERGE (:Genre/:Studio/:Producer) + связи
  │     ├── MERGE (:Character) + HAS_CHARACTER
  │     ├── MERGE (:Person) + STAFF (с ролями) + VOICE_ACTED (с языком)
  │     ├── MERGE (:Anime/:Manga) + RELATED_TO (с типом relation)
  │     ├── MERGE (:ExternalLink) + AVAILABLE_AT / HAS_RESOURCE
  │     └── MERGE (:StreamingPlatform) + STREAMING_ON
  │
  ├── rules.compute_next_check(data, cfg) → ISO timestamp
  └── db.mark_parsed(mal_id, mal_status, next_check_at)
```

## Модули и границы ответственности

| Модуль | Что делает | Чего НЕ делает |
|---|---|---|
| `fetcher.py` | HTTP-запросы к MyAnimeList, файловый кэш `.html`, рейт-лимит | не знает про структуру данных аниме, не знает про Neo4j |
| `mal_scraper.py` | Парсинг HTML → dict (BeautifulSoup). Три функции: сезон, аниме, characters/staff | не делает HTTP-запросов, не пишет в БД |
| `parser.py` | Нормализация данных из scraper для loader (year/season фоллбэк) | не делает HTTP-запросов, не пишет в БД |
| `loader.py` | dict → Cypher MERGE в Neo4j (все узлы и связи) | не знает про HTTP, не знает про SQLite |
| `db.py` | состояние очереди в SQLite (что и когда обновлять) | не содержит доменных данных об аниме |
| `rules.py` | считает `next_check_at` по правилам актуальности | не делает запросов, чистая функция |
| `discover.py` | находит НОВЫЕ тайтлы (только 3 актуальных сезона) | не трогает архивные сезоны |
| `processing.py` | склеивает fetcher → parser → loader → db для ОДНОГО тайтла | общий код для scheduler и bootstrap, чтобы не дублировать логику |
| `scheduler_logic.py` | один цикл: discover + обработка due-очереди | не занимается историческим архивом |
| `bootstrap.py` | разовый проход по всем историческим сезонам | не запускается автоматически, не трогает 3 активных сезона |
| `app.py` | FastAPI + фоновый вечный цикл scheduler'а, эндпоинты управления | не содержит бизнес-логики парсинга напрямую |
| `update_staff.py` | Ручной скрипт: дополнение staff для аниме с <=4 записями | не запускается автоматически, работает поверх существующих данных |
| `check_missing.py` | Ручной скрипт: сверка сезонных страниц с SQLite, добавление недостающих тайтлов | не запускается автоматически, только регистрирует stubs |

Разделение фетча (fetcher), парсинга (mal_scraper) и нормализации (parser)
сделано специально: если понадобятся дополнительные поля, их можно добавить
в `mal_scraper.py` и получить из уже закэшированного HTML, не делая повторных
запросов к сайту (см. `fetcher.cached_get_html(url, force=False)`).

## Bootstrap vs Scheduler — почему это разделено

Изначальная ошибка в проектировании (см. `docs/changelog.md`) — попытка
объединить "разово наполнить архив" и "постоянно актуализировать свежее" в
одну сущность. Это разные по природе задачи:

- **bootstrap.py**: долгая (часы/дни), ресурсоёмкая, запускается вручную,
  один раз (или пока не закроет весь архив). Резюмируема на уровне сезона
  и отдельного тайтла — SQLite фиксирует прогресс после каждой успешной
  обработки, прерывание не приводит к повторной работе.
- **scheduler_logic.py**: лёгкая, работает вечно сама, никогда не завершается,
  занимается только текущим/следующим/прошлым сезоном плюс принудительными
  обновлениями через API.

Обе части используют общий `processing.process_one()` и одну и ту же таблицу
`anime_progress`, поэтому нет риска, что они "не знают" друг о друге и
обработают один и тот же тайтл дважды параллельно с разной логикой.