"""
Первичное наполнение архива: проходит все сезоны от 1917 года до текущего.
Полностью резюмируемое:
  - список сезона кэшируется на диске (для прошлых сезонов кэш бессрочный)
  - каждый обработанный тайтл сразу помечается в SQLite (next_check_at уходит в будущее)
  - если прогон прервался (упал контейнер, обрыв сети) — просто перезапустите:
      docker compose run --rm parsers python bootstrap.py
    он продолжит с того же места, ничего не скачивая повторно.

Специально НЕ обрабатывает текущий/следующий/прошлый сезон — этим постоянно
занимается scheduler.py (см. app.py), здесь нет смысла дублировать.

Ошибки при обработке отдельных тайтлов НЕ роняют процесс — тайтл получает
retry-таймер через mark_failed() и будет переобработан позже (в этом же
прогоне, если retry-таймер успеет наступить, или в следующем запуске).
"""
import logging
import sys

import db
import fetcher
from config import load_config
from mal_seasons import all_seasons, current_season, shift_season
from processing import process_one

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("bootstrap")


def main():
    cfg = load_config()
    db.init_db()
    fetcher.CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_files = list(fetcher.CACHE_DIR.glob("*.json"))
    log.info("Кэш: %s, файлов: %d", fetcher.CACHE_DIR, len(cache_files))

    cy, cs = current_season()
    ny, ns = shift_season(cy, cs, 1)
    py, ps = shift_season(cy, cs, -1)
    skip_seasons = {(cy, cs), (ny, ns), (py, ps)}  # эти держит scheduler.py

    for year, season in all_seasons(1917):
        if (year, season) in skip_seasons:
            continue
        if db.season_already_bootstrapped(year, season):
            continue

        log.info("=== Сезон %s %d ===", season, year)
        try:
            entries = fetcher.get_season_list(year, season, delay_sec=cfg.request_delay_sec)
        except Exception as e:
            log.error("Не удалось получить список сезона %s %d: %s — пропускаем", season, year, e)
            continue

        for e in entries:
            db.upsert_anime_stub(e["mal_id"], year, season)

        processed = 0
        failed = 0
        while True:
            batch = db.select_due_for_season(year, season, limit=cfg.batch_size)
            if not batch:
                break
            for mal_id in batch:
                try:
                    process_one(mal_id, cfg)
                    processed += 1
                except Exception as e:
                    # process_one не должен бросать, но на всякий случай
                    log.error("mal_id=%s: непредвиденная ошибка в process_one: %s", mal_id, e)
                    failed += 1

        db.mark_season_bootstrapped(year, season)
        log.info("Сезон %s %d завершён (%d тайтлов, обработано=%d, ошибок=%d)",
                 season, year, len(entries), processed, failed)

    log.info("Bootstrap полностью завершён.")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log.info("Прервано пользователем — прогресс сохранён, можно продолжить позже.")
        sys.exit(0)