"""Преобразование данных из mal_scraper в формат для loader.py.

Раньше парсил raw JSON от Jikan API (/anime/{id}/full).
Теперь mal_scraper уже возвращает плоский dict с правильными полями.
Этот модуль оставлен для совместимости: нормализует поля и выводит
year/season, если они не были определены (для старых тайтлов).
"""
from __future__ import annotations

import re

_SEASON_MAP = {
    1: "winter", 2: "winter", 3: "winter",
    4: "spring", 5: "spring", 6: "spring",
    7: "summer", 8: "summer", 9: "summer",
    10: "fall", 11: "fall", 12: "fall",
}


def _derive_year_season(data: dict) -> tuple[int | None, str | None]:
    """Выводит year и season, если они не заданы напрямую.

    Приоритет:
      1. data['year'] / data['season'] (из Premiered)
      2. data['aired'] — парсим дату из строки "Apr 4, 2026 to ?"
    """
    year = data.get("year")
    season = data.get("season")

    if year and season:
        return year, season

    if year and not season:
        # Пытаемся извлечь месяц из aired
        aired = data.get("aired") or ""
        m = re.search(r'\b(\d{4})-(\d{2})-\d{2}\b', aired)
        if not m:
            m = re.search(r'\b(\w{3})\s+\d+,?\s+(\d{4})\b', aired)
            if m:
                month_str = m.group(1)
                months = {
                    "Jan": 1, "Feb": 2, "Mar": 3, "Apr": 4, "May": 5, "Jun": 6,
                    "Jul": 7, "Aug": 8, "Sep": 9, "Oct": 10, "Nov": 11, "Dec": 12,
                }
                month = months.get(month_str)
                if month and month in _SEASON_MAP:
                    season = _SEASON_MAP[month]
        return year, season

    # Если year нет — пытаемся из aired
    aired = data.get("aired") or ""
    m = re.search(r'\b(\w{3})\s+\d+,?\s+(\d{4})\b', aired)
    if m:
        year = int(m.group(2))
        months = {
            "Jan": 1, "Feb": 2, "Mar": 3, "Apr": 4, "May": 5, "Jun": 6,
            "Jul": 7, "Aug": 8, "Sep": 9, "Oct": 10, "Nov": 11, "Dec": 12,
        }
        month = months.get(m.group(1))
        if month and month in _SEASON_MAP:
            season = _SEASON_MAP[month]

    return year, season


def extract_fields(raw: dict) -> dict | None:
    """Нормализует данные из mal_scraper для loader.py.

    Теперь raw — это уже готовый dict от parse_anime_page + parse_characters_page.
    Добавляет/выводит year/season, если их не было.
    """
    if not raw or raw.get("mal_id") is None:
        return None

    year, season = _derive_year_season(raw)

    return {
        "mal_id": raw.get("mal_id"),
        "mal_url": f"https://myanimelist.net/anime/{raw.get('mal_id')}" if raw.get("mal_id") else None,
        "poster_url": raw.get("poster_url"),
        "title_original": raw.get("title_original"),
        "title_english": raw.get("title_english") or raw.get("title_english_official") or raw.get("title_original"),
        "title_synonyms": raw.get("title_synonyms"),
        "title_japanese": raw.get("title_japanese"),
        "type": raw.get("type"),
        "episodes": raw.get("episodes"),
        "mal_status": raw.get("mal_status"),
        "aired": raw.get("aired"),
        "premiered": raw.get("premiered"),
        "broadcast": raw.get("broadcast"),
        "producers": raw.get("producers") or [],
        "licensors": raw.get("licensors") or [],
        "studios": raw.get("studios") or [],
        "source": raw.get("source"),
        "genres": raw.get("genres") or [],
        "themes": raw.get("themes") or [],
        "demographic": raw.get("demographic") or [],
        "duration": raw.get("duration"),
        "rating": raw.get("rating"),
        "score": raw.get("score"),
        "scored_by": raw.get("scored_by"),
        "ranked": raw.get("ranked"),
        "popularity": raw.get("popularity"),
        "members": raw.get("members"),
        "favorites": raw.get("favorites"),
        "synopsis": raw.get("synopsis"),
        "background": raw.get("background"),
        "year": year,
        "season": season,
        "related": raw.get("related") or [],
        "available_at": raw.get("available_at") or [],
        "resources": raw.get("resources") or [],
        "streaming_platforms": raw.get("streaming_platforms") or [],
        "characters": raw.get("characters") or [],
        "staff": raw.get("staff") or [],
    }