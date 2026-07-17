"""Тесты session_config: defaults, update, masking."""
import os

import pytest

import session_config


@pytest.fixture
def fresh_config():
    """Сбрасывает конфиг к дефолтам перед каждым тестом."""
    cfg = session_config.get_config()
    old = session_config.asdict(cfg)
    # Reset to env defaults
    cfg.model = os.environ.get("GRAPHRAG_LLM_MODEL", "glm-5.2")
    cfg.base_url = os.environ.get("GRAPHRAG_LLM_BASE_URL", "https://ollama.com/v1")
    cfg.api_key = os.environ.get("GRAPHRAG_LLM_API_KEY",
                                  os.environ.get("OLLAMA_API_KEY", ""))
    cfg.max_tokens = int(os.environ.get("GRAPHRAG_LLM_MAX_TOKENS", "8192"))
    cfg.think = False
    cfg.cypher_prompt = ""
    cfg.answer_prompt = ""
    yield cfg
    # Restore
    for k, v in old.items():
        if hasattr(cfg, k):
            setattr(cfg, k, v)


class TestDefaults:
    def test_has_model(self, fresh_config):
        assert fresh_config.model is not None

    def test_has_base_url(self, fresh_config):
        assert fresh_config.base_url is not None

    def test_think_default_false(self, fresh_config):
        assert fresh_config.think is False

    def test_prompts_default_empty(self, fresh_config):
        assert fresh_config.cypher_prompt == ""
        assert fresh_config.answer_prompt == ""


class TestGetSettings:
    def test_returns_dict(self, fresh_config):
        data = session_config.get_settings()
        assert isinstance(data, dict)
        assert "model" in data
        assert "base_url" in data

    def test_api_key_masked(self, fresh_config):
        fresh_config.api_key = "sk-1234567890abcdef"
        data = session_config.get_settings()
        assert "••••" in data["api_key"]
        assert data["api_key"].startswith("sk-1")
        assert data["api_key"].endswith("cdef")

    def test_api_key_empty_when_no_key(self, fresh_config):
        fresh_config.api_key = ""
        data = session_config.get_settings()
        assert data["api_key"] == ""


class TestUpdateSettings:
    def test_update_model(self, fresh_config):
        result = session_config.update_settings(model="qwen3.5:9b")
        assert fresh_config.model == "qwen3.5:9b"
        assert result["model"] == "qwen3.5:9b"

    def test_update_think(self, fresh_config):
        session_config.update_settings(think=True)
        assert fresh_config.think is True

    def test_update_base_url(self, fresh_config):
        session_config.update_settings(base_url="http://localhost:11434/v1")
        assert fresh_config.base_url == "http://localhost:11434/v1"

    def test_update_max_tokens(self, fresh_config):
        session_config.update_settings(max_tokens=4096)
        assert fresh_config.max_tokens == 4096

    def test_update_api_key(self, fresh_config):
        session_config.update_settings(api_key="sk-new-key-123456")
        assert fresh_config.api_key == "sk-new-key-123456"

    def test_update_none_does_not_change(self, fresh_config):
        original = fresh_config.model
        session_config.update_settings(model=None)
        assert fresh_config.model == original

    def test_update_returns_masked_key(self, fresh_config):
        fresh_config.api_key = "sk-1234567890abcdef"
        result = session_config.update_settings(model="test")
        assert "••••" in result["api_key"]