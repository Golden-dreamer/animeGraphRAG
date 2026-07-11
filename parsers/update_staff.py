"""Дополнение staff в Neo4j для уже обработанных аниме.

Проблема: из-за бага в fetcher.py (короткий URL /anime/{id}/characters
редиректился на основную страницу) в БД остался неполный staff — только
те люди, что перечислены на основной странице аниме (обычно 2-4 человека).
Полная страница /characters (со slug в URL) содержит всех участников.

Этот скрипт:
  1. Запрашивает Neo4j: какие аниме имеют <= threshold staff-связей
  2. Для каждого заново фетчит /characters страницу (с правильным slug)
  3. Парсит staff и обновляет связи в Neo4j (MERGE — не дублирует)

Запуск:
    docker compose run --rm parsers python update_staff.py
    docker compose run --rm parsers python update_staff.py --limit 100
"""
import argparse
import logging
import sys

import fetcher
from config import load_config
from mal_scraper import parse_anime_page, parse_characters_page, extract_slug_from_url
from processing import process_one

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("update_staff")

# Аниме с количеством staff <= этого порога считаются "неполными"
MAX_STAFF_THRESHOLD = 4


def get_anime_needing_staff_refresh(threshold: int = MAX_STAFF_THRESHOLD) -> list[int]:
    """Возвращает mal_id аниме, у которых staff <= threshold (или 0)."""
    from loader import get_driver
    with get_driver().session() as session:
        result = session.run(
            """
            MATCH (a:Anime)
            OPTIONAL MATCH (a)-[:STAFF]->(p:Person)
            WITH a, count(p) AS staff_count
            WHERE staff_count <= $threshold
            RETURN a.mal_id AS mal_id
            ORDER BY a.mal_id
            """,
            threshold=threshold,
        )
        return [r["mal_id"] for r in result if r["mal_id"] is not None]


def update_staff_for_anime(mal_id: int, cfg) -> bool:
    """Заново фетчит /characters страницу и обновляет staff в Neo4j.

    Возвращает True если staff был обновлён (или подтверждён), False при ошибке.
    """
    try:
        from loader import upsert_staff_only

        # Фетчим основную страницу (нужна для slug)
        url_main = f"{fetcher.BASE_URL}/anime/{mal_id}"
        html_main = fetcher.get_html(url_main)
        data_main = parse_anime_page(html_main)
        if data_main is None:
            log.warning("mal_id=%s: не удалось распарсить основную страницу", mal_id)
            return False

        # Извлекаем slug и строим полный URL /characters
        slug = extract_slug_from_url(html_main)
        if slug:
            url_chars = f"{fetcher.BASE_URL}/anime/{mal_id}/{slug}/characters"
        else:
            url_chars = f"{fetcher.BASE_URL}/anime/{mal_id}/characters"

        html_chars = fetcher.get_html(url_chars)
        chars_data = parse_characters_page(html_chars)

        staff = chars_data.get("staff", [])
        if not staff:
            log.info("mal_id=%s: staff не найден на /characters (возможно, genuinely empty)", mal_id)
            return True  # не ошибка — просто нет staff на MAL

        # Записываем staff в Neo4j
        upsert_staff_only(mal_id, staff)
        log.info("mal_id=%s '%s': staff обновлён (%d человек)",
                 mal_id, data_main.get("title_original"), len(staff))
        return True

    except Exception as e:
        log.error("mal_id=%s: ошибка обновления staff: %s", mal_id, e)
        return False


def main():
    parser = argparse.ArgumentParser(description="Дополнение staff в Neo4j")
    parser.add_argument("--limit", type=int, default=None,
                        help="Максимум аниме для обработки (без лимита — все)")
    parser.add_argument("--threshold", type=int, default=MAX_STAFF_THRESHOLD,
                        help=f"Порог staff-связей: аниме с <= threshold обновляются (по умолчанию {MAX_STAFF_THRESHOLD})")
    args = parser.parse_args()

    cfg = load_config()

    log.info("Запрос к Neo4j: аниме с staff <= %d ...", args.threshold)
    mal_ids = get_anime_needing_staff_refresh(args.threshold)
    log.info("Найдено %d аниме для обновления staff", len(mal_ids))

    if args.limit:
        mal_ids = mal_ids[:args.limit]
        log.info("Ограничение --limit=%d, обрабатываем %d", args.limit, len(mal_ids))

    processed = 0
    failed = 0
    for mal_id in mal_ids:
        ok = update_staff_for_anime(mal_id, cfg)
        if ok:
            processed += 1
        else:
            failed += 1

    log.info("Готово: обновлено %d, ошибок %d (из %d)", processed, failed, len(mal_ids))


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log.info("Прервано пользователем — прогресс сохранён.")
        sys.exit(0)