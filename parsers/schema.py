"""Единая схема данных Anime: поля, типы, допустимые значения.

Источник правды для parser.py (нормализация), loader.py (запись в Neo4j)
и graph_state.py (значения mal_status для scheduler).
"""
from __future__ import annotations

from enum import Enum


class AnimeStatus(str, Enum):
    """Допустимые значения mal_status с MyAnimeList."""
    FINISHED = "Finished Airing"
    AIRING = "Currently Airing"
    NOT_YET_AIRED = "Not yet aired"


# Поля узла :Anime, записываемые loader.upsert_anime_node.
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