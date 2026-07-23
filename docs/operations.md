# Эксплуатация

## Обычный запуск

```bash
docker compose up -d --build
```

Координатор управляет парсерами. По умолчанию запускается авто-режим:
чередование user-anime/user-user (слайсы по 30 мин) и airing-parser
(по времени 03:00). Парсеры пассивны — не запускают свои фоновые циклы.
Лимиты MyAnimeList (0.5s между запросами, 55 req/мин) соблюдаются внутри
`fetcher.py` автоматически.

Порты: airing-parser — 8567, user-anime — 8568, user-user — 8569,
coordinator — 8570 (меняются через `PARSERS_PORT`, `USER_ANIME_PORT`,
`USER_USER_PORT`, `COORDINATOR_PORT` в `.env`).

## Первичное наполнение архива (bootstrap)

Проходит все сезоны с 1917 года до текущего (кроме текущего/следующего/
прошлого — те держит scheduler). Прогресс сезонов — в файле
`parsers/anime/bootstrap_progress.txt` (последний обработанный сезон).
Тайтлы внутри сезона — через `title IS NULL`: необработанные
попадают в `select_due_for_season`, обработанные пропускаются.

```bash
docker compose run --rm airing-parser python bootstrap.py
```

В фоне (чтобы закрыть терминал):

```bash
docker compose run -d --name bootstrap airing-parser python bootstrap.py
```

Смотреть прогресс:
```bash
docker logs -f bootstrap
```

Остановить (безопасно, прогресс сохранён):
```bash
docker stop bootstrap && docker rm bootstrap
```

Продолжить с того же места — запустить ту же команду заново. Он не будет:
- повторно обрабатывать тайтлы, у которых `title` уже проставлен;
- пересканировать сезоны до checkpoint'а в `bootstrap_progress.txt`.

Чтобы начать bootstrap с самого начала — удалите `parsers/anime/bootstrap_progress.txt`.

Если в БД остались узлы `:Season` от предыдущих версий (до v9), их можно
удалить:
```cypher
MATCH (s:Season) DETACH DELETE s
```

## Мониторинг

### Airing-parser (порт 8567)

```bash
curl http://localhost:8567/status
```

Пример ответа:

```json
{
  "total_anime": 20392,
  "parsed": 20243,
  "unprocessed_stubs": 149,
  "currently_airing": 257,
  "not_yet_aired": 57
}
```

Неполные узлы (stubs):

```bash
curl http://localhost:8567/stubs
```

Принудительно обновить один тайтл:

```bash
curl -X POST http://localhost:8567/refresh/{mal_id}
```

### Координатор (порт 8570)

Статус всех парсеров + auto_mode:

```bash
curl http://localhost:8570/
```

Запуск авто-режима (чередование user-anime ↔ user-user ↔ airing-parser):

```bash
curl -X POST http://localhost:8570/auto
```

Остановить авто-режим:

```bash
curl -X POST http://localhost:8570/auto/stop
```

Статус авто-режима:

```bash
curl http://localhost:8570/auto/status
```

Изменить длительность слайса (сек):

```bash
curl -X PUT http://localhost:8570/auto/slice \
  -H "Content-Type: application/json" \
  -d '{"slice_sec": 900}'
```

Ручное управление (без авто-режима):

```bash
curl -X POST http://localhost:8570/start/anime        # airing-parser, остановить остальные
curl -X POST http://localhost:8570/start/user-anime   # user-anime, остановить остальные
curl -X POST http://localhost:8570/start/user-user    # user-user, остановить остальные
curl -X POST http://localhost:8570/pause               # остановить все
```

### User-anime (порт 8568)

Парсер пользователей MyAnimeList (anime-centric: stats-страницы).
Запускается как отдельный контейнер `user-anime`.

```bash
docker compose up -d user-anime
```

Мониторинг:

```bash
curl http://localhost:8568/status
```

Ручной запуск цикла:

```bash
curl -X POST http://localhost:8568/trigger-cycle
```

Сканировать конкретное аниме:

```bash
curl -X POST http://localhost:8568/scan-anime/5249
```

### User-user (порт 8569)

Парсер пользователей MyAnimeList (user-centric: animelist refresh).
Запускается как отдельный контейнер `user-user`.

```bash
docker compose up -d user-user
```

Мониторинг:

```bash
curl http://localhost:8569/status
```

Ручной запуск цикла:

```bash
curl -X POST http://localhost:8569/trigger-cycle
```

Обновить конкретного пользователя:

```bash
curl -X POST http://localhost:8569/refresh-user/someuser
```

## GraphRAG

Веб-интерфейс для запросов к графу на естественном языке. Порт 8666.

```bash
docker compose up -d graphrag
```

URL: `http://localhost:8666`

Пайплайн: вопрос → LLM генерирует Cypher → Neo4j → LLM формулирует ответ.
Cypher-запрос показывается в интерфейсе (раскрывающийся блок под ответом).

Логи всех запросов (модель, статус, Cypher, ответ, сырой вывод LLM,
длительность, число попыток) — через веб-интерфейс:

```
http://localhost:8666/logs
```

Или через API:

```bash
curl http://localhost:8666/api/logs?limit=100
```

Проверка живости (модель + URL LLM):

```bash
curl http://localhost:8666/api/health
```

Prometheus-метрики (счётчики запросов, длительность):

```bash
curl http://localhost:8666/metrics
```

## Что происходит при ошибке

1. Ошибка при обработке тайтла (сетевая, парсинг, что угодно) → логируется,
   тайтл остаётся stub (`title IS NULL`).
2. Координатор при следующем цикле снова берёт все Currently Airing +
   Not yet aired + stub'ы → передаёт парсеру.
3. Для архивных тайтлов: `/refresh/{mal_id}` для принудительного обновления,
   или перезапуск bootstrap.
4. Никаких retry-счётчиков, статусов failed, или next_check_at —
   просто «нет title → попробовать снова».

## Просмотр графа

Neo4j Browser: `http://<IP>:7474` (логин `neo4j`, пароль — из `.env`).
Порты в `docker-compose.yml` не ограничены `127.0.0.1` — доступ работает
с любого устройства в локальной сети.

На нодах `:Anime` отображается свойство `title` (алиас `title_original`).
Для существующих нод без `title` — одноразовая команда в Neo4j Browser:

```cypher
MATCH (a:Anime) WHERE a.title IS NULL AND a.title_original IS NOT NULL
SET a.title = a.title_original
```

## Изменение параметров без пересборки образа

`docker-compose.yml` монтирует `./parsers/anime` (airing-parser) и
`./parsers/user_anime` + `./parsers/user_user` (user-парсеры) внутрь
контейнеров как volume, поэтому правки любого `.py` файла требуют только
рестарта контейнера, без `--build`:

```bash
docker compose restart airing-parser
docker compose restart user-anime
docker compose restart user-user
```

## Утилитные скрипты

### Дополнение пропущенных тайтлов

```bash
docker compose run --rm airing-parser python check_missing.py          # актуальные сезоны
docker compose run --rm airing-parser python check_missing.py --all    # все сезоны (1917→)
docker compose run --rm airing-parser python check_missing.py --season 2006 summer
```

Сверяет сезонные страницы MAL с Neo4j, добавляет недостающие как stub'ы.
Координатор обработает их при следующем цикле (или сразу через
`POST /trigger-cycle`).