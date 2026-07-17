"""Тесты graphrag: _extract_cypher, ask() с моками LLM + Neo4j."""
import json
from unittest.mock import patch, MagicMock

import graphrag


def _mock_cypher_and_answer(cypher_text, answer_text="Ответ на основе данных."):
    """Mock for _llm_call: returns cypher on first call, answer on second."""
    calls = []
    def mock(system_prompt, user_content, max_tokens=4096):
        calls.append(system_prompt)
        if "эксперт" in system_prompt or "Cypher" in system_prompt:
            return cypher_text
        return answer_text
    return mock


class TestExtractCypher:
    def test_plain_text(self):
        assert _extract_cypher_test("MATCH (n) RETURN n") == "MATCH (n) RETURN n"

    def test_markdown_block(self):
        text = "```cypher\nMATCH (n) RETURN n\n```"
        assert _extract_cypher_test(text) == "MATCH (n) RETURN n"

    def test_markdown_block_no_lang(self):
        text = "```\nMATCH (n) RETURN n\n```"
        assert _extract_cypher_test(text) == "MATCH (n) RETURN n"

    def test_extra_whitespace(self):
        text = "```cypher\n  MATCH (n) RETURN n  \n```"
        assert _extract_cypher_test(text) == "MATCH (n) RETURN n"


def _extract_cypher_test(text):
    """Обёртка, чтобы тестировать приватную функцию."""
    return graphrag._extract_cypher(text)


class TestAsk:
    """Тестируем ask() с замокированными LLM и Neo4j."""

    def _mock_llm_cypher(self, cypher_text):
        """Мок для _llm_call, который возвращает cypher на первый вызов."""
        calls = []

        def mock(system_prompt, user_content, max_tokens=4096):
            calls.append(system_prompt)
            if "Cypher" in system_prompt or "эксперт" in system_prompt:
                return cypher_text
            # Второй вызов — ответ
            return "Ответ на основе данных."

        return mock, calls

    def test_successful_query(self):
        mock_rows = [{"a.title": "Test Anime", "a.score": 8.5}]

        with patch.object(graphrag, "_llm_call", side_effect=_mock_cypher_and_answer("MATCH (a:Anime) RETURN a.title LIMIT 5")), \
             patch.object(graphrag, "_run_cypher", return_value=(mock_rows, None)):
            result = graphrag.ask("Какие аниме есть?")

        cfg = graphrag._cfg()
        assert result["status"] == "ok"
        assert result["rows"] == 1
        assert result["attempts"] == 1
        assert result["model"] == cfg.model
        assert result["llm_base_url"] == cfg.base_url
        assert result["duration_sec"] is not None
        assert result["cypher_raw"] is not None

    def test_invalid_question(self):
        with patch.object(graphrag, "_llm_call", side_effect=_mock_cypher_and_answer("INVALID")):
            result = graphrag.ask("Какая погода?")

        assert result["status"] == "invalid"
        assert result["rows"] == 0
        assert result["cypher"] == "INVALID"

    def test_clarify_response(self):
        with patch.object(graphrag, "_llm_call",
                          side_effect=_mock_cypher_and_answer("CLARIFY: О каком аниме идёт речь?")):
            result = graphrag.ask("Сколько серий?")

        assert result["status"] == "clarify"
        assert result["answer"] == "О каком аниме идёт речь?"
        assert result["rows"] == 0

    def test_empty_result(self):
        mock_rows = []
        with patch.object(graphrag, "_llm_call", side_effect=_mock_cypher_and_answer("MATCH (a:Anime) RETURN a")), \
             patch.object(graphrag, "_run_cypher", return_value=(mock_rows, None)):
            result = graphrag.ask("Несуществующее аниме")

        assert result["status"] == "empty"
        assert result["rows"] == 0

    def test_llm_error(self):
        def mock_fn(*a, **kw):
            raise RuntimeError("LLM is down")

        with patch.object(graphrag, "_llm_call", side_effect=mock_fn):
            result = graphrag.ask("Что угодно")

        assert result["status"] == "llm_error"
        assert "LLM is down" in result["answer"]

    def test_cypher_syntax_error_retry(self):
        """При ошибке Cypher — повтор, затем успех."""
        call_count = [0]

        def mock_fn(system_prompt, user_content, max_tokens=4096):
            call_count[0] += 1
            if "эксперт" in system_prompt:
                if call_count[0] == 1:
                    return "MATCH (a:Anime RETURN a"  # невалидный
                return "MATCH (a:Anime) RETURN a"  # исправленный
            return "Ответ"

        with patch.object(graphrag, "_llm_call", side_effect=mock_fn), \
             patch.object(graphrag, "_run_cypher") as mock_run:
            mock_run.side_effect = [
                (None, "syntax error"),  # первая попытка — ошибка
                ([{"a.title": "OK"}], None),  # вторая — успех
            ]
            result = graphrag.ask("Тест")

        assert result["status"] == "ok"
        assert result["attempts"] == 2

    def test_all_attempts_exhausted(self):
        def mock_fn(system_prompt, user_content, max_tokens=4096):
            if "эксперт" in system_prompt:
                return "MATCH (a:Anime RETURN a"  # всегда невалидный
            return "Ответ"

        with patch.object(graphrag, "_llm_call", side_effect=mock_fn), \
             patch.object(graphrag, "_run_cypher", return_value=(None, "syntax error")):
            result = graphrag.ask("Тест")

        assert result["status"] == "error"
        assert result["attempts"] == graphrag.MAX_CYPHER_ATTEMPTS

    def test_history_context_passed(self):
        with patch.object(graphrag, "_llm_call", side_effect=_mock_cypher_and_answer("MATCH (a:Anime) RETURN a LIMIT 1")), \
             patch.object(graphrag, "_run_cypher", return_value=([{"a": "b"}], None)):
            result = graphrag.ask(
                "Что ещё?",
                history=[
                    {"role": "user", "content": "Первый вопрос"},
                    {"role": "assistant", "content": "Первый ответ"},
                ],
            )
        assert result["status"] == "ok"