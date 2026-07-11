# Эксплуатация

## Обычный запуск

```bash
docker compose up -d --build
```

С этого момента scheduler работает сам: раз в `cycle_interval_sec` (по
умолчанию — раз в сутки, 86400 сек) подтягивает текущий/следующий/прошлый
сезон и обрабатывает **все** due-тайтлы (без ограничения batch_size).

Лимиты парсинга MyAnimeList (0.5s между запросами, 55 req/мин)
enforcement'ятся внутри fetcher.py автоматически — scheduler не упрётся в
лимиты и не будет заблокирован сайтом.

Порт FastAPI по умолчанию — `8567` (меняется через `PARSERS_PORT` в `.env`).

Ничего дополнительно нажимать не нужно для актуальных тайтлов.

## Первичное наполнение архива (bootstrap)

Проходит все сезоны с 1917 года до текущего (кроме текущего/следующего/прошлого
— те держит scheduler). Резюмируем: прогресс в SQLite, при прерывании —
просто запустите заново.

```bash
docker compose run --rm parsers python bootstrap.py
```

`--rm` — контейнер удалится сам после завершения.

В фоне (чтобы закрыть терминал):

```bash
docker compose run -d --name bootstrap parsers python bootstrap.py
```

Смотреть прогресс:
```bash
docker logs -f bootstrap
```

Остановить (безопасно, прогресс в SQLite сохранён):
```bash
docker stop bootstrap && docker rm bootstrap
```

Продолжить с того же места позже — запустить ту же команду заново. Он не будет:
- повторно обрабатывать тайтлы, у которых `last_parsed_at` уже проставлен;
- пересканировать сезоны, помеченные в `seasons_bootstrapped`.

## Как понять, что что-то пошло не так

```bash
curl http://localhost:8567/status
```

Пример ответа и что он значит:

```json
{
  "total_anime": 20392,           // всего узлов :Anime в графе
  "parsed": 20243,                // обработаны (title IS NOT NULL)
  "unprocessed_stubs": 149,       // не обработаны (title IS NULL) — scheduler попробует снова
  "currently_airing": 257,        // mal_status = 'Currently Airing'
  "not_yet_aired": 57,            // mal_status = 'Not yet aired'
  "seasons_bootstrapped": 437     // закрытых сезонов (узлы :Season)
}
```

### Неполные узлы (stubs)

Если `unprocessed_stubs > 0` — есть тайтлы, которые не были обработаны
(ошибка сети, парсинга, сайт был недоступен). Scheduler попробует снова
в следующем цикле. Посмотреть конкретные тайтлы:

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

## Что происходит при ошибке автоматически (без вашего участия)

1. Ошибка при обработке тайтла (сетевая, парсинг, что угодно) → логируется,
   тайтл остаётся stub (title IS NULL).
2. Scheduler при следующем цикле снова берёт все Currently Airing +
   Not yet aired + stub'ы → пытается снова.
3. Для архивных тайтлов: /refresh/{mal_id} для принудительного обновления,
   или перезапуск bootstrap.
4. Никаких retry-счётчиков, статусов failed, или next_check_at —
   просто "нет title → попробовать снова".

## Принудительное обновление конкретного тайтла

```bash
curl -X POST http://localhost:8567/refresh/{mal_id}
```

Обновляет тайтл прямо сейчас (прямой вызов process_one), без очереди.
Полезно: изменился рейтинг у архивного тайтла; тайтл остался stub;
данные явно устарели.

## Просмотр графа

Neo4j Browser: `http://<IP-машины-в-локальной-сети>:7474`
(логин `neo4j`, пароль — из `.env`).

Порты в `docker-compose.yml` не ограничены `127.0.0.1`, поэтому доступ
работает с любого устройства в той же локальной сети, не только с хоста.

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

Выполняет discover (текущий/следующий/прошлый сезон) и обрабатывает все
due-тайтлы. Влияет на все три актуальных сезона, включая прошлый
(который обычно обновляется раз в неделю). Если цикл уже выполняется —
возвращает 409 Conflict.

### Изменить интервал автоматического цикла

```bash
curl -X PUT http://localhost:8567/schedule \
  -H "Content-Type: application/json" \
  -d '{"cycle_interval_sec": 3600}'
```

Минимум 60 секунд. Изменение применяется немедленно и действует до
перезапуска контейнера. Для постоянного изменения — отредактируйте
`config.yaml` (параметр `cycle_interval_sec`).

## Дополнение staff (после исправления fetcher)

Если база наполнялась до v5, staff у большинства аниме неполный.
Дополнить одним скриптом:

```bash
docker compose run --rm parsers python update_staff.py
```

Проходит все аниме с <=4 staff в Neo4j, фетчит /characters с правильным
URL и обновляет связи. Резюмируемый, можно ограничить: `--limit 100`.

## Дополнение пропущенных тайтлов

Сверить сезонные страницы с БД и добавить недостающие:

```bash
docker compose run --rm parsers python check_missing.py          # актуальные
docker compose run --rm parsers python check_missing.py --all    # все сезоны
docker compose run --rm parsers python check_missing.py --season 2006 summer
```

Недостающие тайтлы регистрируются в очереди, scheduler обработает их
при следующем цикле (или сразу через `POST /trigger-cycle`).