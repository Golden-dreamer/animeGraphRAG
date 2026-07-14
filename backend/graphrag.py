"""GraphRAG engine: question → LLM генерирует Cypher → Neo4j → LLM формулирует ответ.

Two-step pipeline:
  1. text→Cypher: LLM получает схему графа + вопрос, генерирует Cypher-запрос
  2. result→answer: LLM получает результат из Neo4j, формулирует ответ на русском

Самопроверка: если Cypher упал с синтаксической ошибкой — бэкенд возвращает
ошибку LLM, та пробует снова (до 3 попыток).
"""
import json
import logging
import os
import re

import requests
from neo4j import GraphDatabase

import db

log = logging.getLogger("graphrag")

# --- Конфигурация LLM ---

LLM_BASE_URL = os.environ.get("GRAPHRAG_LLM_BASE_URL", "https://ollama.com/v1")
LLM_API_KEY = os.environ.get("GRAPHRAG_LLM_API_KEY", os.environ.get("OLLAMA_API_KEY", ""))
LLM_MODEL = os.environ.get("GRAPHRAG_LLM_MODEL", "glm-5.2")
LLM_MAX_TOKENS = int(os.environ.get("GRAPHRAG_LLM_MAX_TOKENS", "8192"))

# --- Конфигурация Neo4j ---

NEO4J_URI = os.environ.get("NEO4J_URI", "bolt://neo4j:7687")
NEO4J_USER = os.environ.get("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.environ.get("NEO4J_PASSWORD", "")

MAX_CYHPER_ATTEMPTS = 3

# --- Схема графа (для system prompt) ---

GRAPH_SCHEMA = """\
Узлы:
  :Anime        — mal_id, title, title_original, title_english, title_synonyms, title_japanese,
                  poster_url, mal_url, type, episodes, mal_status, aired, premiered, broadcast,
                  source, duration, rating, score, scored_by, ranked, popularity, members,
                  favorites, synopsis, background, year, season
  :Genre        — name
  :Studio       — name
  :Producer     — name
  :Character    — mal_id, name, url
  :Person       — mal_id, name, url
  :ExternalLink — url, name
  :StreamingPlatform — name
  :Manga        — mal_id, title

Связи:
  (Anime)-[:HAS_GENRE]->(Genre)
  (Anime)-[:HAS_THEME]->(Genre)
  (Anime)-[:HAS_DEMOGRAPHIC]->(Genre)
  (Anime)-[:PRODUCED_BY]->(Studio)
  (Anime)-[:PRODUCER_OF]->(Producer)
  (Anime)-[:LICENSED_BY]->(Producer)
  (Anime)-[:RELATED_TO {relation, target_type}]->(Anime | Manga)
  (Anime)-[:HAS_CHARACTER {role}]->(Character)
  (Anime)-[:STAFF {roles}]->(Person)
  (Person)-[:VOICE_ACTED {language, anime_id}]->(Character)
  (Anime)-[:STREAMING_ON {url, available}]->(StreamingPlatform)
  (Anime)-[:AVAILABLE_AT]->(ExternalLink)
  (Anime)-[:HAS_RESOURCE]->(ExternalLink)

Индексы: Anime.mal_id, Person.mal_id, Character.mal_id, Manga.mal_id,
Genre.name, Studio.name, Producer.name — UNIQUE CONSTRAINT.
Anime.mal_status — INDEX.

Особенности:
  :Anime.title — алиас title_original, отображается в Neo4j Browser.
  title IS NULL = тайтл зарегистрирован, но не обработан (stub).
  Person и Character уникальны по mal_id, не по имени (могут быть однофамильцы).
  STAFF.roles — список строк (director, producer, script, key animation, ...).
  Anime.year — число, Anime.season — lowercase (winter/spring/summer/fall).
"""

CYPHER_SYSTEM_PROMPT = f"""\
Ты — эксперт по Cypher-запросам к Neo4j. Тебе задаёт вопрос пользователь на русском.
Твоя задача — составить Cypher-запрос к графу MyAnimeList и вернуть ТОЛЬКО запрос,
без объяснений, без markdown-блоков, без лишнего текста.

Схема графа:
{GRAPH_SCHEMA}

Правила:
1. Возвращай ТОЛЬКО валидный Cypher. Никаких ```cypher блоков, никаких комментариев.
2. ВАЖНО: направление связей! Используй <-[:REL]- для обхода от Person к Anime:
   - (Anime)-[:STAFF]->(Person)      правильно: MATCH (a:Anime)-[:STAFF]->(p:Person)
   - все проекты Person:              MATCH (a:Anime)-[:STAFF]->(p:Person {{mal_id: X}})
   - НЕ пиши (p:Person)-[:STAFF]->(a:Anime) — это не вернёт результатов!
   - (Anime)-[:HAS_CHARACTER]->(Character)
   - (Person)-[:VOICE_ACTED]->(Character)
   - (Anime)-[:PRODUCED_BY]->(Studio)
3. Названия аниме в БД на английском/ромадзи (Shingeki no Kyojin, Attack on Titan).
   НЕ ищи по русским названиям — переводи в английский эквивалент.
4. Для поиска по названию: toLower(a.title) CONTAINS toLower('...') OR \
   toLower(a.title_english) CONTAINS toLower('...').
5. "Режиссёр" = ANY(r IN rel.roles WHERE toLower(r) = 'director').
   'Director' и 'ADR Director' — разные. Используй exact match toLower(r) = 'director'.
6. "Сэйю"/"актёр озвучки" = Person через VOICE_ACTED к Character.
7. LIMIT — ТОЛЬКО когда пользователь явно просит конкретное число ("топ-5", \
"лучшие 3", "покажи 10"). Во всех остальных случаях НЕ добавляй LIMIT — \
бэкенд сам ограничит объём данных. Произвольный LIMIT обрезает результаты \
и приводит к неполным ответам.
8. Сортируй по score DESC или year, если релевантно.
9. Для "все проекты человека": сначала найди Person по имени/ID, потом\
   MATCH (a:Anime)-[:STAFF]->(p) RETURN a.title.
10. Если запрос не имеет смысла в этой схеме — верни "INVALID".
11. Если вопрос неоднозначен или не хватает данных для точного запроса \
   (например, неясно о каком именно тайтле речь, или пользователь не уточнил \
   что именно хочет узнать) — верни "CLARIFY: <уточняющий вопрос на русском>".
   Пример: вопрос "сколько серий" без указания тайтла → \
   "CLARIFY: О каком аниме идёт речь?"
12. Ответы на русском потом сформулирует другая часть системы — ты только Cypher.
"""

ANSWER_SYSTEM_PROMPT = """\
Ты — ассистент по аниме. Тебе задали вопрос, ты выполнил запрос к базе данных MyAnimeList.
Сейчас тебе вернулся результат. Сформулируй ответ на русском языке.

Правила:
1. Отвечай ТОЛЬКО на основе данных из результата запроса. Не придумывай факты.
2. Если результат пустой — скажи честно: "Не нашёл информации по этому запросу."
3. Если данных мало — перечисли что есть, не добавляй от себя.
4. Форматируй читаемо: списки, таблицы если уместно.
5. Кратко и по делу. Без воды.
6. На русском.
"""


def _llm_call(system_prompt: str, user_content: str, max_tokens: int = 4096) -> str:
    """Вызов LLM через OpenAI-compatible API."""
    resp = requests.post(
        f"{LLM_BASE_URL}/chat/completions",
        headers={
            "Authorization": f"Bearer {LLM_API_KEY}",
            "Content-Type": "application/json",
        },
        json={
            "model": LLM_MODEL,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ],
            "max_tokens": max_tokens,
            "temperature": 0.1,
        },
        timeout=60,
    )
    resp.raise_for_status()
    data = resp.json()
    return data["choices"][0]["message"]["content"].strip()


def _extract_cypher(text: str) -> str:
    """Извлекает Cypher из ответа LLM (убирает markdown-блоки если есть)."""
    # Если LLM обернула в ```cypher ... ``` — извлекаем
    m = re.search(r'```(?:cypher)?\s*\n(.*?)\n```', text, re.DOTALL)
    if m:
        return m.group(1).strip()
    return text.strip()


def _run_cypher(cypher: str) -> tuple[list[dict] | None, str | None]:
    """Выполняет Cypher в Neo4j. Возвращает (rows, error)."""
    driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
    try:
        with driver.session() as session:
            result = session.run(cypher)
            rows = [dict(r) for r in result]
            return rows, None
    except Exception as e:
        return None, str(e)
    finally:
        driver.close()


def ask(question: str, chat_id: str = None, history: list[dict] = None) -> dict:
    """Полный пайплайн: question → Cypher → Neo4j → answer.

    Возвращает: {answer, cypher, status, rows, attempts, error, model,
                 llm_base_url, duration_sec, cypher_raw}
    """
    import time
    t0 = time.time()

    # Шаг 1: генерируем Cypher
    history_context = ""
    if history:
        last = history[-6:]  # последние 3 пары
        history_context = "\n\nПредыдущий контекст разговора:\n"
        for m in last:
            role = "Пользователь" if m["role"] == "user" else "Ответ"
            history_context += f"{role}: {m['content'][:200]}\n"

    cypher = None
    error = None
    rows = None
    attempts = 0
    raw = None

    for attempt in range(1, MAX_CYHPER_ATTEMPTS + 1):
        attempts = attempt
        if attempt == 1:
            user_msg = f"Вопрос: {question}{history_context}"
        else:
            user_msg = f"Вопрос: {question}\n\nПредыдущая попытка Cypher упала с ошибкой:\n{error}\n\nИсправь запрос и попробуй снова."

        log.info("Cypher attempt %d/%d for: %s", attempt, MAX_CYHPER_ATTEMPTS, question[:80])

        try:
            raw = _llm_call(CYPHER_SYSTEM_PROMPT, user_msg, max_tokens=2048)
        except Exception as e:
            log.error("LLM call failed: %s", e)
            return {
                "answer": f"Ошибка при обращении к LLM: {e}",
                "cypher": None,
                "status": "llm_error",
                "rows": 0,
                "attempts": attempts,
                "error": str(e),
                "model": LLM_MODEL,
                "llm_base_url": LLM_BASE_URL,
                "duration_sec": round(time.time() - t0, 2),
                "cypher_raw": None,
            }

        cypher = _extract_cypher(raw)

        # Пустой ответ или INVALID — вопрос не подходит для графа
        if not cypher.strip() or cypher.upper().strip() == "INVALID":
            log.info("LLM returned empty/INVALID for this question")
            if chat_id:
                db.log_query(chat_id, question, cypher or "(empty)", "invalid", 0, None, attempts,
                              model=LLM_MODEL, llm_base_url=LLM_BASE_URL,
                              answer="Не нашёл информации по этому запросу.",
                              duration_sec=round(time.time() - t0, 2), cypher_raw=raw)
            return {
                "answer": "Не нашёл информации по этому запросу. Я могу отвечать на вопросы о тайтлах, студиях, жанрах, персонажах, сэйю, режиссёрах и связях между ними.",
                "cypher": cypher or "(empty)",
                "status": "invalid",
                "rows": 0,
                "attempts": attempts,
                "error": None,
                "model": LLM_MODEL,
                "llm_base_url": LLM_BASE_URL,
                "duration_sec": round(time.time() - t0, 2),
                "cypher_raw": raw,
            }

        # CLARIFY — модели не хватает данных для точного запроса
        if cypher.strip().upper().startswith("CLARIFY:"):
            clarify_question = cypher.strip()[len("CLARIFY:"):].strip()
            log.info("LLM requested clarification: %s", clarify_question)
            if chat_id:
                db.log_query(chat_id, question, cypher, "clarify", 0, None, attempts,
                              model=LLM_MODEL, llm_base_url=LLM_BASE_URL,
                              answer=clarify_question,
                              duration_sec=round(time.time() - t0, 2), cypher_raw=raw)
            return {
                "answer": clarify_question,
                "cypher": cypher,
                "status": "clarify",
                "rows": 0,
                "attempts": attempts,
                "error": None,
                "model": LLM_MODEL,
                "llm_base_url": LLM_BASE_URL,
                "duration_sec": round(time.time() - t0, 2),
                "cypher_raw": raw,
            }

        # Шаг 2: выполняем в Neo4j
        rows, error = _run_cypher(cypher)

        if error is None:
            row_count = len(rows) if rows else 0
            log.info("Cypher OK, %d rows returned", row_count)

            if row_count == 0:
                status = "empty"
            else:
                status = "ok"

            # Шаг 3: формулируем ответ
            if row_count == 0:
                answer = "Не нашёл информации по этому запросу."
            else:
                # Ограничиваем размер данных для LLM
                rows_limited = rows[:100]
                result_str = json.dumps(rows_limited, ensure_ascii=False, default=str)
                if len(rows) > 100:
                    result_str += f"\n... (показано 100 из {len(rows)} строк)"

                try:
                    answer = _llm_call(
                        ANSWER_SYSTEM_PROMPT,
                        f"Вопрос: {question}\n\nРезультат запроса ({row_count} строк):\n{result_str}",
                        max_tokens=LLM_MAX_TOKENS,
                    )
                except Exception as e:
                    log.error("Answer LLM call failed: %s", e)
                    answer = f"Запрос выполнен ({row_count} строк), но не удалось сформулировать ответ: {e}"

            # Логируем (с ответом)
            if chat_id:
                db.log_query(chat_id, question, cypher, status, row_count, None, attempts,
                              model=LLM_MODEL, llm_base_url=LLM_BASE_URL,
                              answer=answer,
                              duration_sec=round(time.time() - t0, 2), cypher_raw=raw)

            return {
                "answer": answer,
                "cypher": cypher,
                "status": status,
                "rows": row_count,
                "attempts": attempts,
                "error": None,
                "model": LLM_MODEL,
                "llm_base_url": LLM_BASE_URL,
                "duration_sec": round(time.time() - t0, 2),
                "cypher_raw": raw,
            }
        else:
            log.warning("Cypher error (attempt %d): %s", attempt, error[:200])
            # Пробуем снова

    # Все попытки исчерпаны
    if chat_id:
        db.log_query(chat_id, question, cypher or "", "error", 0, error, attempts,
                      model=LLM_MODEL, llm_base_url=LLM_BASE_URL,
                      answer=f"Не удалось выполнить запрос к базе после {attempts} попыток. Последняя ошибка: {error}",
                      duration_sec=round(time.time() - t0, 2), cypher_raw=raw if 'raw' in dir() else None)

    return {
        "answer": f"Не удалось выполнить запрос к базе после {attempts} попыток. Последняя ошибка: {error}",
        "cypher": cypher,
        "status": "error",
        "rows": 0,
        "attempts": attempts,
        "error": error,
        "model": LLM_MODEL,
        "llm_base_url": LLM_BASE_URL,
        "duration_sec": round(time.time() - t0, 2),
        "cypher_raw": raw if 'raw' in dir() else None,
    }