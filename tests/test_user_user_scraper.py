"""Тесты user-user scraper: parse_animelist, is_captcha_page, is_not_found_page.
"""
import json
import os
import sys
import importlib.util

# Загружаем user_user/scraper.py как отдельный модуль
_mod_path = os.path.join(os.path.dirname(__file__), '..', 'parsers', 'user_user', 'scraper.py')
_spec = importlib.util.spec_from_file_location('user_user_scraper', _mod_path)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)
sys.modules['user_user_scraper'] = _mod

# base_scraper из parsers/ (если ещё не загружен)
if 'base_scraper' not in sys.modules:
    _base_path = os.path.join(os.path.dirname(__file__), '..', 'parsers', 'base_scraper.py')
    _base_spec = importlib.util.spec_from_file_location('base_scraper', _base_path)
    _base_mod = importlib.util.module_from_spec(_base_spec)
    _base_spec.loader.exec_module(_base_mod)
    sys.modules['base_scraper'] = _base_mod

from user_user_scraper import parse_animelist, is_not_found_page
from base_scraper import is_captcha_page

FIXTURES_DIR = os.path.join(os.path.dirname(__file__), 'fixtures')


# ---------------------------------------------------------------------------
# parse_animelist
# ---------------------------------------------------------------------------


class TestParseAnimelist:
    def test_parses_real_sample(self):
        with open(os.path.join(FIXTURES_DIR, 'animelist_sample.json'), encoding='utf-8') as f:
            data = json.load(f)
        result = parse_animelist(data)
        assert len(result) == 224
        first = result[0]
        assert first['mal_id'] == 1575  # Code Geass
        assert 'status' in first
        assert 'score' in first
        assert 'episodes_watched' in first

    def test_score_zero_becomes_none(self):
        data = [{'anime_id': 1, 'score': 0, 'status': 2, 'num_watched_episodes': 12, 'tags': ''}]
        result = parse_animelist(data)
        assert result[0]['score'] is None

    def test_score_positive_preserved(self):
        data = [{'anime_id': 1, 'score': 7, 'status': 2, 'num_watched_episodes': 12, 'tags': ''}]
        result = parse_animelist(data)
        assert result[0]['score'] == 7

    def test_status_mapped_correctly(self):
        data = [{'anime_id': 1, 'score': 0, 'status': 1, 'num_watched_episodes': 0, 'tags': ''}]
        result = parse_animelist(data)
        assert result[0]['status'] == 'Watching'

    def test_empty_list(self):
        assert parse_animelist([]) == []
        assert parse_animelist(None) == []

    def test_tags_preserved(self):
        data = [{'anime_id': 1, 'score': 5, 'status': 2, 'num_watched_episodes': 12, 'tags': 'best anime'}]
        result = parse_animelist(data)
        assert result[0]['tags'] == 'best anime'

    def test_tags_empty_becomes_none(self):
        data = [{'anime_id': 1, 'score': 5, 'status': 2, 'num_watched_episodes': 12, 'tags': ''}]
        result = parse_animelist(data)
        assert result[0]['tags'] is None


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
        assert is_captcha_page('<html><body>normal</body></html>') == False

    def test_none(self):
        assert is_captcha_page(None) == False


class TestIsNotFoundPage:
    def test_detects_not_found(self):
        assert is_not_found_page('<html>Page not found</html>') == True

    def test_normal_page(self):
        assert is_not_found_page('<html><body>normal</body></html>') == False

    def test_none(self):
        assert is_not_found_page(None) == True