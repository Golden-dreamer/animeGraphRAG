"""HTTP-клиент для MyAnimeList с rate limiter и retry.

Дедуплицирован из parsers/anime/fetcher.py и parsers/users/user_fetcher.py.
Общая логика: двойной лимит (минимальный интервал + sliding window),
ретраи с экспоненциальным backoff, CAPTCHA-детекция.

Каждый парсер создаёт свой экземпляр MalFetcher со своим state
(чтобы лимиты считались независимо для каждого контейнера).

Kill switch: _kill_event (threading.Event) проверяется перед каждым
HTTP-запросом. coordinator выставляет его через POST /pause — все
фетчеры немедленно бросают PauseRequested, цикл останавливается
за время не более одного HTTP-таймаута (≈20 сек), а не "до конца элемента".
"""
from __future__ import annotations

import logging
import os
import threading
import time
from collections import deque

import requests

from base_scraper import is_captcha_page

log = logging.getLogger("mal_fetcher")


class PauseRequested(Exception):
    """Координатор запросил паузу — немедленно прервать работу."""


# Глобальный kill event — общий для всех MalFetcher в процессе.
# Выставляется BaseParser.set_paused(True), сбрасывается set_paused(False).
_kill_event = threading.Event()


def trigger_pause():
    """Выставить kill — все HTTP-запросы немедленно бросят PauseRequested."""
    _kill_event.set()


def clear_pause():
    """Сбросить kill."""
    _kill_event.clear()


def is_kill_set() -> bool:
    return _kill_event.is_set()


class MalFetcher:
    """HTTP-клиент для MyAnimeList с лимитами и ретраями.

    Параметры читаются из env при создании. Каждый экземпляр имеет
    собственное состояние рейт-лимитера (timestamps, penalty).
    """

    def __init__(self):
        self.base_url = os.environ.get("MAL_BASE_URL", "https://myanimelist.net")
        self.user_agent = os.environ.get(
            "MAL_USER_AGENT",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        )
        self.min_interval_sec = float(os.environ.get("API_MIN_INTERVAL_SEC", "0.5"))
        self.rate_window_sec = int(os.environ.get("API_RATE_WINDOW_SEC", "60"))
        self.rate_window_max = int(os.environ.get("API_RATE_WINDOW_MAX", "55"))
        self.max_retries = int(os.environ.get("API_MAX_RETRIES", "4"))
        self.retry_base_delay = float(os.environ.get("API_RETRY_BASE_DELAY", "1.5"))
        self.retry_max_delay = float(os.environ.get("API_RETRY_MAX_DELAY", "30"))
        self.http_timeout = int(os.environ.get("API_HTTP_TIMEOUT", "20"))

        # Состояние рейт-лимитера (per-instance)
        self._last_request_ts = 0.0
        self._request_timestamps: deque[float] = deque()
        self._429_penalty_until = 0.0

    def _rate_limit(self):
        """Двойной лимит: не чаще 1 запрос в min_interval_sec + не более
        rate_window_max запросов за последние rate_window_sec секунд.
        При недавнем 429 — удваиваем интервал.
        """
        if _kill_event.is_set():
            raise PauseRequested("kill switch activated before rate_limit")

        now = time.monotonic()

        min_interval = self.min_interval_sec
        if self._429_penalty_until > now:
            min_interval = self.min_interval_sec * 2

        elapsed = now - self._last_request_ts
        if elapsed < min_interval:
            # Спим короткими кусочками — чтобы kill event сработал быстро
            remaining = min_interval - elapsed
            while remaining > 0 and not _kill_event.is_set():
                time.sleep(min(remaining, 2.0))
                remaining = min_interval - (time.monotonic() - self._last_request_ts)
            if _kill_event.is_set():
                raise PauseRequested("kill switch activated during rate_limit sleep")
            now = time.monotonic()

        self._request_timestamps.append(now)
        while self._request_timestamps and (now - self._request_timestamps[0]) >= self.rate_window_sec:
            self._request_timestamps.popleft()

        if len(self._request_timestamps) > self.rate_window_max:
            sleep_for = self.rate_window_sec - (now - self._request_timestamps[0]) + 0.1
            log.debug("rate limit: sleeping %.1fs (minute window full)", sleep_for)
            time.sleep(sleep_for)
            now = time.monotonic()
            while self._request_timestamps and (now - self._request_timestamps[0]) >= self.rate_window_sec:
                self._request_timestamps.popleft()

        self._last_request_ts = time.monotonic()

    def http_get(self, url: str) -> str:
        """HTTP GET с ретраями на транзиентные ошибки.

        Возвращает HTML-текст. Бросает requests.exceptions.HTTPError
        для 4xx (кроме 429) и 5xx без ретрая.
        """
        last_exc = None
        for attempt in range(1, self.max_retries + 1):
            if _kill_event.is_set():
                raise PauseRequested("kill switch activated in http_get")
            self._rate_limit()
            try:
                resp = requests.get(
                    url,
                    timeout=self.http_timeout,
                    headers={
                        "User-Agent": self.user_agent,
                        "Accept": "text/html,application/xhtml+xml,application/json",
                        "Accept-Language": "en-US,en;q=0.9",
                    },
                )
                resp.raise_for_status()
                html = resp.text

                if is_captcha_page(html):
                    log.warning("CAPTCHA detected on %s, backing off 60s", url)
                    self._429_penalty_until = time.monotonic() + 60
                    time.sleep(60)
                    last_exc = requests.exceptions.RequestException("CAPTCHA page")
                    continue

                return html

            except requests.exceptions.SSLError as e:
                last_exc = e
                log.warning("SSL error (attempt %d/%d) for %s: %s", attempt, self.max_retries, url, e)
                self._429_penalty_until = time.monotonic() + 30
            except requests.exceptions.ConnectionError as e:
                last_exc = e
                log.warning("Connection error (attempt %d/%d) for %s: %s", attempt, self.max_retries, url, e)
                self._429_penalty_until = time.monotonic() + 30
            except requests.exceptions.Timeout as e:
                last_exc = e
                log.warning("Timeout (attempt %d/%d) for %s: %s", attempt, self.max_retries, url, e)
                self._429_penalty_until = time.monotonic() + 30
            except requests.exceptions.HTTPError as e:
                resp = e.response
                status = resp.status_code if resp is not None else 0
                if status == 429:
                    last_exc = e
                    retry_after = resp.headers.get("Retry-After") if resp is not None else None
                    delay = float(retry_after) if retry_after else self.retry_base_delay * (2 ** attempt)
                    log.warning("429 (attempt %d/%d) for %s, sleeping %.1fs", attempt, self.max_retries, url, delay)
                    time.sleep(delay)
                    self._429_penalty_until = time.monotonic() + 60
                    continue
                elif 500 <= status < 600:
                    log.warning("HTTP %d for %s — не ретраим", status, url)
                    raise
                else:
                    raise
            except requests.exceptions.RequestException as e:
                last_exc = e
                log.warning("Request error (attempt %d/%d) for %s: %s", attempt, self.max_retries, url, e)

            if attempt < self.max_retries:
                if _kill_event.is_set():
                    raise PauseRequested("kill switch activated before retry delay")
                delay = min(self.retry_base_delay * (2 ** (attempt - 1)), self.retry_max_delay)
                # Спим короткими кусочками для быстрого kill
                slept = 0.0
                while slept < delay and not _kill_event.is_set():
                    chunk = min(delay - slept, 2.0)
                    time.sleep(chunk)
                    slept += chunk
                if _kill_event.is_set():
                    raise PauseRequested("kill switch activated during retry delay")

        if last_exc is not None:
            raise last_exc
        raise requests.exceptions.RequestException(f"Unknown error fetching {url}")