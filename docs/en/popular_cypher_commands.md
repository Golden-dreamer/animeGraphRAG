# Useful Cypher Queries for Anime GraphRAG

DB schema: nodes `Anime`, `Genre`, `Studio`, `Producer`, `Person` (staff + VA),
`Character`, `StreamingPlatform`, `ExternalLink`, `Manga`.

Relationships:
```
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
```

---

## 1. Director of anime X — what else they directed

```cypher
// Find the director by anime title and show all their works
MATCH (a:Anime)-[:STAFF]->(p:Person)
WHERE a.title CONTAINS 'Fullmetal Alchemist'
  AND ANY(r IN p.roles WHERE toLower(r) CONTAINS 'director')
MATCH (other:Anime)-[:STAFF]->(p)
RETURN p.name AS director, other.title AS title, other.year AS year, other.score AS score
ORDER BY other.score DESC;
```

## 2. Voice actor — who else they voiced

```cypher
// Find a seiyuu by name and show all characters they voiced
MATCH (p:Person)-[:VOICE_ACTED]->(c:Character)<-[:HAS_CHARACTER]-(a:Anime)
WHERE p.name CONTAINS 'Miyu'
RETURN p.name AS voice_actor,
       c.name AS character,
       collect(a.title) AS anime_titles,
       count(a) AS anime_count
ORDER BY anime_count DESC;
```

## 3. All anime from a specific studio

```cypher
MATCH (a:Anime)-[:PRODUCED_BY]->(s:Studio)
WHERE s.name = 'Kyoto Animation'
RETURN a.title, a.year, a.score, a.episodes, a.type
ORDER BY a.score DESC;
```

## 4. Top-N anime by genre

```cypher
MATCH (a:Anime)-[:HAS_GENRE]->(g:Genre {name: 'Action'})
WHERE a.score IS NOT NULL
RETURN a.title, a.score, a.year, a.members
ORDER BY a.score DESC
LIMIT 25;
```

## 5. Genre intersection — anime with multiple genres

```cypher
// Anime that have both Action and Comedy
MATCH (a:Anime)-[:HAS_GENRE]->(:Genre {name: 'Action'}),
      (a)-[:HAS_GENRE]->(:Genre {name: 'Comedy'})
RETURN a.title, a.score, a.year
ORDER BY a.score DESC
LIMIT 20;
```

## 6. Most popular genres (by anime count)

```cypher
MATCH (a:Anime)-[:HAS_GENRE]->(g:Genre)
RETURN g.name AS genre, count(a) AS anime_count, avg(a.score) AS avg_score
ORDER BY anime_count DESC;
```

## 7. Anime of a season

```cypher
// All anime of a specific season
MATCH (a:Anime)
WHERE a.year = 2024 AND a.season = 'spring'
RETURN a.title, a.score, a.type, a.episodes
ORDER BY a.score DESC;
```

## 8. Related titles (sequels, prequels, spin-offs)

```cypher
MATCH (a:Anime {title: 'Attack on Titan'})-[r:RELATED_TO]->(related)
RETURN related.title AS title, r.relation AS relation, r.target_type AS type
ORDER BY related.year;
```

## 9. Anime by the same director and studio (full filmography via studio)

```cypher
MATCH (a:Anime)-[:STAFF]->(p:Person)
WHERE p.name CONTAINS 'Hayao'
  AND ANY(r IN p.roles WHERE toLower(r) CONTAINS 'director')
MATCH (a)-[:PRODUCED_BY]->(s:Studio)
RETURN p.name AS director, s.name AS studio, a.title, a.year, a.score
ORDER BY a.year;
```

## 10. Who voiced a specific character

```cypher
MATCH (p:Person)-[va:VOICE_ACTED]->(c:Character)<-[:HAS_CHARACTER]-(a:Anime)
WHERE c.name CONTAINS 'Goku'
RETURN c.name AS character, p.name AS voice_actor, va.language AS language,
       collect(a.title) AS anime
ORDER BY voice_actor;
```

## 11. Seiyuu with the most roles

```cypher
MATCH (p:Person)-[:VOICE_ACTED]->(c:Character)
RETURN p.name AS voice_actor, count(c) AS characters_voiced
ORDER BY characters_voiced DESC
LIMIT 20;
```

## 12. Studios with the highest average score

```cypher
MATCH (a:Anime)-[:PRODUCED_BY]->(s:Studio)
WHERE a.score IS NOT NULL
RETURN s.name AS studio, count(a) AS titles, avg(a.score) AS avg_score,
       max(a.score) AS best_score
ORDER BY avg_score DESC
LIMIT 20;
```

## 13. Where to watch anime (streaming)

```cypher
MATCH (a:Anime)-[r:STREAMING_ON]->(p:StreamingPlatform)
WHERE a.title CONTAINS 'Demon Slayer'
RETURN p.name AS platform, r.url AS url, r.available AS available;
```

## 14. Anime by the same source material

```cypher
MATCH (a:Anime {source: 'Manga'})
WHERE a.score IS NOT NULL
RETURN a.title, a.score, a.year
ORDER BY a.score DESC
LIMIT 25;
```

## 15. Recommendations: similar anime (shared genres + studio)

```cypher
// Find anime similar to a given one: 2+ shared genres and the same studio
MATCH (base:Anime)-[:PRODUCED_BY]->(s:Studio)<-[:PRODUCED_BY]-(rec:Anime),
      (base)-[:HAS_GENRE]->(g:Genre)<-[:HAS_GENRE]-(rec)
WHERE base.title CONTAINS 'Steins;Gate'
  AND rec <> base
WITH base, rec, s, count(DISTINCT g) AS shared_genres
WHERE shared_genres >= 2
RETURN rec.title AS recommendation, rec.score, shared_genres, s.name AS shared_studio
ORDER BY rec.score DESC
LIMIT 15;
```

## 16. All characters of an anime

```cypher
MATCH (a:Anime)-[r:HAS_CHARACTER]->(c:Character)
WHERE a.title CONTAINS 'Evangelion'
RETURN c.name AS character, r.role AS role
ORDER BY r.role;
```

## 17. Full staff of an anime

```cypher
MATCH (a:Anime)-[r:STAFF]->(p:Person)
WHERE a.title CONTAINS 'Cowboy Bebop'
RETURN p.name AS name, r.roles AS roles
ORDER BY p.name;
```

## 18. Anime with the largest voice cast

```cypher
MATCH (a:Anime)<-[:HAS_CHARACTER]-(c:Character)<-[:VOICE_ACTED]-(p:Person)
RETURN a.title, count(DISTINCT p) AS voice_actors
ORDER BY voice_actors DESC
LIMIT 15;
```

## 19. What genres a specific anime has

```cypher
MATCH (a:Anime)-[:HAS_GENRE]->(g:Genre)
WHERE a.title CONTAINS 'One Piece'
RETURN g.name AS genre;
```

## 20. DB statistics — total nodes and relationships

```cypher
// Node count by type
MATCH (n)
RETURN labels(n) AS type, count(n) AS count
ORDER BY count DESC;
```

```cypher
// Relationship count by type
MATCH ()-[r]->()
RETURN type(r) AS type, count(r) AS count
ORDER BY count DESC;
```

---

## Syntax Cheat Sheet

- `MATCH` — pattern matching (analogous to JOIN/FROM)
- `WHERE` — filtering
- `RETURN` — what to return (analogous to SELECT)
- `ORDER BY ... DESC/ASC` — sorting
- `LIMIT N` — limit the number of results
- `count()`, `avg()`, `max()` — aggregate functions
- `collect(x)` — collect values into a list
- `CONTAINS` — substring match (case-sensitive)
- `toLower(x) CONTAINS '...'` — case-insensitive search
- `ANY(r IN list WHERE ...)` — existential quantifier over a list
- `coalesce(a, b)` — return a if not null, otherwise b
- `DISTINCT` — remove duplicates