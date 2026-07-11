# Changelog

Формат: дата — что изменилось и почему. Ведётся вручную, по мере значимых
архитектурных решений (не каждый мелкий коммит).

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