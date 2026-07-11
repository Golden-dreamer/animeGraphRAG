# Модель данных

## SQLite (`data/state.db`) — служебное состояние, НЕ доменные данные

### `anime_progress`

| Поле | Тип | Смысл |
|---|---|---|
| `mal_id` | INTEGER PK | ID тайтла на MyAnimeList |
| `year`, `season` | INTEGER, TEXT | сезон выхода (используется для приоритета и правил обновления) |
| `mal_status` | TEXT | статус с сайта: `Currently Airing` / `Finished Airing` / `Not yet aired` |
| `priority` | INTEGER | `1` = принудительно поставлен в начало очереди (`POST /refresh`), `0` = обычный |
| `last_parsed_at` | TEXT (ISO) | когда последний раз успешно обработан |
| `next_check_at` | TEXT (ISO) | когда обрабатывать следующий раз (см. `rules.py`) |
| `attempts` | INTEGER | счётчик подряд идущих ошибок, сбрасывается при успехе |
| `status` | TEXT | `pending` (ещё не обработан или ждёт retry) / `ok` (успешно спарсен хотя бы раз) / `failed` (исчерпал попытки, нужно вмешательство) |
| `last_error` | TEXT | текст последней ошибки (для диагностики через `GET /failed`) |

`next_check_at = '9999-01-01T00:00:00+00:00'` — специальное значение
"никогда не обновлять автоматически" (используется и для архивных тайтлов
старше `refresh_recent_years`, и для `status='failed'`).

### `seasons_bootstrapped`

| Поле | Тип | Смысл |
|---|---|---|
| `year`, `season` | INTEGER, TEXT | PK |
| `completed_at` | TEXT (ISO) | когда `bootstrap.py` полностью закрыл этот сезон |

Используется только `bootstrap.py`, чтобы не пересканировать список сезона
повторно при рестарте (хотя список и так закэширован — эта таблица экономит
даже чтение кэша и итерацию по уже обработанным тайтлам).

## Neo4j — доменный граф

### Узлы

| Метка | Свойства | Источник |
|---|---|---|
| `:Anime` | `mal_id`, `title`, `title_original`, `title_english`, `title_synonyms`, `title_japanese`, `poster_url`, `mal_url`, `type`, `episodes`, `mal_status`, `aired`, `premiered`, `broadcast`, `source`, `duration`, `rating`, `score`, `scored_by`, `ranked`, `popularity`, `members`, `favorites`, `synopsis`, `background`, `year`, `season` | Основная страница аниме |
| `:Genre` | `name` | Genres, Themes, Demographic |
| `:Studio` | `name` | Information → Studios |
| `:Producer` | `name` | Information → Producers, Licensors |
| `:Character` | `mal_id`, `name`, `url` | Characters & Voice Actors |
| `:Person` | `mal_id`, `name`, `url` | Staff, Voice Actors |
| `:ExternalLink` | `url`, `name` | Available At, Resources (включая ссылку на MAL) |
| `:StreamingPlatform` | `name` | Streaming Platforms |
| `:Manga` | `mal_id`, `title` | Related Entries (тип manga) |

### Связи

| Связь | От → К | Свойства | Смысл |
|---|---|---|---|
| `HAS_GENRE` | Anime → Genre | | Жанр |
| `HAS_THEME` | Anime → Genre | | Тема (Isekai, Psychological, ...) |
| `HAS_DEMOGRAPHIC` | Anime → Genre | | Демография (Shounen, Seinen, ...) |
| `PRODUCED_BY` | Anime → Studio | | Студия |
| `PRODUCER_OF` | Anime → Producer | | Продюсер |
| `LICENSED_BY` | Anime → Producer | | Лицензиар |
| `HAS_CHARACTER` | Anime → Character | `role` (Main/Supporting) | Персонаж аниме |
| `STAFF` | Anime → Person | `roles` (список) | Человек из staff (director, producer, ...) |
| `VOICE_ACTED` | Person → Character | `language`, `anime_id` | Voice actor озвучил персонажа |
| `RELATED_TO` | Anime → Anime/Manga | `relation` (Prequel, Sequel, Adaptation, ...), `target_type` | Связанный тайтл |
| `AVAILABLE_AT` | Anime → ExternalLink | | Официальный сайт, Twitter, ... |
| `HAS_RESOURCE` | Anime → ExternalLink | | AniDB, ANN, Wikipedia, ... (включая ссылку на MAL) |
| `STREAMING_ON` | Anime → StreamingPlatform | `url`, `available` | Стриминговая платформа |

Все записи идут через `MERGE`, повторный `loader.upsert_anime()` с тем же
`mal_id` не создаёт дублей, а обновляет свойства узла.

### Отображение в Neo4j Browser

Neo4j Browser показывает на ноде первое string-свойство по алфавиту.
Узел `:Anime` содержит `title` (алиас для `title_original`), которое идёт
раньше `aired` по алфавиту — поэтому на нодах отображается название аниме,
а не дата выхода.

Для уже существующих нод без `title` — одноразовая Cypher-команда:
```cypher
MATCH (a:Anime) WHERE a.title IS NULL AND a.title_original IS NOT NULL
SET a.title = a.title_original
```