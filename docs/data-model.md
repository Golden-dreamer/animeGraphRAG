# Модель данных

Neo4j — единственная БД. Хранит граф аниме и состояние очереди парсинга.

## Узлы

| Метка | Свойства | Источник |
|---|---|---|
| `:Anime` | `mal_id`, `title`, `title_original`, `title_english`, `title_synonyms`, `title_japanese`, `poster_url`, `mal_url`, `type`, `episodes`, `mal_status`, `aired`, `premiered`, `broadcast`, `source`, `duration`, `rating`, `score`, `scored_by`, `ranked`, `popularity`, `members`, `favorites`, `synopsis`, `background`, `year`, `season`, `stats_watching`, `stats_completed`, `stats_on_hold`, `stats_dropped`, `stats_plan_to_watch`, `stats_total`, `summary_stats_at`, `score_stats` (JSON string), `score_stats_at` | Основная страница аниме + stats-страница (user-anime) |
| `:Genre` | `name` | Genres, Themes, Demographic |
| `:Studio` | `name` | Information → Studios |
| `:Producer` | `name` | Information → Producers, Licensors |
| `:Character` | `mal_id`, `name`, `url` | Characters & Voice Actors |
| `:Person` | `mal_id`, `name`, `url` | Staff, Voice Actors |
| `:ExternalLink` | `url`, `name` | Available At, Resources (включая ссылку на MAL) |
| `:StreamingPlatform` | `name` | Streaming Platforms |
| `:Manga` | `mal_id`, `title` | Related Entries (тип manga) |
| `:User` | `username` (unique), `profile_url`, `status` ("active"|"archived"), `discovered_via_anime`, `last_seen`, `created_at`, `archived_at` | user-anime / user-user |

`:Anime.title` — алиас для `title_original`, нужен для корректного
отображения в Neo4j Browser. `title IS NULL` означает, что тайтл
зарегистрирован как stub, но не обработан (данные с MAL ещё не получены).

Прогресс bootstrap хранится в файле `bootstrap_progress.txt` (на хосте
через volume `./parsers/anime`), не в Neo4j.

`:User` ≠ `:Person`. `:Person` — staff/VA (создатели аниме). `:User` —
зритель, который ставит оценки (источник: user-anime / user-user).

## Связи

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
| `RELATED_TO` | Anime → Anime/Manga | `relation`, `target_type` | Связанный тайтл |
| `AVAILABLE_AT` | Anime → ExternalLink | | Официальный сайт, Twitter, ... |
| `HAS_RESOURCE` | Anime → ExternalLink | | AniDB, ANN, Wikipedia, ... (включая ссылку на MAL) |
| `STREAMING_ON` | Anime → StreamingPlatform | `url`, `available` | Стриминговая платформа |
| `RATED` | User → Anime | `score`, `status`, `episodes_watched`, `tags`, `updated_at` | Оценка пользователя |

Все записи идут через `MERGE` — повторный `loader.upsert_anime()` с тем же
`mal_id` не создаёт дублей, а обновляет свойства узла.

## Индексы и констрейнты

Neo4j не создаёт индексы по свойствам автоматически. Без явных индексов
каждый `MERGE (a:Anime {mal_id: $mal_id})` делает полный скан всех узлов
данной метки — O(n).

Созданы констрейнты (уникальность + индекс) для всех ключей, по которым
идёт `MERGE` в `loader.py`:

| Метка | Свойство | Тип |
|---|---|---|
| `:Anime` | `mal_id` | UNIQUE CONSTRAINT |
| `:Person` | `mal_id` | UNIQUE CONSTRAINT |
| `:Character` | `mal_id` | UNIQUE CONSTRAINT |
| `:Manga` | `mal_id` | UNIQUE CONSTRAINT |
| `:Genre` | `name` | UNIQUE CONSTRAINT |
| `:Studio` | `name` | UNIQUE CONSTRAINT |
| `:Producer` | `name` | UNIQUE CONSTRAINT |
| `:User` | `username` | UNIQUE CONSTRAINT |
| `:ExternalLink` | `url` | INDEX (не unique) |

Дополнительно: `INDEX FOR (a:Anime) ON (a.mal_status)` — ускоряет
`select_due_anime` (фильтрация по `mal_status IN [...]`).

Проверка:
```cypher
SHOW CONSTRAINTS YIELD name, type, labelsOrTypes, properties
RETURN name, type, labelsOrTypes, properties ORDER BY labelsOrTypes;

SHOW INDEXES YIELD name, type, state, labelsOrTypes, properties
RETURN name, type, state, labelsOrTypes, properties ORDER BY labelsOrTypes;
```

## Добавление данных с других сайтов

Если в будущем появится инфа с AniList, Kitsu и т.д.:
- `mal_id` остаётся главным ключом (MAL — первоисточник).
- ID с других сайтов — дополнительные свойства: `a.anilist_id = 12345`.
- При необходимости — `CREATE INDEX FOR (a:Anime) ON (a.anilist_id)`.
- Если появится аниме, которого нет на MAL — суррогатный ключ
  `uid = "mal:5249"` или `"anilist:12345"` с констрейнтом на `uid`.