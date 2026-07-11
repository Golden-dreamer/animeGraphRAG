# Changelog

Формат: дата — что изменилось и почему. Ведётся вручную, по мере значимых
архитектурных решений (не каждый мелкий коммит).

## 2026-07-11 (v8) — удаление SQLite, одна БД (Neo4j)

**Проблема:** SQLite (state.db) хранил очередь задач, retry-логику и
метаданные — дублировал данные, уже есть в Neo4j (mal_status, year,
season). Бессмертный кэш скрывал обновления. Retry-логика усложняла
код без реальной пользы.

**Решения:**
- `db.py`, `rules.py` — удалены.
- `graph_state.py` — новый модуль: очередь/state в Neo4j.
  - "Не обработан" = `:Anime` с `title IS NULL` (stub).
  - `select_due_anime` — `mal_status IN ['Currently Airing', 'Not yet aired']`.
  - `select_due_for_season` — `title IS NULL` для конкретного сезона.
  - `:Season {year, season, bootstrapped: true}` — заменяет seasons_bootstrapped.
- `processing.py` — убрана retry-логика, mark_failed, mark_parsed.
  Ошибка → лог, тайтл остаётся stub (title IS NULL), scheduler попробует снова.
- `scheduler_logic.py` — select due из Neo4j через graph_state.
- `discover.py` — MERGE stubs в Neo4j через graph_state.upsert_anime_stub.
- `bootstrap.py` — работа через Neo4j (graph_state).
- `check_missing.py` — сверка MAL ↔ Neo4j напрямую.
- `update_staff.py` — убрана зависимость от db.
- `app.py`:
  - `/refresh/{mal_id}` — вызывает process_one напрямую (без очереди).
  - `/status` — статистика из Neo4j (total, parsed, stubs, airing, seasons).
  - `/stubs` — новый endpoint: неполные узлы (title IS NULL).
  - `/failed`, `/failed/retry` — удалены.
- `docker-compose.yml` — убран volume `./parsers/data`.
- `parsers/data/` — больше не нужна.
- Индекс: `CREATE INDEX FOR (a:Anime) ON (a.mal_status)`.

**Архитектура:** одна БД (Neo4j), одна точка истины.
  - Домен: :Anime, :Person, :Character, :Genre, :Studio, :Producer, :Manga, :Season.
  - Состояние: title IS NULL = не обработан, :Season.bootstrapped = сезон закрыт.
  - Scheduler: обновляет Currently Airing + Not yet aired каждый цикл.

## 2026-07-11 (v7) — удаление файлового кэша

**Проблема:** файловый кэш HTML-страниц (5.9 GB, 45k файлов) не имел TTL —
бессмертный кэш скрывал обновления MAL. Discover читал старые сезонные
страницы и не видел новые тайтлы. SQLite (state.db) уже отслеживал
обработанные тайтлы, делая кэш избыточным для резюмируемости.

**Решения:**
- `fetcher.py`: удалён cached_get_html, CACHE_DIR, get_cache_stats,
  clear_cache, cleanup_cache_if_over_limit. Добавлен get_html (прямой HTTP).
- `app.py`: удалены endpoints /cache/stats, /cache/clear, cache из /config.
- `config.py`, `config.yaml`: удалён cache_max_mb.
- `docker-compose.yml`: удалён CACHE_MAX_MB и volume ./parsers/cache.
- `scheduler_logic.py`: удалён cleanup_cache_if_over_limit.
- `update_staff.py`, `bootstrap.py`, `check_missing.py`: убраны
  cache-зависимости и --force флаги.
- `processing.py`: убран force параметр из process_one.
- Папка parsers/cache/ (5.9 GB) — удалить вручную (см. operations.md).

**Результат:** discover теперь каждый цикл идёт на MAL напрямую и видит
актуальные данные. Резюмируемость сохранена через SQLite (status='ok',
next_check_at в будущем).

## 2026-07-11 (v6) — индексы и констрейнты Neo4j, настройка памяти

**Проблема:** Neo4j работал без индексов и констрейнтов по свойствам —
только дефолтные LOOKUP-индексы по внутреннему ID. Каждый `MERGE
(a:Anime {mal_id: $mal_id})` делал полный скан всех узлов (O(n)).
На 187k узлов и 520k связей — уже медленно; при миллионах пользователей
с оценками — катастрофа. Память Neo4j — дефолт (~512MB heap), данных 905MB.

**Решения:**
- Созданы 7 uniqueness-констрейнтов (индекс + гарантия уникальности):
  `Anime.mal_id`, `Person.mal_id`, `Character.mal_id`, `Manga.mal_id`,
  `Genre.name`, `Studio.name`, `Producer.name`.
- Создан 1 обычный индекс: `ExternalLink.url`.
- `docker-compose.yml`: добавлены настройки памяти Neo4j:
  `heap_initial=1G`, `heap_max=4G`, `pagecache=2G`.
- Все MERGE в loader.py теперь идут через индекс — O(log n) вместо O(n).

**Текущий размер БД:** 187,621 узлов, 520,449 связей, 905 MB на диске.

## 2026-07-11 (v5) — исправление staff, управление scheduler, дополнение БД

**Проблемы:**
1. Staff парсился неполно: fetcher.py использовал короткий URL
   `/anime/{id}/characters`, который MAL редиректит на основную страницу
   аниме (без /characters), где staff ограничен 2-4 людьми. Полный URL
   со slug (`/anime/{id}/{slug}/characters`) отдаёт отдельную страницу
   с полным списком staff (до 100+ человек).
2. Нет API для ручного запуска цикла scheduler или изменения интервала.
3. Некоторые тайтлы пропущены при первичном наполнении (MAL дополняет
   сезонные страницы со временем).

**Решения:**
- `mal_scraper.py`: добавлена `extract_slug_from_url(html)` — извлекает
  slug из canonical/og:url URL основной страницы.
- `fetcher.py`: `get_anime_full()` теперь строит полный URL
  `/anime/{id}/{slug}/characters` вместо короткого.
- `loader.py`: добавлена `upsert_staff_only(mal_id, staff)` — точечное
  обновление staff без перезаписи остальных полей.
- `update_staff.py`: новый скрипт для дополнения staff у уже обработанных
  аниме (проходит все с <=4 staff, обновляет через правильный URL).
- `check_missing.py`: новый скрипт для сверки сезонных страниц с SQLite
  и добавления недостающих тайтлов в очередь.
- `app.py`: добавлены эндпоинты:
  - `POST /trigger-cycle` — запустить цикл scheduler прямо сейчас
  - `PUT /schedule` — изменить cycle_interval_sec (интервал автоматического цикла)

## 2026-07-10 (v4) — переход на прямой HTML-парсинг MyAnimeList

**Проблемы:**
1. Jikan API — сторонний прокси к MyAnimeList, часто нестабильный (429, 504,
   SSL-ошибки). Сайт myanimelist.net работает стабильно.
2. Jikan `/anime/{id}/full` отдаёт ограниченный набор полей — нет characters,
   voice actors, staff, related entries, resources, streaming platforms.
3. Ошибка `ValueError: could not convert string to float: '.'` для тайтлов
   без оценок (score = "N/A", ещё не вышли) — парсер падал.

**Решения:**
- `fetcher.py`: полностью переписан — вместо Jikan API (JSON) теперь прямые
  HTTP-запросы к myanimelist.net (HTML). Кэш в `.html` файлах вместо `.json`.
  Лимиты те же (0.5s интервал, 55 req/мин).
- `mal_scraper.py`: новый модуль — парсер HTML через BeautifulSoup. Три
  функции:
  - `parse_season_page()` — список тайтлов сезона (180 для Summer 2026)
  - `parse_anime_page()` — все поля со страницы аниме (titles, info, stats,
    synopsis, background, related entries, resources, streaming platforms)
  - `parse_characters_page()` — персонажи + voice actors + staff
- `parser.py`: нормализует данные из scraper (вместо JSON Jikan). Фоллбэк
  year/season из aired для старых тайтлов.
- `loader.py`: расширен для записи полной модели данных в Neo4j:
  - Узлы: Anime, Genre, Studio, Producer, Character, Person, ExternalLink,
    StreamingPlatform, Manga (related)
  - Связи: HAS_GENRE, HAS_THEME, HAS_DEMOGRAPHIC, PRODUCED_BY, PRODUCER_OF,
    LICENSED_BY, HAS_CHARACTER, STAFF (с ролями), VOICE_ACTED (с языком),
    RELATED_TO (с типом relation), AVAILABLE_AT, HAS_RESOURCE, STREAMING_ON
  - `a.title` (алиас `title_original`) — для корректного отображения в
    Neo4j Browser (т.к. `aired` шёл раньше `title_original` по алфавиту)
  - `a.mal_url` — прямая ссылка на страницу аниме
- `requirements.txt`: добавлен `beautifulsoup4==4.12.3`
- `docker-compose.yml`: `JIKAN_BASE_URL` → `MAL_BASE_URL`
- `app.py`: добавлен эндпоинт `POST /failed/retry` — сброс всех failed-тайтлов
  одним запросом.
- N/A scores: score/ranked/scored_by корректно возвращают None для тайтлов
  без оценок (вместо краша).
- `from __future__ import annotations` во всех модулях — совместимость с
  Python 3.9+ (хост) и 3.12 (Docker).

**Данные:** на один тайтл — два HTTP-запроса (основная страница + characters/
staff). ~27 тайтлов/мин при лимите 55 req/мин.

## 2026-07-10 (v3) — resilience и лимиты API

**Проблемы:**
1. `TypeError: mark_failed() missing 2 required positional arguments` —
   `processing.py` вызывал `mark_failed(mal_id, retry_after_iso)`, а `db.py`
   после v2 требовал 4 аргумента.
2. `bootstrap.py` останавливался при первой же ошибке `process_one()`.
3. Jikan `/anime/{id}/full` часто отдаёт `year=null, season=null` для
   старых тайтлов.
4. `SSLEOFError` при запросах к Jikan API — fetcher делал один `requests.get`
   без ретраев.
5. Scheduler ограничивался `batch_size=50` тайтлов за цикл.
6. Кэш `parsers/cache/` и `parsers/data/` были пусты: docker-compose
   использовал named volumes поверх хост-папок.

**Решения:**
- `processing.py`: исправлен вызов `mark_failed()`. `process_one()` никогда
  не бросает исключения наружу.
- `bootstrap.py`: цикл обработки обёрнут в `try/except` per mal_id.
- `parser.py`: добавлен `_derive_year_season()` — фоллбэк через `aired`.
- `fetcher.py`: переписан rate limiter — два лимита одновременно.
  Ретраи с экспоненциальным бэкоффом.
- `scheduler_logic.py`: обрабатывает все due-тайтлы за цикл.
- `docker-compose.yml`: named volumes заменены на bind mounts.

## 2026-07-10 (v2) — retry с ограничением попыток

**Проблема:** тайтл с ошибкой откладывался на 6 часов вперёд, сезон мог быть
помечен как обработанный раньше, чем тайтл долетел до Neo4j.

**Решение:** `attempts` + `status` в `anime_progress`. Короткий retry (5 мин),
после 3 неудач — `failed`. Эндпоинт `GET /failed`.

## 2026-07-10 (v1) — первая версия MVP

- Jikan API (без HTML-парсинга). Восемь полей на тайтл.
- Файловый кэш + SQLite для состояния очереди.
- Разделение bootstrap (архив) и scheduler (актуальные сезоны).
- Мини-FastAPI: `/refresh/{mal_id}`, `/status`.
- `docker compose up` (Neo4j + parsers).