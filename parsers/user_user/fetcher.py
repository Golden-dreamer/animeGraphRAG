"""HTTP-клиент user-user-parser — обёртка над MalFetcher.

Фетчит animelist пользователей и проверяет профили.
"""
from __future__ import annotations

import json
import logging

import requests

from base_fetcher import MalFetcher
from config import Config

log = logging.getLogger("user_user_fetcher")

_fetcher = MalFetcher()


def fetch_animelist(username: str, cfg: Config) -> list | None:
    """Загружает JSON animelist пользователя.

    Возвращает список dict'ов или None при 404 (профиль удалён).
    Пагинация: offset=0,300,600... (MAL отдаёт по 300).
    """
    all_entries = []
    offset = 0
    while True:
        url = f"{cfg.base_url}/animelist/{username}/load.json?offset={offset}&status=7"
        try:
            text = _fetcher.http_get(url)
        except requests.exceptions.HTTPError as e:
            if e.response is not None and e.response.status_code == 404:
                return None
            raise
        data = json.loads(text)
        if not data:
            break
        all_entries.extend(data)
        if len(data) < 300:
            break
        offset += 300
    return all_entries


def check_profile(username: str, cfg: Config) -> bool:
    """Проверяет, существует ли профиль пользователя. True = существует."""
    url = f"{cfg.base_url}/profile/{username}"
    try:
        _fetcher.http_get(url)
        return True
    except requests.exceptions.HTTPError as e:
        if e.response is not None and e.response.status_code == 404:
            return False
        raise