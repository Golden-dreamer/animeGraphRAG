import hashlib
import json
import os
import time
from pathlib import Path

import requests

CACHE_DIR = Path(os.environ.get("CACHE_DIR", "/app/cache"))
BASE_URL = "https://api.jikan.moe/v4"

_last_request_ts = 0.0


def _cache_key(url: str, params: dict) -> str:
    raw = url + json.dumps(params, sort_keys=True)
    return hashlib.sha256(raw.encode()).hexdigest()


def _cache_path(key: str) -> Path:
    return CACHE_DIR / f"{key}.json"


def _rate_limit(delay_sec: float):
    global _last_request_ts
    elapsed = time.monotonic() - _last_request_ts
    if elapsed < delay_sec:
        time.sleep(delay_sec - elapsed)
    _last_request_ts = time.monotonic()


def _http_get(url: str, params: dict, delay_sec: float) -> dict:
    _rate_limit(delay_sec)
    resp = requests.get(url, params=params, timeout=20)
    resp.raise_for_status()
    return resp.json()


def cached_get(url: str, params: dict, delay_sec: float, force: bool = False) -> dict:
    """GET с файловым кэшем. force=True игнорирует кэш и перезаписывает его."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    key = _cache_key(url, params)
    path = _cache_path(key)

    if path.exists() and not force:
        return json.loads(path.read_text(encoding="utf-8"))

    data = _http_get(url, params, delay_sec)
    path.write_text(json.dumps(data), encoding="utf-8")
    return data


def get_season_list(year: int, season: str, delay_sec: float, force: bool = False) -> list[dict]:
    """Список тайтлов сезона (с пагинацией). Возвращает [{mal_id, ...}, ...]."""
    url = f"{BASE_URL}/seasons/{year}/{season}"
    page = 1
    results = []
    while True:
        data = cached_get(url, {"page": page}, delay_sec, force=force)
        items = data.get("data", [])
        results.extend({"mal_id": item["mal_id"]} for item in items if "mal_id" in item)
        pagination = data.get("pagination", {})
        if not pagination.get("has_next_page"):
            break
        page += 1
    return results


def get_anime_full(mal_id: int, delay_sec: float, force: bool = False) -> dict:
    url = f"{BASE_URL}/anime/{mal_id}/full"
    return cached_get(url, {}, delay_sec, force=force)


def cleanup_cache_if_over_limit(max_mb: int):
    if not CACHE_DIR.exists():
        return
    files = sorted(CACHE_DIR.glob("*.json"), key=lambda f: f.stat().st_mtime)
    total = sum(f.stat().st_size for f in files)
    limit = max_mb * 1024 * 1024
    for f in files:
        if total <= limit:
            break
        total -= f.stat().st_size
        f.unlink(missing_ok=True)
