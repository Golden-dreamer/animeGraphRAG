"""Проверка и дополнение БД недостающими аниме.

Сверяет списки тайтлов на сезонных страницах MAL с узлами в Neo4j,
и добавляет недостающие тайтлы как stub'ы (title IS NULL).

Запуск:
    docker compose run --rm parsers python check_missing.py
    docker compose run --rm parsers python check_missing.py --season 2026 summer
    docker compose run --rm parsers python check_missing.py --all  # все сезоны
"""
import argparse
import logging
import sys

import fetcher
import graph_state
from config import load_config
from mal_seasons import all_seasons, current_season, shift_season

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("check_missing")


def check_season(year: int, season: str) -> tuple[int, int]:
    """Проверяет один сезон: сравнивает список MAL с Neo4j, добавляет недостающие.
    Возвращает (total_on_mal, added_to_db)."""
    entries = fetcher.get_season_list(year, season, delay_sec=0)
    total = len(entries)

    added = 0
    for e in entries:
        added += graph_state.upsert_anime_stub(e["mal_id"], year, season)

    if added:
        log.info("Сезон %s %d: на MAL %d тайтлов, добавлено %d недостающих",
                 season, year, total, added)
    else:
        log.info("Сезон %s %d: на MAL %d тайтлов, все в БД", season, year, total)

    return total, added


def main():
    parser = argparse.ArgumentParser(description="Проверка и дополнение БД недостающими аниме")
    parser.add_argument("--season", nargs=2, metavar=("YEAR", "SEASON"),
                        help="Проверить конкретный сезон (например: 2026 summer)")
    parser.add_argument("--all", action="store_true",
                        help="Проверить все сезоны от 1917 до текущего")
    args = parser.parse_args()

    load_config()

    if args.season:
        year, season = int(args.season[0]), args.season[1].lower()
        check_season(year, season)
    elif args.all:
        cy, cs = current_season()
        ny, ns = shift_season(cy, cs, 1)
        total_added = 0
        total_checked = 0
        for year, season in all_seasons(1917):
            if (year, season) == (ny, ns):
                break  # следующий сезон ещё не полный
            _, added = check_season(year, season)
            total_added += added
            total_checked += 1
        log.info("Проверено %d сезонов, добавлено %d недостающих тайтлов",
                 total_checked, total_added)
    else:
        # По умолчанию: проверяем текущий, следующий и прошлый сезоны
        cy, cs = current_season()
        ny, ns = shift_season(cy, cs, 1)
        py, ps = shift_season(cy, cs, -1)
        for year, season in [(cy, cs), (ny, ns), (py, ps)]:
            check_season(year, season)

    log.info("Готово. Недостающие тайтлы добавлены как stub'ы (title IS NULL).")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log.info("Прервано пользователем.")
        sys.exit(0)