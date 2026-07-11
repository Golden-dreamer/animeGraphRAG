"""
Первичное наполнение архива: проходит все сезоны от 1917 года до текущего.
Полностью резюмируемое:
  - каждый обработанный тайтл получает title в Neo4j (title IS NULL = не обработан)
  - прогресс сезонов — в файле bootstrap_progress.txt (одна строка: "2024 spring")
  - если прогон прервался (упал контейнер, обрыв сети) — просто перезапустите:
      docker compose run --rm parsers python bootstrap.py
    он продолжит с последнего обработанного сезона, ничего не скачивая повторно.

Специально НЕ обрабатывает текущий/следующий/прошлый сезон — этим постоянно
занимается scheduler (см. app.py), здесь нет смысла дублировать.

Ошибки при обработке отдельных тайтлов НЕ роняют процесс — тайтл остаётся
без title и будет обработан при следующем запуске или через /refresh.
"""
import logging
import os
import sys

import fetcher
import graph_state
from config import load_config
from mal_seasons import all_seasons, current_season, shift_season, SEASON_ORDER
from processing import process_one

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("bootstrap")

# Файл лежит в /app (volume ./parsers → /app), переживает перезапуск контейнера
_PROGRESS_FILE = os.path.join(os.path.dirname(__file__), "bootstrap_progress.txt")


def _load_checkpoint() -> tuple[int, str] | None:
    """Читает последний обработанный сезон из файла.
    Возвращает (year, season) или None если файл не найден."""
    if not os.path.exists(_PROGRESS_FILE):
        return None
    try:
        with open(_PROGRESS_FILE, "r") as f:
            line = f.read().strip()
        parts = line.split()
        if len(parts) == 2:
            return int(parts[0]), parts[1].lower()
    except (ValueError, OSError):
        pass
    log.warning("Не удалось прочитать %s — начинаем с начала", _PROGRESS_FILE)
    return None


def _save_checkpoint(year: int, season: str):
    """Записывает обработанный сезон в файл."""
    with open(_PROGRESS_FILE, "w") as f:
        f.write(f"{year} {season}\n")


def _should_skip(year: int, season: str, checkpoint: tuple[int, str] | None) -> bool:
    """True если сезон уже был обработан (находится до или равен checkpoint'у)."""
    if checkpoint is None:
        return False
    cy, cs = checkpoint
    cidx = SEASON_ORDER.index(cs)
    sidx = SEASON_ORDER.index(season)
    return (year, sidx) <= (cy, cidx)


def main():
    cfg = load_config()

    cy, cs = current_season()
    ny, ns = shift_season(cy, cs, 1)
    py, ps = shift_season(cy, cs, -1)
    skip_seasons = {(cy, cs), (ny, ns), (py, ps)}  # эти держит scheduler

    checkpoint = _load_checkpoint()
    if checkpoint:
        log.info("Checkpoint: последний обработанный сезон — %s %d", checkpoint[1], checkpoint[0])

    for year, season in all_seasons(1917):
        if (year, season) in skip_seasons:
            continue
        if _should_skip(year, season, checkpoint):
            continue

        log.info("=== Сезон %s %d ===", season, year)
        try:
            entries = fetcher.get_season_list(year, season, delay_sec=cfg.request_delay_sec)
        except Exception as e:
            log.error("Не удалось получить список сезона %s %d: %s — пропускаем", season, year, e)
            continue

        for e in entries:
            graph_state.upsert_anime_stub(e["mal_id"], year, season)

        processed = 0
        failed = 0
        while True:
            batch = graph_state.select_due_for_season(year, season, limit=cfg.batch_size)
            if not batch:
                break
            for mal_id in batch:
                try:
                    process_one(mal_id, cfg)
                    processed += 1
                except Exception as e:
                    log.error("mal_id=%s: непредвиденная ошибка в process_one: %s", mal_id, e)
                    failed += 1

        _save_checkpoint(year, season)
        log.info("Сезон %s %d завершён (%d тайтлов, обработано=%d, ошибок=%d)",
                 season, year, len(entries), processed, failed)

    log.info("Bootstrap полностью завершён.")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log.info("Прервано пользователем — прогресс сохранён, можно продолжить позже.")
        sys.exit(0)