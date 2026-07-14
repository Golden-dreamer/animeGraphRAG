"""Тесты mal_scraper: утилиты, extract_slug, parse_season_page."""
import pytest
from mal_scraper import (
    _clean, _clean_int, _clean_float, _extract_id_from_url,
    _extract_type_from_url, extract_slug_from_url, parse_season_page,
    parse_anime_page, parse_characters_page,
)


class TestClean:
    def test_strips_whitespace(self):
        assert _clean("  hello  ") == "hello"

    def test_collapses_internal_spaces(self):
        assert _clean("hello    world") == "hello world"

    def test_strips_newlines(self):
        assert _clean("hello\nworld") == "hello world"

    def test_none_input(self):
        assert _clean(None) is None

    def test_empty_string(self):
        assert _clean("") is None

    def test_unescapes_html_entities(self):
        assert _clean("Hello &amp; Goodbye") == "Hello & Goodbye"


class TestCleanInt:
    def test_simple_number(self):
        assert _clean_int("123") == 123

    def test_with_commas(self):
        assert _clean_int("1,234,567") == 1234567

    def test_with_hash(self):
        assert _clean_int("#42") == 42

    def test_none_input(self):
        assert _clean_int(None) is None

    def test_invalid(self):
        assert _clean_int("abc") is None

    def test_empty(self):
        assert _clean_int("") is None


class TestCleanFloat:
    def test_simple_float(self):
        assert _clean_float("8.45") == 8.45

    def test_na(self):
        assert _clean_float("N/A") is None

    def test_none(self):
        assert _clean_float(None) is None

    def test_empty(self):
        assert _clean_float("") is None


class TestExtractIdFromUrl:
    def test_anime_url(self):
        assert _extract_id_from_url("https://myanimelist.net/anime/5249/Mitsume") == 5249

    def test_manga_url(self):
        assert _extract_id_from_url("https://myanimelist.net/manga/141772/Title") == 141772

    def test_character_url(self):
        assert _extract_id_from_url("https://myanimelist.net/character/204764/Name") == 204764

    def test_people_url(self):
        assert _extract_id_from_url("https://myanimelist.net/people/819/Name") == 819

    def test_no_match(self):
        assert _extract_id_from_url("https://example.com") is None

    def test_none(self):
        assert _extract_id_from_url(None) is None


class TestExtractTypeFromUrl:
    def test_anime(self):
        assert _extract_type_from_url("https://myanimelist.net/anime/123/Title") == "anime"

    def test_manga(self):
        assert _extract_type_from_url("https://myanimelist.net/manga/456/Title") == "manga"

    def test_none(self):
        assert _extract_type_from_url(None) is None


class TestExtractSlugFromUrl:
    def test_canonical_link(self):
        html = '<link rel="canonical" href="https://myanimelist.net/anime/5249/Mitsume_ga_Tooru">'
        assert extract_slug_from_url(html) == "Mitsume_ga_Tooru"

    def test_og_url_fallback(self):
        html = '<meta property="og:url" content="https://myanimelist.net/anime/16498/Attack_on_Titan">'
        assert extract_slug_from_url(html) == "Attack_on_Titan"

    def test_no_url(self):
        html = "<html><body>nothing</body></html>"
        assert extract_slug_from_url(html) is None

    def test_canonical_over_og(self):
        html = '''<link rel="canonical" href="https://myanimelist.net/anime/1/First">
        <meta property="og:url" content="https://myanimelist.net/anime/1/Second">'''
        assert extract_slug_from_url(html) == "First"


class TestParseSeasonPage:
    def test_empty_html(self):
        assert parse_season_page("<html></html>") == []

    def test_parses_anime_entries(self):
        html = """
        <html><body>
        <div class="js-seasonal-anime">
            <h2><a class="link-title" href="https://myanimelist.net/anime/5249/Test_Title">Test Title</a></h2>
        </div>
        <div class="js-seasonal-anime">
            <h2><a class="link-title" href="https://myanimelist.net/anime/16498/Another_Anime">Another Anime</a></h2>
        </div>
        </body></html>
        """
        result = parse_season_page(html)
        assert len(result) == 2
        assert result[0]["mal_id"] == 5249
        assert result[0]["title"] == "Test Title"
        assert result[1]["mal_id"] == 16498

    def test_skips_entries_without_link(self):
        html = """
        <html><body>
        <div class="js-seasonal-anime"><p>no link here</p></div>
        </body></html>
        """
        assert parse_season_page(html) == []


class TestParseAnimePage:
    def test_invalid_page_returns_none(self):
        html = "<html><body>not an anime page</body></html>"
        assert parse_anime_page(html) is None

    def test_minimal_valid_page(self):
        html = """
        <html><head>
            <link rel="canonical" href="https://myanimelist.net/anime/5249/Test_Anime">
            <meta property="og:url" content="https://myanimelist.net/anime/5249/Test_Anime">
        </head><body>
            <h1 class="title-name">Test Anime</h1>
            <p class="title-english">Test English</p>
        </body></html>
        """
        result = parse_anime_page(html)
        assert result is not None
        assert result["mal_id"] == 5249
        assert result["title_original"] == "Test Anime"
        assert result["title_english"] == "Test English"

    def test_parses_premiered(self):
        html = """
        <html><head>
            <link rel="canonical" href="https://myanimelist.net/anime/123/Test">
        </head><body>
            <h1 class="title-name">Test</h1>
            <h2>Information</h2>
            <div class="spaceit_pad"><span class="dark_text">Premiered:</span> Spring 2024</div>
        </body></html>
        """
        result = parse_anime_page(html)
        assert result["premiered"] == "Spring 2024"
        assert result["season"] == "spring"
        assert result["year"] == 2024


class TestParseCharactersPage:
    def test_empty_page(self):
        result = parse_characters_page("<html></html>")
        assert result == {"characters": [], "staff": []}

    def test_parses_staff(self):
        # MAL format: <a> and <small> in same <td>
        html = """
        <html><body>
        <h2>Staff</h2>
        <table>
            <tr>
                <td>
                    <a href="https://myanimelist.net/people/123/Director_Name">Director Name</a>
                    <small>Director</small>
                </td>
            </tr>
        </table>
        </body></html>
        """
        result = parse_characters_page(html)
        assert len(result["staff"]) == 1
        assert result["staff"][0]["mal_id"] == 123
        assert result["staff"][0]["name"] == "Director Name"
        assert "Director" in result["staff"][0]["roles"]