"""HTTP-клиент user-anime-parser — обёртка над MalFetcher.

Фетчит stats-страницы аниме с MyAnimeList.
"""
from __future__ import annotations

import logging

import requests

from base_fetcher import MalFetcher
from config import Config

log = logging.getLogger("user_anime_fetcher")

_fetcher = MalFetcher()


def fetch_stats_page(mal_id: int, slug: str, offset: int, cfg: Config) -> str | None:
    """Загружает HTML stats-страницы аниме.

    URL: {base_url}/anime/{id}/{slug}/stats?m=all&show={offset}
    slug может быть "_" — MAL принимает placeholder и редиректит.
    Возвращает None при 404.
    """
    url = f"{cfg.base_url}/anime/{mal_id}/{slug}/stats?m=all&show={offset}"
    try:
        return _fetcher.http_get(url)
    except requests.exceptions.HTTPError as e:
        if e.response is not None and e.response.status_code == 404:
            return None
        raise