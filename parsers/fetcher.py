"""HTTP-клиент для MyAnimeList с лимитами.

Парсит HTML напрямую с myanimelist.net.
Лимиты:
  - не более 3 запросов в секунду (минимум ~0.5 сек между запросами)
  - не более 60 запросов в минуту (скользящее окно)

Все сетевые ошибки (SSL, timeout, 429, 5xx) ретраятся с экспоненциальным
бэкоффом — до MAX_RETRIES попыток.

Лимиты, и таймауты настраиваются через переменные окружения
(см. .env и docker-compose.yml).
"""
from __future__ import annotations

import logging
import os
import time
from collections import deque

import requests

log = logging.getLogger("fetcher")

BASE_URL = os.environ.get("MAL_BASE_URL", "https://myanimelist.net")

USER_AGENT = os.environ.get(
    "MAL_USER_AGENT",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
)

# --- лимиты (настраиваются через env, те же что у Jikan) ---
MIN_INTERVAL_SEC = float(os.environ.get("API_MIN_INTERVAL_SEC", "0.5"))
RATE_WINDOW_SEC = int(os.environ.get("API_RATE_WINDOW_SEC", "60"))
RATE_WINDOW_MAX = int(os.environ.get("API_RATE_WINDOW_MAX", "55"))

# --- ретраи (настраиваются через env) ---
MAX_RETRIES = int(os.environ.get("API_MAX_RETRIES", "4"))
RETRY_BASE_DELAY = float(os.environ.get("API_RETRY_BASE_DELAY", "1.5"))
RETRY_MAX_DELAY = float(os.environ.get("API_RETRY_MAX_DELAY", "30"))
HTTP_TIMEOUT = int(os.environ.get("API_HTTP_TIMEOUT", "20"))

# --- состояние рейт-лимитера ---
_last_request_ts = 0.0
_request_timestamps: deque[float] = deque()
_429_penalty_until = 0.0


def _rate_limit():
    """Двойной лимит: не чаще 1 запрос в MIN_INTERVAL_SEC + не более
    RATE_WINDOW_MAX запросов за последние RATE_WINDOW_SEC секунд.
    Если недавно был 429/5xx — удваиваем интервал до окончания пенальти.
    """
    global _last_request_ts, _request_timestamps

    now = time.monotonic()

    min_interval = MIN_INTERVAL_SEC
    if _429_penalty_until > now:
        min_interval = MIN_INTERVAL_SEC * 2

    elapsed = now - _last_request_ts
    if elapsed < min_interval:
        sleep_for = min_interval - elapsed
        time.sleep(sleep_for)
        now = time.monotonic()

    _request_timestamps.append(now)
    while _request_timestamps and (now - _request_timestamps[0]) >= RATE_WINDOW_SEC:
        _request_timestamps.popleft()

    if len(_request_timestamps) > RATE_WINDOW_MAX:
        sleep_for = RATE_WINDOW_SEC - (now - _request_timestamps[0]) + 0.1
        log.debug("rate limit: sleeping %.1fs (minute window full)", sleep_for)
        time.sleep(sleep_for)
        now = time.monotonic()
        while _request_timestamps and (now - _request_timestamps[0]) >= RATE_WINDOW_SEC:
            _request_timestamps.popleft()

    _last_request_ts = time.monotonic()


def _http_get_with_retry(url: str) -> str:
    """HTTP GET с ретраями на транзиентные ошибки. Возвращает HTML-текст."""
    global _429_penalty_until
    last_exc = None
    for attempt in range(1, MAX_RETRIES + 1):
        _rate_limit()
        try:
            resp = requests.get(
                url,
                timeout=HTTP_TIMEOUT,
                headers={
                    "User-Agent": USER_AGENT,
                    "Accept": "text/html,application/xhtml+xml",
                    "Accept-Language": "en-US,en;q=0.9",
                },
            )
            resp.raise_for_status()
            return resp.text
        except requests.exceptions.SSLError as e:
            last_exc = e
            log.warning("SSL error (attempt %d/%d) for %s: %s", attempt, MAX_RETRIES, url, e)
            _429_penalty_until = time.monotonic() + 30
        except requests.exceptions.ConnectionError as e:
            last_exc = e
            log.warning("Connection error (attempt %d/%d) for %s: %s", attempt, MAX_RETRIES, url, e)
            _429_penalty_until = time.monotonic() + 30
        except requests.exceptions.Timeout as e:
            last_exc = e
            log.warning("Timeout (attempt %d/%d) for %s: %s", attempt, MAX_RETRIES, url, e)
            _429_penalty_until = time.monotonic() + 30
        except requests.exceptions.HTTPError as e:
            resp = e.response
            status = resp.status_code if resp is not None else 0
            if status == 429:
                last_exc = e
                retry_after = resp.headers.get("Retry-After") if resp is not None else None
                delay = float(retry_after) if retry_after else RETRY_BASE_DELAY * (2 ** attempt)
                log.warning("429 Too Many Requests (attempt %d/%d) for %s, sleeping %.1fs",
                            attempt, MAX_RETRIES, url, delay)
                time.sleep(delay)
                _429_penalty_until = time.monotonic() + 60
                continue
            elif 500 <= status < 600:
                log.warning("HTTP %d for %s — не ретраим, откладываем", status, url)
                raise
            else:
                raise
        except requests.exceptions.RequestException as e:
            last_exc = e
            log.warning("Request error (attempt %d/%d) for %s: %s", attempt, MAX_RETRIES, url, e)

        if attempt < MAX_RETRIES:
            delay = min(RETRY_BASE_DELAY * (2 ** (attempt - 1)), RETRY_MAX_DELAY)
            log.debug("retry %d/%d in %.1fs for %s", attempt, MAX_RETRIES, delay, url)
            time.sleep(delay)

    if last_exc is not None:
        raise last_exc
    raise requests.exceptions.RequestException(f"Unknown error fetching {url}")


def get_html(url: str) -> str:
    """HTTP GET с ретраями и лимитами. Возвращает HTML-текст."""
    return _http_get_with_retry(url)


def get_season_list(year: int, season: str, delay_sec: float) -> list[dict]:
    """Список тайтлов сезона. Возвращает [{mal_id, title, url}, ...].

    delay_sec не используется — рейт-лимитинг управляется внутренним механизмом.
    Параметр сохранён для обратной совместимости.
    """
    from mal_scraper import parse_season_page

    url = f"{BASE_URL}/anime/season/{year}/{season}"
    html = _http_get_with_retry(url)
    return parse_season_page(html)


def get_anime_full(mal_id: int, delay_sec: float) -> dict:
    """Парсит основную страницу аниме + страницу characters/staff.
    Возвращает объединённый dict со всеми полями.

    delay_sec не используется — сохранён для обратной совместимости.

    ВАЖНО: для страницы characters используется полный URL со slug
    (/anime/{id}/{slug}/characters), а не короткий (/anime/{id}/characters).
    MAL редиректит короткий URL на основную страницу аниме (без /characters),
    где staff ограничен несколькими людьми. Полный URL отдаёт отдельную
    страницу /characters с полным списком staff.
    """
    from mal_scraper import parse_anime_page, parse_characters_page, extract_slug_from_url

    # Основная страница — MAL редиректит короткий URL на полный со slug
    url_main = f"{BASE_URL}/anime/{mal_id}"
    html_main = _http_get_with_retry(url_main)
    data = parse_anime_page(html_main)
    if data is None:
        return {}

    # Извлекаем slug из canonical URL основной страницы
    # (og:url или link[rel=canonical] вида https://myanimelist.net/anime/5249/Mitsume_ga_Tooru)
    slug = extract_slug_from_url(html_main)
    if slug:
        url_chars = f"{BASE_URL}/anime/{mal_id}/{slug}/characters"
    else:
        # Fallback: короткий URL (MAL редиректит, но staff будет неполным)
        url_chars = f"{BASE_URL}/anime/{mal_id}/characters"

    html_chars = _http_get_with_retry(url_chars)
    chars_data = parse_characters_page(html_chars)

    data['characters'] = chars_data.get('characters', [])
    data['staff'] = chars_data.get('staff', [])

    return data