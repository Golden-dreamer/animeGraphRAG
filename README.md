# Anime GraphRAG — парсер MyAnimeList

Парсер MyAnimeList (HTML-скрапинг) с загрузкой в графовую БД Neo4j.
Scheduler актуализирует текущий, следующий и прошлый сезоны.
Bootstrap проходится по архиву с 1917 года.

## Запуск

1. Откройте `.env` в корне, смените `NEO4J_PASSWORD` на свой.
2. Поднимите проект:

   ```bash
   docker compose up -d --build
   ```

3. Проверьте:
   - Neo4j Browser: `http://<IP>:7474` (логин `neo4j`, пароль из `.env`)
   - FastAPI: `http://<IP>:8567/docs`

Scheduler запускается автоматически при старте контейнера. По умолчанию —
раз в сутки (настраивается через `cycle_interval_sec`). Лимиты MyAnimeList
(0.5s между запросами, 55 req/мин) соблюдаются внутри `fetcher.py`.

## Первичное наполнение архива (опционально, разово)

Загрузить все аниме с 1917 года (кроме трёх актуальных сезонов — их держит
scheduler):

```bash
docker compose run --rm parsers python bootstrap.py
```

Прогресс сохраняется в Neo4j (`title IS NULL` у необработанных
тайтлов) и в файле `parsers/bootstrap_progress.txt` (последний обработанный
сезон). При прерывании — просто запустите заново, продолжит с места
остановки. Лимиты — ~27 тайтлов/мин (два запроса
на тайтл: основная страница + characters/staff).

Подробнее — [`docs/operations.md`](docs/operations.md).

## Дополнение staff

Если база наполнялась до исправления URL `/characters` (v5), staff у
большинства аниме неполный (2-4 человека вместо полного списка):

```bash
docker compose run --rm parsers python update_staff.py
docker compose run --rm parsers python update_staff.py --limit 100
```

Скрипт проходит все аниме с <=4 staff в Neo4j, заново фетчит `/characters`
с правильным URL и обновляет связи. MERGE предотвращает дубли.

## Дополнение пропущенных тайтлов

Сверить сезонные страницы с БД и добавить недостающие:

```bash
docker compose run --rm parsers python check_missing.py          # актуальные сезоны
docker compose run --rm parsers python check_missing.py --all    # все сезоны (1917→)
docker compose run --rm parsers python check_missing.py --season 2006 summer
```

Недостающие тайтлы регистрируются как stub'ы (`title IS NULL`), scheduler
обработает их при следующем цикле.

## Управление через API

```bash
# Статус
curl http://localhost:8567/status

# Неполные узлы (title IS NULL)
curl http://localhost:8567/stubs

# Принудительно обновить один тайтл
curl -X POST http://localhost:8567/refresh/{mal_id}

# Запустить цикл scheduler прямо сейчас
curl -X POST http://localhost:8567/trigger-cycle

# Изменить интервал автоматического цикла (секунды, минимум 60)
curl -X PUT http://localhost:8567/schedule \
  -H "Content-Type: application/json" \
  -d '{"cycle_interval_sec": 3600}'

# Текущая конфигурация
curl http://localhost:8567/config
```

Полный список эндпоинтов — [`docs/configuration.md`](docs/configuration.md).

## Архитектура

```
parsers/
  app.py             — FastAPI + фоновый цикл scheduler'а
  scheduler_logic.py — один цикл: discover + обработка due-очереди
  discover.py        — регистрация тайтлов 3 актуальных сезонов в графе
  graph_state.py     — очередь/state в Neo4j (stub'ы, due-выборка, сезоны)
  processing.py      — обработка одного тайтла (общая для scheduler и bootstrap)
  fetcher.py         — HTTP к MyAnimeList + рейт-лимит + ретраи
  mal_scraper.py     — парсер HTML: сезон, страница аниме, characters/staff
  parser.py          — нормализация данных из scraper для loader
  loader.py          — запись в Neo4j (Cypher MERGE)
  mal_seasons.py     — утилиты сезонов: current, shift, all_seasons
  config.py          — загрузка config.yaml + env-переменных
  bootstrap.py       — ручной прогон архива (1917→)
  update_staff.py    — ручной: дополнение staff для аниме с <=4 записями
  check_missing.py   — ручной: сверка MAL ↔ Neo4j, добавление недостающих
  config.yaml        — параметры (правится без пересборки образа)
```

Подробнее — [`docs/architecture.md`](docs/architecture.md).
Модель данных — [`docs/data-model.md`](docs/data-model.md).
Эксплуатация — [`docs/operations.md`](docs/operations.md).
Конфигурация — [`docs/configuration.md`](docs/configuration.md).
Changelog — [`docs/changelog.md`](docs/changelog.md).
Cypher-запросы — [`docs/popular_cypher_commands.md`](docs/popular_cypher_commands.md).

## GraphRAG UI

Веб-интерфейс для запросов к графу на естественном языке (порт 8666):

```bash
docker compose up -d graphrag
```

URL: `http://localhost:8666`

Пайплайн: вопрос → LLM генерирует Cypher-запрос → Neo4j → LLM формулирует
ответ на русском. Cypher показывается в интерфейсе (раскрывающийся блок).
Модель LLM настраивается через `.env` (`GRAPHRAG_LLM_MODEL`, по умолчанию
`glm-5.2`). Подробнее — [`docs/architecture.md`](docs/architecture.md)
и [`docs/operations.md`](docs/operations.md).