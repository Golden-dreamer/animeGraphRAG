import os
from neo4j import GraphDatabase

_driver = None


def get_driver():
    global _driver
    if _driver is None:
        uri = os.environ["NEO4J_URI"]
        user = os.environ.get("NEO4J_USER", "neo4j")
        password = os.environ["NEO4J_PASSWORD"]
        _driver = GraphDatabase.driver(uri, auth=(user, password))
    return _driver


CYPHER_UPSERT = """
MERGE (a:Anime {mal_id: $mal_id})
SET a.poster_url = $poster_url,
    a.title_original = $title_original,
    a.title_english = $title_english,
    a.type = $type,
    a.year = $year,
    a.season = $season,
    a.mal_status = $mal_status
WITH a
UNWIND $genres AS genre_name
MERGE (g:Genre {name: genre_name})
MERGE (a)-[:HAS_GENRE]->(g)
WITH a
UNWIND $studios AS studio_name
MERGE (s:Studio {name: studio_name})
MERGE (a)-[:PRODUCED_BY]->(s)
"""


def upsert_anime(data: dict):
    with get_driver().session() as session:
        session.run(CYPHER_UPSERT, **data)


def close():
    global _driver
    if _driver is not None:
        _driver.close()
        _driver = None
