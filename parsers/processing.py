"""Обработка одного тайтла: фетчит, парсит, льёт в Neo4j.

Без retry-логики. Если обработка падает — тайтл остаётся со старыми
данными (или без title, если это stub). Scheduler попробует снова
в следующем цикле (для актуальных) или при ручном /refresh.
"""
import logging

import fetcher
import loader
import parser
from config import Config

log = logging.getLogger("processing")


def process_one(mal_id: int, cfg: Config):
    """Фетчит страницу аниме + characters/staff, парсит, льёт в Neo4j.

    Никогда не бросает исключения наружу — логирует ошибку и возвращается.
    title IS NULL на узле означает "не обработан" — scheduler подберёт снова.
    """
    try:
        raw = fetcher.get_anime_full(mal_id, delay_sec=cfg.request_delay_sec)
        data = parser.extract_fields(raw)
        if data is None or data.get("mal_id") is None:
            log.warning("mal_id=%s: пустой/некорректный ответ, пропускаю", mal_id)
            return
        loader.upsert_anime(data)
        log.info("mal_id=%s '%s' обработан", mal_id, data.get("title_original"))
    except Exception as e:
        err_msg = f"{type(e).__name__}: {e}"
        log.error("mal_id=%s: ошибка обработки: %s", mal_id, err_msg)