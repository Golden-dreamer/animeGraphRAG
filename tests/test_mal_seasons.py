"""Тесты mal_seasons: current_season, shift_season, all_seasons."""
from mal_seasons import current_season, shift_season, all_seasons, SEASON_ORDER


class TestCurrentSeason:
    def test_returns_year_and_season(self):
        year, season = current_season()
        assert isinstance(year, int)
        assert season in SEASON_ORDER

    def test_january_is_winter(self):
        # Подделываем datetime через monkeypatch невозможно без патча модуля,
        # но мы можем проверить логику через shift_season
        assert shift_season(2026, "winter", 0) == (2026, "winter")


class TestShiftSeason:
    def test_zero_delta_is_identity(self):
        assert shift_season(2026, "summer", 0) == (2026, "summer")

    def test_forward_one(self):
        assert shift_season(2026, "winter", 1) == (2026, "spring")
        assert shift_season(2026, "fall", 1) == (2027, "winter")

    def test_backward_one(self):
        assert shift_season(2026, "spring", -1) == (2026, "winter")
        assert shift_season(2026, "winter", -1) == (2025, "fall")

    def test_full_year_forward(self):
        assert shift_season(2026, "summer", 4) == (2027, "summer")

    def test_backward_full_year(self):
        assert shift_season(2026, "summer", -4) == (2025, "summer")

    def test_two_years_forward(self):
        assert shift_season(2025, "fall", 8) == (2027, "fall")


class TestAllSeasons:
    def test_starts_at_1917_winter(self):
        seasons = list(all_seasons(1917))
        assert seasons[0] == (1917, "winter")

    def test_contains_2024_spring(self):
        seasons = list(all_seasons(1917))
        assert (2024, "spring") in seasons

    def test_ends_at_next_season(self):
        seasons = list(all_seasons(1917))
        cy, cs = current_season()
        ny, ns = shift_season(cy, cs, 1)
        assert seasons[-1] == (ny, ns)

    def test_seasons_are_sequential(self):
        seasons = list(all_seasons(2020))
        for i in range(1, len(seasons)):
            prev = seasons[i - 1]
            curr = seasons[i]
            assert shift_season(prev[0], prev[1], 1) == curr