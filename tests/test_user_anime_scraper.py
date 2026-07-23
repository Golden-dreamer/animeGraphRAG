"""Тесты user-anime scraper: parse_stats_page, has_next_page,
parse_summary_stats, parse_score_stats, is_captcha_page, is_not_found_page.
"""
import os
import sys
import importlib.util

# Загружаем user_anime/scraper.py как отдельный модуль (без конфликтов имён)
_mod_path = os.path.join(os.path.dirname(__file__), '..', 'parsers', 'user_anime', 'scraper.py')
_spec = importlib.util.spec_from_file_location('user_anime_scraper', _mod_path)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)
sys.modules['user_anime_scraper'] = _mod

# base_scraper из parsers/
_base_path = os.path.join(os.path.dirname(__file__), '..', 'parsers', 'base_scraper.py')
_base_spec = importlib.util.spec_from_file_location('base_scraper', _base_path)
_base_mod = importlib.util.module_from_spec(_base_spec)
_base_spec.loader.exec_module(_base_mod)
sys.modules['base_scraper'] = _base_mod

from user_anime_scraper import (
    parse_stats_page, has_next_page, parse_summary_stats, parse_score_stats,
    is_not_found_page,
)
from base_scraper import is_captcha_page

FIXTURES_DIR = os.path.join(os.path.dirname(__file__), 'fixtures')


def _load_fixture(name: str) -> str:
    with open(os.path.join(FIXTURES_DIR, name), encoding='utf-8') as f:
        return f.read()


# ---------------------------------------------------------------------------
# parse_stats_page
# ---------------------------------------------------------------------------


class TestParseStatsPage:
    def test_returns_empty_list_on_empty_html(self):
        assert parse_stats_page('<html></html>') == []

    def test_returns_empty_list_on_none(self):
        assert parse_stats_page(None) == []

    def test_returns_empty_list_on_no_table(self):
        assert parse_stats_page('<html><body>no table here</body></html>') == []

    def test_parses_real_sample_first_user(self):
        html = _load_fixture('stats_sample.html')
        result = parse_stats_page(html)
        assert len(result) == 75
        first = result[0]
        assert first['username'] == 'DeathSpoonz'
        assert first['profile_url'] == 'https://myanimelist.net/profile/DeathSpoonz'
        assert first['score'] is None
        assert first['status'] == 'Watching'
        assert first['episodes_watched'] == 1

    def test_parses_user_with_score(self):
        html = _load_fixture('stats_sample.html')
        result = parse_stats_page(html)
        starry = [r for r in result if r['username'] == 'starry_nytt'][0]
        assert starry['score'] == 8
        assert starry['status'] == 'Completed'
        assert starry['episodes_watched'] == 28

    def test_all_usernames_are_unique(self):
        html = _load_fixture('stats_sample.html')
        result = parse_stats_page(html)
        usernames = [r['username'] for r in result]
        assert len(usernames) == len(set(usernames))

    def test_synthetic_minimal_html(self):
        html = """<html><body>
        <table class="table-recently-updated">
        <tr><td>Member</td><td>Score</td><td>Status</td><td>Eps Seen</td></tr>
        <tr>
            <td><div class="di-tc"><a href="/profile/TestUser" class="word-break">TestUser</a></div></td>
            <td class="ac">9</td>
            <td class="ac">Completed</td>
            <td class="ac">24 / 24</td>
            <td class="ac">2 hours ago</td>
        </tr>
        </table>
        </body></html>"""
        result = parse_stats_page(html)
        assert len(result) == 1
        assert result[0]['username'] == 'TestUser'
        assert result[0]['score'] == 9
        assert result[0]['status'] == 'Completed'
        assert result[0]['episodes_watched'] == 24

    def test_dash_score_becomes_none(self):
        html = """<table class="table-recently-updated">
        <tr><td>Member</td><td>Score</td><td>Status</td><td>Eps</td></tr>
        <tr>
            <td><a href="/profile/NoScore" class="word-break">NoScore</a></td>
            <td class="ac">-</td>
            <td class="ac">Watching</td>
            <td class="ac">5 / 12</td>
        </tr>
        </table>"""
        result = parse_stats_page(html)
        assert result[0]['score'] is None
        assert result[0]['episodes_watched'] == 5


# ---------------------------------------------------------------------------
# has_next_page
# ---------------------------------------------------------------------------


class TestHasNextPage:
    def test_true_on_first_page(self):
        html = _load_fixture('stats_sample.html')
        assert has_next_page(html) == True

    def test_false_on_empty_html(self):
        assert has_next_page(None) == False
        assert has_next_page('<html></html>') == False

    def test_false_on_last_page(self):
        html = """<html><body>
        <div class="spaceit"><a href="?show=7400">Previous</a></div>
        <table class="table-recently-updated"><tr><td>header</td></tr></table>
        </body></html>"""
        assert has_next_page(html) == False

    def test_true_with_next_link(self):
        html = """<html><body>
        <div class="spaceit"><a href="?show=75#members">Next Page</a></div>
        </body></html>"""
        assert has_next_page(html) == True


# ---------------------------------------------------------------------------
# parse_summary_stats
# ---------------------------------------------------------------------------


class TestParseSummaryStats:
    def test_parses_real_sample(self):
        html = _load_fixture('stats_sample.html')
        result = parse_summary_stats(html)
        assert result is not None
        assert result['total'] == 1484336
        assert result['watching'] == 240011
        assert result['completed'] == 975291
        assert result['on_hold'] == 30066
        assert result['dropped'] == 24579
        assert result['plan_to_watch'] == 214389

    def test_returns_none_on_empty(self):
        assert parse_summary_stats(None) is None
        assert parse_summary_stats('<html></html>') is None


# ---------------------------------------------------------------------------
# parse_score_stats
# ---------------------------------------------------------------------------


class TestParseScoreStats:
    def test_parses_real_sample(self):
        html = _load_fixture('stats_sample.html')
        result = parse_score_stats(html)
        assert result is not None
        assert 10 in result
        assert 9 in result
        assert result[10] == 486578
        assert result[9] == 235672

    def test_returns_none_on_empty(self):
        assert parse_score_stats(None) is None
        assert parse_score_stats('<html></html>') is None


# ---------------------------------------------------------------------------
# is_captcha_page / is_not_found_page
# ---------------------------------------------------------------------------


class TestIsCaptchaPage:
    def test_detects_captcha_title(self):
        html = '<html><title>Captcha Required</title><body>...</body></html>'
        assert is_captcha_page(html) == True

    def test_detects_cloudflare(self):
        assert is_captcha_page('<html>cf-challenge</html>') == True

    def test_normal_page(self):
        html = _load_fixture('stats_sample.html')
        assert is_captcha_page(html) == False

    def test_none(self):
        assert is_captcha_page(None) == False


class TestIsNotFoundPage:
    def test_detects_not_found(self):
        assert is_not_found_page('<html>Page not found</html>') == True

    def test_normal_page(self):
        html = _load_fixture('stats_sample.html')
        assert is_not_found_page(html) == False

    def test_none(self):
        assert is_not_found_page(None) == True