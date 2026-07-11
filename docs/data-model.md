# Модель данных

## Neo4j — единственная БД (с v8)

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
| `:Season` | `year`, `season`, `bootstrapped` | Внутренний: отметка о закрытии сезона в bootstrap |

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

### Индексы и констрейнты

Neo4j не создаёт индексы по свойствам автоматически — только дефолтные
LOOKUP-индексы по внутреннему ID узла (который нестабилен и не подходит
для MERGE). Без явных индексов каждый `MERGE (a:Anime {mal_id: $mal_id})`
делает полный скан всех узлов данной метки — O(n).

Созданы констрейнты (уникальность + индекс) для всех ключей, по которым
идёт `MERGE` в `loader.py`:

| Метка | Свойство | Тип | Назначение |
|---|---|---|---|
| `:Anime` | `mal_id` | UNIQUE CONSTRAINT | Первичный ключ тайтла |
| `:Person` | `mal_id` | UNIQUE CONSTRAINT | Первичный ключ человека (staff/VA) |
| `:Character` | `mal_id` | UNIQUE CONSTRAINT | Первичный ключ персонажа |
| `:Manga` | `mal_id` | UNIQUE CONSTRAINT | Первичный ключ манги (related) |
| `:Genre` | `name` | UNIQUE CONSTRAINT | Уникальность жанра/темы/демографии |
| `:Studio` | `name` | UNIQUE CONSTRAINT | Уникальность студии |
| `:Producer` | `name` | UNIQUE CONSTRAINT | Уникальность продюсера/лицензиара |
| `:ExternalLink` | `url` | INDEX (не unique) | Быстрый lookup по URL |

Констрейнт = индекс + гарантия уникальности. Если попытаться создать
два Anime с одинаковым `mal_id` — Neo4j выбросит ошибку (что и нужно,
`MERGE` сам предотвращает дубли, но констрейнт — страховка).

Cypher-команды для проверки:
```cypher
SHOW CONSTRAINTS YIELD name, type, labelsOrTypes, properties
RETURN name, type, labelsOrTypes, properties ORDER BY labelsOrTypes;

SHOW INDEXES YIELD name, type, state, labelsOrTypes, properties
RETURN name, type, state, labelsOrTypes, properties ORDER BY labelsOrTypes;
```

### Добавление данных с других сайтов

Если в будущем появится инфа с AniList, Kitsu и т.д.:
- `mal_id` остаётся главным ключом (MAL — первоисточник).
- ID с других сайтов — дополнительные свойства: `a.anilist_id = 12345`.
- При необходимости — `CREATE INDEX FOR (a:Anime) ON (a.anilist_id)`.
- Если появится аниме, которого нет на MAL — суррогатный ключ
  `uid = "mal:5249"` или `"anilist:12345"` с констрейнтом на `uid`.

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