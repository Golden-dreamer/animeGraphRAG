"""HTTP-клиент для MyAnimeList — обёртка над MalFetcher.

Сохраняет module-level API для обратной совместимости:
  fetcher.get_html(url), fetcher.get_anime_full(id, delay), etc.

Лимиты и retry вынесены в base_fetcher.MalFetcher.
"""
from __future__ import annotations

import logging
import os

from base_fetcher import MalFetcher

log = logging.getLogger("fetcher")

# Единственный экземпляр с state рейт-лимитера
_fetcher = MalFetcher()

# Совместимость: модули читают эти атрибуты напрямую
BASE_URL = _fetcher.base_url
MIN_INTERVAL_SEC = _fetcher.min_interval_sec
RATE_WINDOW_SEC = _fetcher.rate_window_sec
RATE_WINDOW_MAX = _fetcher.rate_window_max
MAX_RETRIES = _fetcher.max_retries
RETRY_BASE_DELAY = _fetcher.retry_base_delay
RETRY_MAX_DELAY = _fetcher.retry_max_delay
HTTP_TIMEOUT = _fetcher.http_timeout


def get_html(url: str) -> str:
    """HTTP GET с ретраями и лимитами. Возвращает HTML-текст."""
    return _fetcher.http_get(url)


def get_season_list(year: int, season: str, delay_sec: float) -> list[dict]:
    """Список тайтлов сезона. Возвращает [{mal_id, title, url}, ...].

    delay_sec не используется — рейт-лимитинг управляется внутренним механизмом.
    Параметр сохранён для обратной совместимости.
    """
    from mal_scraper import parse_season_page

    url = f"{BASE_URL}/anime/season/{year}/{season}"
    html = _fetcher.http_get(url)
    return parse_season_page(html)


def get_anime_full(mal_id: int, delay_sec: float) -> dict:
    """Парсит основную страницу аниме + страницу characters/staff.
    Возвращает объединённый dict со всеми полями.

    delay_sec не используется — сохранён для обратной совместимости.
    """
    from mal_scraper import parse_anime_page, parse_characters_page, extract_slug_from_url

    url_main = f"{BASE_URL}/anime/{mal_id}"
    html_main = _fetcher.http_get(url_main)
    data = parse_anime_page(html_main)
    if data is None:
        return {}

    slug = extract_slug_from_url(html_main)
    if slug:
        url_chars = f"{BASE_URL}/anime/{mal_id}/{slug}/characters"
    else:
        url_chars = f"{BASE_URL}/anime/{mal_id}/characters"

    html_chars = _fetcher.http_get(url_chars)
    chars_data = parse_characters_page(html_chars)

    data['characters'] = chars_data.get('characters', [])
    data['staff'] = chars_data.get('staff', [])

    return data