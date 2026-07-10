from datetime import timedelta

import db
from config import Config
from mal_seasons import current_season, shift_season


def compute_next_check(entry: dict, cfg: Config) -> str:
    """
    Правило:
      - текущий или следующий сезон -> раз в сутки
      - прошлый сезон -> раз в неделю
      - младше N лет (но не входит в предыдущие категории) -> раз в год
      - старше N лет -> никогда автоматически (только force_refresh)
    """
    cy, cs = current_season()
    ny, ns = shift_season(cy, cs, 1)
    py, ps = shift_season(cy, cs, -1)

    key = (entry.get("year"), entry.get("season"))

    if key in [(cy, cs), (ny, ns)]:
        delta = timedelta(days=cfg.refresh_current_days)
    elif key == (py, ps):
        delta = timedelta(days=cfg.refresh_previous_days)
    elif entry.get("year") and (cy - entry["year"]) <= cfg.refresh_recent_years:
        delta = timedelta(days=cfg.refresh_recent_days)
    else:
        return db.FAR_FUTURE

    from datetime import datetime, timezone
    return (datetime.now(timezone.utc) + delta).isoformat()
