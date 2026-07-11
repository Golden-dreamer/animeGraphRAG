import logging

import db
import fetcher
import loader
import parser
from config import Config
from rules import compute_next_check

log = logging.getLogger("processing")


def process_one(mal_id: int, cfg: Config, force: bool = False):
    """Фетчит, парсит, льёт в Neo4j, обновляет next_check_at.

    Никогда не бросает исключения наружу — все ошибки записываются в SQLite
    через mark_failed(), тайтл получает retry-таймер и процесс продолжается.
    """
    try:
        raw = fetcher.get_anime_full(mal_id, delay_sec=cfg.request_delay_sec, force=force)
        data = parser.extract_fields(raw)
        if data is None or data.get("mal_id") is None:
            log.warning("mal_id=%s: пустой/некорректный ответ, откладываю", mal_id)
            db.mark_failed(mal_id, "пустой/некорректный ответ API",
                           cfg.max_attempts, cfg.retry_backoff_minutes)
            return
        loader.upsert_anime(data)
        next_check = compute_next_check(data, cfg)
        db.mark_parsed(mal_id, data.get("mal_status"), next_check)
        log.info("mal_id=%s '%s' обработан, next_check=%s",
                 mal_id, data.get("title_original"), next_check)
    except Exception as e:
        err_msg = f"{type(e).__name__}: {e}"
        log.error("mal_id=%s: ошибка обработки: %s", mal_id, err_msg)
        db.mark_failed(mal_id, err_msg, cfg.max_attempts, cfg.retry_backoff_minutes)