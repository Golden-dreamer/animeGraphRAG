"""Регистрирует в графе тайтлы текущего, следующего и прошлого сезона."""
import graph_state
import fetcher
from config import Config
from mal_seasons import current_season, shift_season


def discover_recent(cfg: Config) -> int:
    """Запрашивает сезонные страницы MAL и регистрирует новые тайтлы в графе.
    Возвращает количество вновь зарегистрированных тайтлов."""
    cy, cs = current_season()
    ny, ns = shift_season(cy, cs, 1)
    py, ps = shift_season(cy, cs, -1)

    added = 0
    for year, season in [(cy, cs), (ny, ns), (py, ps)]:
        entries = fetcher.get_season_list(year, season, delay_sec=cfg.request_delay_sec)
        for e in entries:
            added += graph_state.upsert_anime_stub(e["mal_id"], year, season)
    return added