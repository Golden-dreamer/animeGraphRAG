# Эксплуатация

## Обычный запуск

```bash
docker compose up -d --build
```

Scheduler работает сам: раз в `cycle_interval_sec` (по умолчанию 86400 —
раз в сутки) регистрирует новые тайтлы 3 актуальных сезонов и обрабатывает
все due-тайтлы. Лимиты MyAnimeList (0.5s между запросами, 55 req/мин)
соблюдаются внутри `fetcher.py` автоматически.

Порт FastAPI по умолчанию — `8567` (меняется через `PARSERS_PORT` в `.env`).

## Первичное наполнение архива (bootstrap)

Проходит все сезоны с 1917 года до текущего (кроме текущего/следующего/
прошлого — те держит scheduler). Прогресс сезонов — в файле `bootstrap_progress.txt` (последний обработанный
сезон). Тайтлы внутри сезона — через `title IS NULL`: необработанные
попадают в `select_due_for_season`, обработанные пропускаются.

```bash
docker compose run --rm parsers python bootstrap.py
```

В фоне (чтобы закрыть терминал):

```bash
docker compose run -d --name bootstrap parsers python bootstrap.py
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

Чтобы начать bootstrap с самого начала — удалите `parsers/bootstrap_progress.txt`.

Если в БД остались узлы `:Season` от предыдущих версий (до v9), их можно
удалить:
```cypher
MATCH (s:Season) DETACH DELETE s
```

## Мониторинг

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

- `total_anime` — всего узлов `:Anime` в графе.
- `parsed` — обработаны (`title IS NOT NULL`).
- `unprocessed_stubs` — не обработаны (`title IS NULL`). Scheduler
  попробует снова в следующем цикле.
- `currently_airing` / `not_yet_aired` — `mal_status` из MAL.

### Неполные узлы (stubs)

```bash
curl http://localhost:8567/stubs
```

Принудительно обновить один тайтл:

```bash
curl -X POST http://localhost:8567/refresh/{mal_id}
```

Или запустить полный цикл scheduler:

```bash
curl -X POST http://localhost:8567/trigger-cycle
```

## GraphRAG

Веб-интерфейс для запросов к графу на естественном языке. Порт 8666.

```bash
docker compose up -d graphrag
```

URL: `http://localhost:8666`

Пайплайн: вопрос → LLM генерирует Cypher → Neo4j → LLM формулирует ответ.
Cypher-запрос показывается в интерфейсе (раскрывающийся блок под ответом).

Логи всех запросов (включая ошибки и число попыток):

```bash
curl http://localhost:8666/api/logs?limit=50
```

Проверка живости:

```bash
curl http://localhost:8666/api/health
```

## Что происходит при ошибке

1. Ошибка при обработке тайтла (сетевая, парсинг, что угодно) → логируется,
   тайтл остаётся stub (`title IS NULL`).
2. Scheduler при следующем цикле снова берёт все Currently Airing +
   Not yet aired + stub'ы → пытается снова.
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

`docker-compose.yml` монтирует `./parsers` внутрь контейнера как volume,
поэтому правки `config.yaml` (или любого `.py` файла) требуют только
рестарта контейнера, без `--build`:

```bash
docker compose restart parsers
```

## Управление циклами scheduler через API

### Запустить цикл прямо сейчас

```bash
curl -X POST http://localhost:8567/trigger-cycle
```

Если цикл уже выполняется — возвращает 409 Conflict.

### Изменить интервал автоматического цикла

```bash
curl -X PUT http://localhost:8567/schedule \
  -H "Content-Type: application/json" \
  -d '{"cycle_interval_sec": 3600}'
```

Минимум 60 секунд. Действует до перезапуска контейнера. Для постоянного
изменения — отредактируйте `config.yaml`.

## Утилитные скрипты

### Дополнение staff

Если база наполнялась до v5, staff у большинства аниме неполный:

```bash
docker compose run --rm parsers python update_staff.py
docker compose run --rm parsers python update_staff.py --limit 100
docker compose run --rm parsers python update_staff.py --threshold 4
```

Проходит все аниме с staff <= threshold (по умолчанию 4), фетчит
`/characters` с правильным URL, обновляет связи через `upsert_staff_only`.

### Дополнение пропущенных тайтлов

```bash
docker compose run --rm parsers python check_missing.py          # актуальные
docker compose run --rm parsers python check_missing.py --all    # все сезоны
docker compose run --rm parsers python check_missing.py --season 2006 summer
```

Сверяет сезонные страницы MAL с Neo4j, добавляет недостающие как stub'ы.
Scheduler обработает их при следующем цикле (или сразу через
`POST /trigger-cycle`).