"""Загрузка данных пользователей в Neo4j для user-user-parser.

Загружает полный animelist пользователя: обновляет RATED edge'и,
архивирует удалённые профили, обновляет last_seen.
"""
from __future__ import annotations

import logging
import os

from neo4j import GraphDatabase

log = logging.getLogger("user_user_loader")

_driver = None


def get_driver():
    global _driver
    if _driver is None:
        uri = os.environ["NEO4J_URI"]
        user = os.environ.get("NEO4J_USER", "neo4j")
        password = os.environ["NEO4J_PASSWORD"]
        _driver = GraphDatabase.driver(uri, auth=(user, password))
    return _driver


def close():
    global _driver
    if _driver is not None:
        _driver.close()
        _driver = None


def ensure_constraints():
    """Создаёт constraints/индексы. Вызывается при старте."""
    with get_driver().session() as session:
        session.run(
            "CREATE CONSTRAINT username_unique IF NOT EXISTS "
            "FOR (u:User) REQUIRE u.username IS UNIQUE"
        )
        session.run(
            "CREATE CONSTRAINT anime_mal_id_unique IF NOT EXISTS "
            "FOR (a:Anime) REQUIRE a.mal_id IS UNIQUE"
        )
        session.run(
            "CREATE INDEX user_status IF NOT EXISTS FOR (u:User) ON (u.status)"
        )
        session.run(
            "CREATE INDEX user_last_seen IF NOT EXISTS FOR (u:User) ON (u.last_seen)"
        )
    log.info("Constraints and indexes ensured")


def upsert_animelist(username: str, entries: list[dict]):
    """Загружает полный animelist пользователя.

    entries: [{mal_id, score, status, episodes_watched, tags}, ...]
    """
    if not entries:
        return
    with get_driver().session() as session:
        session.execute_write(_upsert_animelist_tx, username, entries)


def _upsert_animelist_tx(tx, username: str, entries: list[dict]):
    tx.run("""
        UNWIND $entries AS e
        MERGE (u:User {username: $username})
        MERGE (a:Anime {mal_id: e.mal_id})
        MERGE (u)-[r:RATED]->(a)
        SET r.score = e.score,
            r.status = e.status,
            r.episodes_watched = e.episodes_watched,
            r.tags = e.tags,
            r.updated_at = datetime()
    """, username=username, entries=entries)

    # Cleanup: удаляем оценки, которых больше нет в animelist
    fresh_ids = [e['mal_id'] for e in entries]
    tx.run("""
        MATCH (u:User {username: $username})-[r:RATED]->(a:Anime)
        WHERE NOT a.mal_id IN $fresh_ids
        DELETE r
    """, username=username, fresh_ids=fresh_ids)


def archive_user(username: str):
    """Помечает пользователя как archived (удалил профиль)."""
    with get_driver().session() as session:
        session.run("""
            MATCH (u:User {username: $username})
            SET u.status = "archived", u.archived_at = datetime()
        """, username=username)
    log.info("User %s archived (profile deleted)", username)


def update_user_last_seen(username: str):
    """Обновляет last_seen при успешном refresh."""
    with get_driver().session() as session:
        session.run("""
            MATCH (u:User {username: $username})
            SET u.last_seen = datetime()
        """, username=username)