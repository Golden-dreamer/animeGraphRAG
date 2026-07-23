"""Конфигурация user-user-parser. Переменные окружения с дефолтами."""
from __future__ import annotations

import os


class Config:
    def __init__(self):
        # Neo4j
        self.neo4j_uri = os.environ.get("NEO4J_URI", "bolt://neo4j:7687")
        self.neo4j_user = os.environ.get("NEO4J_USER", "neo4j")
        self.neo4j_password = os.environ.get("NEO4J_PASSWORD", "")

        # MAL
        self.base_url = os.environ.get("MAL_BASE_URL", "https://myanimelist.net")
        self.user_agent = os.environ.get(
            "MAL_USER_AGENT",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        )

        # Rate limiting
        self.min_interval_sec = float(os.environ.get("API_MIN_INTERVAL_SEC", "0.5"))
        self.rate_window_sec = int(os.environ.get("API_RATE_WINDOW_SEC", "60"))
        self.rate_window_max = int(os.environ.get("API_RATE_WINDOW_MAX", "55"))
        self.max_retries = int(os.environ.get("API_MAX_RETRIES", "4"))
        self.retry_base_delay = float(os.environ.get("API_RETRY_BASE_DELAY", "1.5"))
        self.retry_max_delay = float(os.environ.get("API_RETRY_MAX_DELAY", "30"))
        self.http_timeout = int(os.environ.get("API_HTTP_TIMEOUT", "20"))

        # Batch
        self.refresh_batch_size = int(os.environ.get("USER_REFRESH_BATCH_SIZE", "50"))


def load_config() -> Config:
    return Config()