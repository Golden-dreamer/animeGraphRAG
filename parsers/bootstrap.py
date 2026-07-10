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
        entries = fetcher.get_season_list(year, season, delay_sec=cfg.request_delay_sec)
        for e in entries:
            db.upsert_anime_stub(e["mal_id"], year, season)

        while True:
            batch = db.select_due_for_season(year, season, limit=cfg.batch_size)
            if not batch:
                break
            for mal_id in batch:
                process_one(mal_id, cfg)

        db.mark_season_bootstrapped(year, season)
        log.info("Сезон %s %d завершён (%d тайтлов)", season, year, len(entries))

    log.info("Bootstrap полностью завершён.")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log.info("Прервано пользователем — прогресс сохранён, можно продолжить позже.")
        sys.exit(0)
