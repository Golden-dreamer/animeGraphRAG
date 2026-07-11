import os
import yaml

_CONFIG_PATH = os.environ.get("CONFIG_PATH", "/app/config.yaml")


class Config:
    def __init__(self, data: dict):
        self.batch_size = int(os.environ.get("BATCH_SIZE", data.get("batch_size", 50)))
        self.cycle_interval_sec = int(os.environ.get("CYCLE_INTERVAL_SEC", data.get("cycle_interval_sec", 86400)))
        self.request_delay_sec = float(os.environ.get("REQUEST_DELAY_SEC", data.get("request_delay_sec", 1.2)))


def load_config() -> Config:
    data = {}
    if os.path.exists(_CONFIG_PATH):
        with open(_CONFIG_PATH, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    return Config(data)