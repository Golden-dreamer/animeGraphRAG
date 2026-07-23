"""Единая схема данных для всех парсеров MyAnimeList.

Источник правды для полей узлов :Anime, :User, связи :RATED,
статусов, и констант adaptive backoff.

Монтируется read-only в каждый контейнер парсера (как base_parser.py).
"""
from __future__ import annotations

from enum import Enum


# ---------------------------------------------------------------------------
# :Anime
# ---------------------------------------------------------------------------

class AnimeStatus(str, Enum):
    """Допустимые значения mal_status с MyAnimeList."""
    FINISHED = "Finished Airing"
    AIRING = "Currently Airing"
    NOT_YET_AIRED = "Not yet aired"


# Поля узла :Anime, записываемые loader.upsert_anime.
# Порядок = порядок SET в Cypher. mal_id — key (MERGE), не SET.
ANIME_FIELDS: tuple[str, ...] = (
    "poster_url",
    "title_original",
    "title_english",
    "title_synonyms",
    "title_japanese",
    "type",
    "episodes",
    "mal_status",
    "aired",
    "premiered",
    "broadcast",
    "source",
    "duration",
    "rating",
    "score",
    "scored_by",
    "ranked",
    "popularity",
    "members",
    "favorites",
    "synopsis",
    "background",
    "year",
    "season",
    "mal_url",
)

# Статусы, при которых тайтл считается "актуальным" для scheduler.
DUE_STATUSES: tuple[str, ...] = (
    AnimeStatus.AIRING.value,
    AnimeStatus.NOT_YET_AIRED.value,
)


# ---------------------------------------------------------------------------
# :User
# ---------------------------------------------------------------------------

class UserStatus(str, Enum):
    """Статус узла :User."""
    ACTIVE = "active"
    ARCHIVED = "archived"


# Поля узла :User (mal_id нет — ключ по username).
# Порядок = порядок SET в Cypher. username — key (MERGE), не SET.
USER_FIELDS: tuple[str, ...] = (
    "profile_url",
    "status",
    "discovered_via_anime",
    "last_seen",
    "created_at",
    "archived_at",
)


# ---------------------------------------------------------------------------
# :RATED (связь User → Anime)
# ---------------------------------------------------------------------------

RATING_FIELDS: tuple[str, ...] = (
    "score",           # 1-10 или None
    "status",          # "Watching" | "Completed" | "On Hold" | "Dropped" | "Plan to Watch"
    "episodes_watched",
    "tags",            # теги пользователя на это аниме
    "updated_at",
)

# Маппинг статусов из animelist JSON (int → строка)
ANIMELIST_STATUS_MAP: dict[int, str] = {
    1: "Watching",
    2: "Completed",
    3: "On Hold",
    4: "Dropped",
    6: "Plan to Watch",
}


# ---------------------------------------------------------------------------
# Adaptive backoff константы
# ---------------------------------------------------------------------------

# Аниме: интервал старт 10 дней, шаг +10 при отсутствии изменений
ANIME_INITIAL_INTERVAL_DAYS = 10
ANIME_INTERVAL_STEP_DAYS = 10

# Юзеры: интервал старт 15 дней, шаг +15 при отсутствии изменений
USER_INITIAL_INTERVAL_DAYS = 15
USER_INTERVAL_STEP_DAYS = 15