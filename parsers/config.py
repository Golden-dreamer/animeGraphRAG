import os
import yaml

_CONFIG_PATH = os.environ.get("CONFIG_PATH", "/app/config.yaml")


class Config:
    def __init__(self, data: dict):
        self.batch_size = int(os.environ.get("BATCH_SIZE", data.get("batch_size", 50)))
        self.cycle_interval_sec = int(os.environ.get("CYCLE_INTERVAL_SEC", data.get("cycle_interval_sec", 86400)))
        self.request_delay_sec = float(os.environ.get("REQUEST_DELAY_SEC", data.get("request_delay_sec", 1.2)))
        self.cache_max_mb = int(os.environ.get("CACHE_MAX_MB", data.get("cache_max_mb", 10240)))
        self.max_attempts = int(os.environ.get("MAX_ATTEMPTS", data.get("max_attempts", 3)))
        self.retry_backoff_minutes = int(os.environ.get("RETRY_BACKOFF_MINUTES", data.get("retry_backoff_minutes", 5)))

        # правила обновления (next_check_at)
        self.refresh_current_days = int(data.get("refresh_current_days", 1))
        self.refresh_previous_days = int(data.get("refresh_previous_days", 7))
        self.refresh_recent_years = int(data.get("refresh_recent_years", 3))
        self.refresh_recent_days = int(data.get("refresh_recent_days", 365))


def load_config() -> Config:
    data = {}
    if os.path.exists(_CONFIG_PATH):
        with open(_CONFIG_PATH, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    return Config(data)
