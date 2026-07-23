"""Конфигурация airing-parser. Переменные окружения с дефолтами."""
from __future__ import annotations

import os


class Config:
    def __init__(self):
        self.batch_size = int(os.environ.get("BATCH_SIZE", "50"))
        self.cycle_interval_sec = int(os.environ.get("CYCLE_INTERVAL_SEC", "86400"))
        self.request_delay_sec = float(os.environ.get("REQUEST_DELAY_SEC", "1.2"))


def load_config() -> Config:
    return Config()