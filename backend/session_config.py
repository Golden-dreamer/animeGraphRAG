"""In-memory session config for GraphRAG LLM settings.

Defaults come from environment variables. Changes via /api/settings
apply for the current container session only — not persisted.
"""
import os
from dataclasses import dataclass, field, asdict


def _env(key: str, default: str = "") -> str:
    return os.environ.get(key, default)


@dataclass
class SessionConfig:
    model: str = field(default_factory=lambda: _env("GRAPHRAG_LLM_MODEL", "glm-5.2"))
    base_url: str = field(default_factory=lambda: _env("GRAPHRAG_LLM_BASE_URL", "https://ollama.com/v1"))
    api_key: str = field(default_factory=lambda: _env("GRAPHRAG_LLM_API_KEY",
                                                       _env("OLLAMA_API_KEY", "")))
    max_tokens: int = field(default_factory=lambda: int(_env("GRAPHRAG_LLM_MAX_TOKENS", "8192")))
    think: bool = False
    cypher_prompt: str = ""  # empty = use default from graphrag.py
    answer_prompt: str = ""  # empty = use default from graphrag.py

    def to_dict(self) -> dict:
        d = asdict(self)
        # Не показываем api_key в GET для безопасности — только mask
        if d["api_key"]:
            d["api_key"] = d["api_key"][:4] + "••••" + d["api_key"][-4:]
        else:
            d["api_key"] = ""
        return d

    def update(self, **kwargs) -> dict:
        """Применяет изменения. Возвращает обновлённый dict (с masked key)."""
        for key in ("model", "base_url", "api_key", "cypher_prompt", "answer_prompt"):
            if key in kwargs and kwargs[key] is not None:
                setattr(self, key, kwargs[key])
        if "max_tokens" in kwargs and kwargs["max_tokens"] is not None:
            self.max_tokens = int(kwargs["max_tokens"])
        if "think" in kwargs and kwargs["think"] is not None:
            self.think = bool(kwargs["think"])
        return self.to_dict()


# Синглтон — одна конфигурация на процесс
_config = SessionConfig()


def get_config() -> SessionConfig:
    return _config


def get_settings() -> dict:
    return _config.to_dict()


def update_settings(**kwargs) -> dict:
    return _config.update(**kwargs)