"""Загрузка данных пользователей в Neo4j для user-anime-parser.

Создаёт узлы :User и связи :RATED из stats-страниц аниме.
Также сохраняет Summary Stats и Score Stats на узлах :Anime.
"""
from __future__ import annotations

import json
import logging
import os

from neo4j import GraphDatabase

log = logging.getLogger("user_anime_loader")

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
        session.run(
            "CREATE INDEX anime_summary_stats_at IF NOT EXISTS "
            "FOR (a:Anime) ON (a.summary_stats_at)"
        )
    log.info("Constraints and indexes ensured")


def upsert_stats_batch(mal_id: int, ratings: list[dict]):
    """Batch upsert пользователей и их оценок с одной stats-страницы."""
    if not ratings:
        return
    with get_driver().session() as session:
        session.execute_write(_upsert_stats_batch_tx, mal_id, ratings)


def _upsert_stats_batch_tx(tx, mal_id: int, ratings: list[dict]):
    tx.run("""
        UNWIND $ratings AS r
        MERGE (u:User {username: r.username})
        ON CREATE SET u.profile_url = r.profile_url,
                      u.discovered_via_anime = $mal_id,
                      u.status = "active",
                      u.created_at = datetime()
        MERGE (a:Anime {mal_id: $mal_id})
        MERGE (u)-[rel:RATED]->(a)
        SET rel.score = r.score,
            rel.status = r.status,
            rel.episodes_watched = r.episodes_watched,
            rel.updated_at = datetime(),
            u.last_seen = datetime()
    """, mal_id=mal_id, ratings=ratings)


def upsert_summary_stats(mal_id: int, summary: dict):
    """Сохраняет Summary Stats на узел :Anime."""
    with get_driver().session() as session:
        session.run("""
            MATCH (a:Anime {mal_id: $mal_id})
            SET a.stats_watching = $watching,
                a.stats_completed = $completed,
                a.stats_on_hold = $on_hold,
                a.stats_dropped = $dropped,
                a.stats_plan_to_watch = $plan_to_watch,
                a.stats_total = $total,
                a.summary_stats_at = datetime()
        """, mal_id=mal_id,
           watching=summary.get('watching'),
           completed=summary.get('completed'),
           on_hold=summary.get('on_hold'),
           dropped=summary.get('dropped'),
           plan_to_watch=summary.get('plan_to_watch'),
           total=summary.get('total'))


def upsert_score_stats(mal_id: int, scores: dict):
    """Сохраняет Score Stats (разбивка оценок) на узел :Anime."""
    if not scores:
        return
    with get_driver().session() as session:
        session.run("""
            MATCH (a:Anime {mal_id: $mal_id})
            SET a.score_stats = $scores_json,
                a.score_stats_at = datetime()
        """, mal_id=mal_id, scores_json=json.dumps(scores))


def cleanup_stale_ratings(mal_id: int, fresh_usernames: set[str]):
    """Удаляет RATED edge'и для аниме, которых нет в свежем скане."""
    if not fresh_usernames:
        return
    with get_driver().session() as session:
        result = session.run("""
            MATCH (u:User)-[r:RATED]->(a:Anime {mal_id: $mal_id})
            WHERE NOT u.username IN $fresh_usernames
            DELETE r
            RETURN count(r) AS deleted
        """, mal_id=mal_id, fresh_usernames=list(fresh_usernames))
        deleted = result.single()["deleted"]
        if deleted:
            log.info("mal_id=%s: удалено %d устаревших оценок", mal_id, deleted)