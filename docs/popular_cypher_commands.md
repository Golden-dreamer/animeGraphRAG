# Полезные Cypher-запросы для Anime GraphRAG

Схема БД: узлы `Anime`, `Genre`, `Studio`, `Producer`, `Person` (staff + VA),
`Character`, `StreamingPlatform`, `ExternalLink`, `Manga`.

Связи:
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

## 1. Режиссёр аниме X — что ещё он снимал

```cypher
// Найти режиссёра по названию аниме и показать все его работы
MATCH (a:Anime)-[:STAFF]->(p:Person)
WHERE a.title CONTAINS 'Fullmetal Alchemist'
  AND ANY(r IN p.roles WHERE toLower(r) CONTAINS 'director')
MATCH (other:Anime)-[:STAFF]->(p)
RETURN p.name AS director, other.title AS title, other.year AS year, other.score AS score
ORDER BY other.score DESC;
```

## 2. Актёр озвучки — кого ещё озвучивал

```cypher
// Найти сэйю по имени и показать всех озвученных персонажей
MATCH (p:Person)-[:VOICE_ACTED]->(c:Character)<-[:HAS_CHARACTER]-(a:Anime)
WHERE p.name CONTAINS 'Miyu'
RETURN p.name AS voice_actor,
       c.name AS character,
       collect(a.title) AS anime_titles,
       count(a) AS anime_count
ORDER BY anime_count DESC;
```

## 3. Все аниме конкретной студии

```cypher
MATCH (a:Anime)-[:PRODUCED_BY]->(s:Studio)
WHERE s.name = 'Kyoto Animation'
RETURN a.title, a.year, a.score, a.episodes, a.type
ORDER BY a.score DESC;
```

## 4. Топ-N аниме по жанру

```cypher
MATCH (a:Anime)-[:HAS_GENRE]->(g:Genre {name: 'Action'})
WHERE a.score IS NOT NULL
RETURN a.title, a.score, a.year, a.members
ORDER BY a.score DESC
LIMIT 25;
```

## 5. Пересечение жанров — аниме с несколькими жанрами

```cypher
// Аниме, у которых есть и Action, и Comedy
MATCH (a:Anime)-[:HAS_GENRE]->(:Genre {name: 'Action'}),
      (a)-[:HAS_GENRE]->(:Genre {name: 'Comedy'})
RETURN a.title, a.score, a.year
ORDER BY a.score DESC
LIMIT 20;
```

## 6. Самые популярные жанры (по количеству аниме)

```cypher
MATCH (a:Anime)-[:HAS_GENRE]->(g:Genre)
RETURN g.name AS genre, count(a) AS anime_count, avg(a.score) AS avg_score
ORDER BY anime_count DESC;
```

## 7. Аниме сезона

```cypher
// Все аниме конкретного сезона
MATCH (a:Anime)
WHERE a.year = 2024 AND a.season = 'spring'
RETURN a.title, a.score, a.type, a.episodes, a.studios
ORDER BY a.score DESC;
```

## 8. Связанные тайтлы (продолжения, приквелы, спин-оффы)

```cypher
MATCH (a:Anime {title: 'Attack on Titan'})-[r:RELATED_TO]->(related)
RETURN related.title AS title, r.relation AS relation, r.target_type AS type
ORDER BY related.year;
```

## 9. Аниме одного режиссёра и студии (полная фильмография через студию)

```cypher
MATCH (a:Anime)-[:STAFF]->(p:Person)
WHERE p.name CONTAINS 'Hayao'
  AND ANY(r IN p.roles WHERE toLower(r) CONTAINS 'director')
MATCH (a)-[:PRODUCED_BY]->(s:Studio)
RETURN p.name AS director, s.name AS studio, a.title, a.year, a.score
ORDER BY a.year;
```

## 10. Кто озвучивал конкретного персонажа

```cypher
MATCH (p:Person)-[va:VOICE_ACTED]->(c:Character)<-[:HAS_CHARACTER]-(a:Anime)
WHERE c.name CONTAINS 'Goku'
RETURN c.name AS character, p.name AS voice_actor, va.language AS language,
       collect(a.title) AS anime
ORDER BY voice_actor;
```

## 11. Сэйю с наибольшим числом ролей

```cypher
MATCH (p:Person)-[:VOICE_ACTED]->(c:Character)
RETURN p.name AS voice_actor, count(c) AS characters_voiced
ORDER BY characters_voiced DESC
LIMIT 20;
```

## 12. Студии с самым высоким средним score

```cypher
MATCH (a:Anime)-[:PRODUCED_BY]->(s:Studio)
WHERE a.score IS NOT NULL
RETURN s.name AS studio, count(a) AS titles, avg(a.score) AS avg_score,
       max(a.score) AS best_score
ORDER BY avg_score DESC
LIMIT 20;
```

## 13. Где смотреть аниме (стриминг)

```cypher
MATCH (a:Anime)-[r:STREAMING_ON]->(p:StreamingPlatform)
WHERE a.title CONTAINS 'Demon Slayer'
RETURN p.name AS platform, r.url AS url, r.available AS available;
```

## 14. Аниме по той же оригинальной работе (source)

```cypher
MATCH (a:Anime {source: 'Manga'})
WHERE a.score IS NOT NULL
RETURN a.title, a.score, a.year
ORDER BY a.score DESC
LIMIT 25;
```

## 15. Рекомендации: похожие аниме (общие жанры + студия)

```cypher
// Найти аниме, похожие на заданное: 2+ общих жанра и та же студия
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

## 16. Все персонажи аниме

```cypher
MATCH (a:Anime)-[r:HAS_CHARACTER]->(c:Character)
WHERE a.title CONTAINS 'Evangelion'
RETURN c.name AS character, r.role AS role
ORDER BY r.role;
```

## 17. Полный состав стаффа аниме

```cypher
MATCH (a:Anime)-[r:STAFF]->(p:Person)
WHERE a.title CONTAINS 'Cowboy Bebop'
RETURN p.name AS name, r.roles AS roles
ORDER BY p.name;
```

## 18. Аниме с самой большой командой озвучки

```cypher
MATCH (a:Anime)<-[:HAS_CHARACTER]-(c:Character)<-[:VOICE_ACTED]-(p:Person)
RETURN a.title, count(DISTINCT p) AS voice_actors
ORDER BY voice_actors DESC
LIMIT 15;
```

## 19. Какие жанры у конкретного аниме

```cypher
MATCH (a:Anime)-[:HAS_GENRE]->(g:Genre)
WHERE a.title CONTAINS 'One Piece'
RETURN g.name AS genre;
```

## 20. Статистика БД — общее количество узлов и связей

```cypher
// Количество узлов по типам
MATCH (n)
RETURN labels(n) AS type, count(n) AS count
ORDER BY count DESC;
```

```cypher
// Количество связей по типам
MATCH ()-[r]->()
RETURN type(r) AS type, count(r) AS count
ORDER BY count DESC;
```

---

## Шпаргалка по синтаксису

- `MATCH` — поиск по паттерну (аналог JOIN/FROM)
- `WHERE` — фильтрация
- `RETURN` — что вернуть (аналог SELECT)
- `ORDER BY ... DESC/ASC` — сортировка
- `LIMIT N` — ограничение количества
- `count()`, `avg()`, `max()` — агрегатные функции
- `collect(x)` — собрать значения в список
- `CONTAINS` — подстрока (case-sensitive)
- `toLower(x) CONTAINS '...'` — case-insensitive поиск
- `ANY(r IN list WHERE ...)` — квантор существования по списку
- `coalesce(a, b)` — вернуть a если не null, иначе b
- `DISTINCT` — убрать дубликаты