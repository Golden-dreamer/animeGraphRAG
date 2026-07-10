import db
import fetcher
from config import Config
from mal_seasons import current_season, shift_season


def discover_recent(cfg: Config):
    """Регистрирует в очереди тайтлы текущего, следующего и прошлого сезона.
    Список сезона кэшируется на cycle_interval_sec, так что реальный HTTP-запрос
    уходит нечасто, а не при каждом цикле."""
    cy, cs = current_season()
    ny, ns = shift_season(cy, cs, 1)
    py, ps = shift_season(cy, cs, -1)

    for year, season in [(cy, cs), (ny, ns), (py, ps)]:
        entries = fetcher.get_season_list(year, season, delay_sec=cfg.request_delay_sec)
        for e in entries:
            db.upsert_anime_stub(e["mal_id"], year, season)
