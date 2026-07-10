"""Работа с сезонами MyAnimeList: winter/spring/summer/fall."""
from datetime import datetime, timezone

SEASON_ORDER = ["winter", "spring", "summer", "fall"]


def current_season():
    now = datetime.now(timezone.utc)
    month = now.month
    if month in (1, 2, 3):
        season = "winter"
    elif month in (4, 5, 6):
        season = "spring"
    elif month in (7, 8, 9):
        season = "summer"
    else:
        season = "fall"
    return now.year, season


def shift_season(year: int, season: str, delta: int):
    """Сдвигает сезон на delta шагов вперёд (delta>0) или назад (delta<0)."""
    idx = SEASON_ORDER.index(season) + delta
    year += idx // 4
    idx = idx % 4
    return year, SEASON_ORDER[idx]


def all_seasons(start_year: int = 1917):
    """Генерирует (year, season) от start_year/winter до следующего сезона включительно."""
    cy, cs = current_season()
    end_year, end_season = shift_season(cy, cs, 1)  # включаем следующий сезон (анонсы)

    year, season = start_year, "winter"
    while True:
        yield year, season
        if (year, season) == (end_year, end_season):
            break
        year, season = shift_season(year, season, 1)
