# Data Model

Neo4j is the only database. It stores the anime graph and the parsing queue state.

## Nodes

| Label | Properties | Source |
|---|---|---|
| `:Anime` | `mal_id`, `title`, `title_original`, `title_english`, `title_synonyms`, `title_japanese`, `poster_url`, `mal_url`, `type`, `episodes`, `mal_status`, `aired`, `premiered`, `broadcast`, `source`, `duration`, `rating`, `score`, `scored_by`, `ranked`, `popularity`, `members`, `favorites`, `synopsis`, `background`, `year`, `season`, `stats_watching`, `stats_completed`, `stats_on_hold`, `stats_dropped`, `stats_plan_to_watch`, `stats_total`, `summary_stats_at`, `score_stats` (JSON string), `score_stats_at` | Main anime page + stats page (user-anime) |
| `:Genre` | `name` | Genres, Themes, Demographic |
| `:Studio` | `name` | Information → Studios |
| `:Producer` | `name` | Information → Producers, Licensors |
| `:Character` | `mal_id`, `name`, `url` | Characters & Voice Actors |
| `:Person` | `mal_id`, `name`, `url` | Staff, Voice Actors |
| `:ExternalLink` | `url`, `name` | Available At, Resources (including the MAL link) |
| `:StreamingPlatform` | `name` | Streaming Platforms |
| `:Manga` | `mal_id`, `title` | Related Entries (type manga) |
| `:User` | `username` (unique), `profile_url`, `status` ("active"|"archived"), `discovered_via_anime`, `last_seen`, `created_at`, `archived_at` | user-anime / user-user |

`:Anime.title` is an alias for `title_original`, needed for correct
display in Neo4j Browser. `title IS NULL` means the title is registered
as a stub but not yet processed (data from MAL has not been fetched yet).

Bootstrap progress is stored in the file `bootstrap_progress.txt` (on the
host via the `./parsers/anime` volume), not in Neo4j.

`:User` ≠ `:Person`. `:Person` — staff/VA (anime creators). `:User` —
a viewer who rates anime (source: user-anime / user-user).

## Relationships

| Relationship | From → To | Properties | Meaning |
|---|---|---|---|
| `HAS_GENRE` | Anime → Genre | | Genre |
| `HAS_THEME` | Anime → Genre | | Theme (Isekai, Psychological, ...) |
| `HAS_DEMOGRAPHIC` | Anime → Genre | | Demographic (Shounen, Seinen, ...) |
| `PRODUCED_BY` | Anime → Studio | | Studio |
| `PRODUCER_OF` | Anime → Producer | | Producer |
| `LICENSED_BY` | Anime → Producer | | Licensor |
| `HAS_CHARACTER` | Anime → Character | `role` (Main/Supporting) | Anime character |
| `STAFF` | Anime → Person | `roles` (list) | Staff member (director, producer, ...) |
| `VOICE_ACTED` | Person → Character | `language`, `anime_id` | Voice actor voiced a character |
| `RELATED_TO` | Anime → Anime/Manga | `relation`, `target_type` | Related title |
| `AVAILABLE_AT` | Anime → ExternalLink | | Official site, Twitter, ... |
| `HAS_RESOURCE` | Anime → ExternalLink | | AniDB, ANN, Wikipedia, ... (including the MAL link) |
| `STREAMING_ON` | Anime → StreamingPlatform | `url`, `available` | Streaming platform |
| `RATED` | User → Anime | `score`, `status`, `episodes_watched`, `tags`, `updated_at` | User rating |

All writes go through `MERGE` — re-running `loader.upsert_anime()` with the
same `mal_id` does not create duplicates but updates node properties.

## Indexes and Constraints

Neo4j does not create property indexes automatically. Without explicit
indexes, every `MERGE (a:Anime {mal_id: $mal_id})` does a full scan of all
nodes with the given label — O(n).

Constraints (uniqueness + index) have been created for all keys used in
`MERGE` calls within `loader.py`:

| Label | Property | Type |
|---|---|---|
| `:Anime` | `mal_id` | UNIQUE CONSTRAINT |
| `:Person` | `mal_id` | UNIQUE CONSTRAINT |
| `:Character` | `mal_id` | UNIQUE CONSTRAINT |
| `:Manga` | `mal_id` | UNIQUE CONSTRAINT |
| `:Genre` | `name` | UNIQUE CONSTRAINT |
| `:Studio` | `name` | UNIQUE CONSTRAINT |
| `:Producer` | `name` | UNIQUE CONSTRAINT |
| `:User` | `username` | UNIQUE CONSTRAINT |
| `:ExternalLink` | `url` | INDEX (non-unique) |

Additionally: `INDEX FOR (a:Anime) ON (a.mal_status)` — speeds up
`select_due_anime` (filtering by `mal_status IN [...]`).

Verification:
```cypher
SHOW CONSTRAINTS YIELD name, type, labelsOrTypes, properties
RETURN name, type, labelsOrTypes, properties ORDER BY labelsOrTypes;

SHOW INDEXES YIELD name, type, state, labelsOrTypes, properties
RETURN name, type, state, labelsOrTypes, properties ORDER BY labelsOrTypes;
```

## Adding Data from Other Sources

If data from AniList, Kitsu, etc. becomes available in the future:
- `mal_id` remains the primary key (MAL is the source of truth).
- IDs from other sites become additional properties: `a.anilist_id = 12345`.
- If needed — `CREATE INDEX FOR (a:Anime) ON (a.anilist_id)`.
- If an anime appears that is not on MAL — use a surrogate key
  `uid = "mal:5249"` or `"anilist:12345"` with a constraint on `uid`.