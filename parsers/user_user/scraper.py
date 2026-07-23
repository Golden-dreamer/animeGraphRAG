"""Парсер JSON animelist пользователей MyAnimeList.

Парсит /animelist/{user}/load.json — список аниме с оценками,
статусами, эпизодами, тегами.
"""
from __future__ import annotations

from base_scraper import is_captcha_page

# Маппинг статусов из animelist JSON (int → строка)
_ANIMELIST_STATUS_MAP = {
    1: "Watching",
    2: "Completed",
    3: "On Hold",
    4: "Dropped",
    6: "Plan to Watch",
}


def parse_animelist(data: list | None) -> list[dict]:
    """Парсит JSON animelist пользователя.

    Возвращает [{mal_id, score, status, episodes_watched, tags}, ...]
    score=0 означает "без оценки" → None.
    """
    if not data or not isinstance(data, list):
        return []

    results = []
    for entry in data:
        mal_id = entry.get('anime_id')
        if mal_id is None:
            continue

        score = entry.get('score', 0)
        status_int = entry.get('status')
        status = _ANIMELIST_STATUS_MAP.get(status_int, None)
        episodes = entry.get('num_watched_episodes', 0)
        tags = entry.get('tags', '')

        results.append({
            'mal_id': mal_id,
            'score': score if score and score > 0 else None,
            'status': status,
            'episodes_watched': episodes if episodes else None,
            'tags': tags if tags else None,
        })

    return results


def is_not_found_page(html: str | None) -> bool:
    """Проверяет, является ли HTML 404-страницей (профиль не найден)."""
    if not html:
        return True
    lower = html[:5000].lower()
    return 'not found' in lower or 'page not found' in lower