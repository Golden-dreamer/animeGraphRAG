"""Загрузка данных в Neo4j.

Создаёт узлы Anime, Genre, Studio, Producer, Licensor, Person (staff/VA),
Character, StreamingPlatform, Resource, ExternalLink.
Связи: HAS_GENRE, PRODUCED_BY, LICENSED_BY, PRODUCER_OF, STAFF (с ролями),
VOICE_ACTED (с языком), HAS_CHARACTER, RELATED_TO (с типом relation),
AVAILABLE_AT, STREAMING_ON, HAS_RESOURCE.
"""
import os

from neo4j import GraphDatabase

from schema import ANIME_FIELDS

_driver = None


def get_driver():
    global _driver
    if _driver is None:
        uri = os.environ["NEO4J_URI"]
        user = os.environ.get("NEO4J_USER", "neo4j")
        password = os.environ["NEO4J_PASSWORD"]
        _driver = GraphDatabase.driver(uri, auth=(user, password))
    return _driver


def upsert_anime(data: dict):
    """Полная перезапись узла Anime и всех связанных сущностей."""
    with get_driver().session() as session:
        # 1. Базовый узел Anime
        session.execute_write(_upsert_anime_node, data)

        # 2. Жанры, темы, демография
        for genre_name in data.get("genres", []):
            session.execute_write(_link_genre, data["mal_id"], genre_name, "HAS_GENRE")
        for theme_name in data.get("themes", []):
            session.execute_write(_link_genre, data["mal_id"], theme_name, "HAS_THEME")
        for dem_name in data.get("demographic", []):
            session.execute_write(_link_genre, data["mal_id"], dem_name, "HAS_DEMOGRAPHIC")

        # 3. Студии, продюсеры, лицензиары
        for studio_name in data.get("studios", []):
            session.execute_write(_link_studio, data["mal_id"], studio_name)
        for producer_name in data.get("producers", []):
            session.execute_write(_link_producer, data["mal_id"], producer_name, "PRODUCER_OF")
        for licensor_name in data.get("licensors", []):
            session.execute_write(_link_producer, data["mal_id"], licensor_name, "LICENSED_BY")

        # 4. Связанные тайтлы
        for rel in data.get("related", []):
            session.execute_write(_link_related, data["mal_id"], rel)

        # 5. Внешние ссылки (Available At, Resources)
        for link in data.get("available_at", []):
            session.execute_write(_link_external, data["mal_id"], link, "AVAILABLE_AT")
        for link in data.get("resources", []):
            session.execute_write(_link_external, data["mal_id"], link, "HAS_RESOURCE")

        # 6. Стриминговые платформы
        for plat in data.get("streaming_platforms", []):
            session.execute_write(_link_streaming, data["mal_id"], plat)

        # 7. Персонажи (свойство узла аниме — имя + ссылка)
        for char in data.get("characters", []):
            session.execute_write(_link_character, data["mal_id"], char)

        # 8. Стафф (отдельные узлы Person)
        for person in data.get("staff", []):
            session.execute_write(_link_staff, data["mal_id"], person)

        # 9. Voice actors (отдельные узлы Person, связь через Character)
        for char in data.get("characters", []):
            for va in char.get("voice_actors", []):
                session.execute_write(_link_voice_actor, data["mal_id"], char, va)


def _upsert_anime_node(tx, data: dict):
    # title = title_original (display alias in Neo4j Browser)
    # поля — из schema.ANIME_FIELDS, mal_id — key (MERGE, не SET)
    set_clauses = ["a.title = $title_original"] + [
        f"a.{f} = ${f}" for f in ANIME_FIELDS
    ]
    params = {f: data.get(f) for f in ANIME_FIELDS}
    params["mal_id"] = data.get("mal_id")
    tx.run(
        f"MERGE (a:Anime {{mal_id: $mal_id}})\n        SET {', '.join(set_clauses)}",
        **params,
    )


def _link_genre(tx, mal_id: int, genre_name: str, rel_type: str):
    tx.run(f"""
        MERGE (a:Anime {{mal_id: $mal_id}})
        MERGE (g:Genre {{name: $name}})
        MERGE (a)-[:{rel_type}]->(g)
    """, mal_id=mal_id, name=genre_name)


def _link_studio(tx, mal_id: int, studio_name: str):
    tx.run("""
        MERGE (a:Anime {mal_id: $mal_id})
        MERGE (s:Studio {name: $name})
        MERGE (a)-[:PRODUCED_BY]->(s)
    """, mal_id=mal_id, name=studio_name)


def _link_producer(tx, mal_id: int, producer_name: str, rel_type: str):
    tx.run(f"""
        MERGE (a:Anime {{mal_id: $mal_id}})
        MERGE (p:Producer {{name: $name}})
        MERGE (a)-[:{rel_type}]->(p)
    """, mal_id=mal_id, name=producer_name)


def _link_related(tx, mal_id: int, rel: dict):
    """Создаёт связь между аниме и связанным тайтлом (аниме или мангой)."""
    mal_type = rel.get("mal_type", "anime")
    target_id = rel.get("mal_id")
    relation = rel.get("relation", "")
    title = rel.get("title", "")

    if not target_id:
        return

    label = "Anime" if mal_type == "anime" else "Manga"
    # Для Anime-целей НЕ ставим title — узел должен оставаться stub'ом
    # (title IS NULL), чтобы scheduler его обработал. Manga-цели получают
    # title, т.к. не парсятся этим парсером.
    if mal_type == "anime":
        tx.run(f"""
            MERGE (a:Anime {{mal_id: $mal_id}})
            MERGE (t:{label} {{mal_id: $target_id}})
            MERGE (a)-[r:RELATED_TO]->(t)
            SET r.relation = $relation,
                r.target_type = $target_type
        """, mal_id=mal_id, target_id=target_id,
             relation=relation, target_type=mal_type)
    else:
        tx.run(f"""
            MERGE (a:Anime {{mal_id: $mal_id}})
            MERGE (t:{label} {{mal_id: $target_id}})
            SET t.title = coalesce(t.title, $title)
            MERGE (a)-[r:RELATED_TO]->(t)
            SET r.relation = $relation,
                r.target_type = $target_type
        """, mal_id=mal_id, target_id=target_id, title=title,
             relation=relation, target_type=mal_type)


def _link_external(tx, mal_id: int, link: dict, rel_type: str):
    url = link.get("url", "")
    name = link.get("name", "")
    if not url:
        return
    tx.run(f"""
        MERGE (a:Anime {{mal_id: $mal_id}})
        MERGE (e:ExternalLink {{url: $url}})
        SET e.name = coalesce(e.name, $name)
        MERGE (a)-[:{rel_type}]->(e)
    """, mal_id=mal_id, url=url, name=name)


def _link_streaming(tx, mal_id: int, plat: dict):
    name = plat.get("name", "")
    url = plat.get("url", "")
    available = plat.get("available", False)
    if not name:
        return
    tx.run("""
        MERGE (a:Anime {mal_id: $mal_id})
        MERGE (p:StreamingPlatform {name: $name})
        MERGE (a)-[r:STREAMING_ON]->(p)
        SET r.url = $url,
            r.available = $available
    """, mal_id=mal_id, name=name, url=url, available=available)


def _link_character(tx, mal_id: int, char: dict):
    """Персонажи — свойство узла аниме (имя + ссылка), а не отдельный узел.
    По требованию: имя, ссылка, роль.
    """
    char_id = char.get("mal_id")
    name = char.get("name", "")
    url = char.get("url", "")
    role = char.get("role", "")
    if not char_id or not name:
        return

    tx.run("""
        MERGE (a:Anime {mal_id: $mal_id})
        MERGE (c:Character {mal_id: $char_id})
        SET c.name = $name,
            c.url = $url
        MERGE (a)-[r:HAS_CHARACTER]->(c)
        SET r.role = $role
    """, mal_id=mal_id, char_id=char_id, name=name, url=url, role=role)


def _link_staff(tx, mal_id: int, person: dict):
    """Стафф — отдельный узел Person, связь STAFF с ролями."""
    person_id = person.get("mal_id")
    name = person.get("name", "")
    url = person.get("url", "")
    roles = person.get("roles", [])
    if not person_id or not name:
        return

    tx.run("""
        MERGE (a:Anime {mal_id: $mal_id})
        MERGE (p:Person {mal_id: $person_id})
        SET p.name = $name,
            p.url = $url
        MERGE (a)-[r:STAFF]->(p)
        SET r.roles = $roles
    """, mal_id=mal_id, person_id=person_id, name=name, url=url, roles=roles)


def _link_voice_actor(tx, mal_id: int, char: dict, va: dict):
    """Voice actor — отдельный узел Person, связь VOICE_ACTED с языком.
    Связь: Person -[:VOICE_ACTED]-> Character, с property anime_id и language.
    """
    va_id = va.get("mal_id")
    va_name = va.get("name", "")
    va_url = va.get("url", "")
    language = va.get("language", "")
    char_id = char.get("mal_id")

    if not va_id or not va_name or not char_id:
        return

    tx.run("""
        MERGE (c:Character {mal_id: $char_id})
        MERGE (p:Person {mal_id: $va_id})
        SET p.name = coalesce(p.name, $va_name),
            p.url = coalesce(p.url, $va_url)
        MERGE (p)-[r:VOICE_ACTED]->(c)
        SET r.language = $language,
            r.anime_id = $anime_id
    """, char_id=char_id, va_id=va_id, va_name=va_name,
         va_url=va_url, language=language, anime_id=mal_id)


def close():
    global _driver
    if _driver is not None:
        _driver.close()
        _driver = None