"""Парсер HTML stats-страниц аниме MyAnimeList.

Парсит /anime/{id}/{slug}/stats — таблицу "Recently Updated By"
с пользователями и их оценками, а также Summary Stats и Score Stats.
"""
from __future__ import annotations

import re

from bs4 import BeautifulSoup

from base_scraper import clean as _clean, clean_int as _clean_int, is_captcha_page


def _parse_episodes(text: str | None) -> int | None:
    """Парсит '28 / 28' или '5 / 28' → episodes_watched (первое число)."""
    if text is None:
        return None
    text = text.strip()
    m = re.search(r'(\d+)\s*/', text)
    if m:
        return int(m.group(1))
    return _clean_int(text)


# Статусы на stats-странице (строка как есть)
_STATS_STATUSES = {
    "Watching", "Completed", "On-Hold", "Dropped", "Plan to Watch",
}


def parse_stats_page(html: str | None) -> list[dict]:
    """Парсит HTML stats-страницы аниме.

    Возвращает [{username, profile_url, score, status, episodes_watched}, ...]
    """
    if not html:
        return []

    soup = BeautifulSoup(html, 'html.parser')
    table = soup.select_one('table.table-recently-updated')
    if not table:
        return []

    rows = table.select('tr')
    results = []

    for row in rows[1:]:
        tds = row.select('td')
        if len(tds) < 4:
            continue

        link = tds[0].select_one('a.word-break')
        if not link:
            continue
        href = link.get('href', '')
        username = _clean(link.get_text())
        if not username or '/profile/' not in href:
            continue

        score_raw = _clean(tds[1].get_text())
        score = _clean_int(score_raw) if score_raw != '-' else None

        status = _clean(tds[2].get_text())
        episodes_watched = _parse_episodes(_clean(tds[3].get_text()))

        results.append({
            'username': username,
            'profile_url': href,
            'score': score,
            'status': status,
            'episodes_watched': episodes_watched,
        })

    return results


def has_next_page(html: str | None) -> bool:
    """Проверяет, есть ли следующая страница в пагинации stats."""
    if not html:
        return False
    soup = BeautifulSoup(html, 'html.parser')
    for a in soup.select('div.spaceit a'):
        text = _clean(a.get_text())
        if text and 'next' in text.lower():
            return True
    return False


def parse_summary_stats(html: str | None) -> dict | None:
    """Парсит Summary Stats со stats-страницы.

    Возвращает {watching, completed, on_hold, dropped, plan_to_watch, total}
    или None если блок не найден.
    """
    if not html:
        return None

    soup = BeautifulSoup(html, 'html.parser')
    heading = soup.select_one('#summary_stats')
    if not heading:
        return None

    stats = {}
    for sibling in heading.next_elements:
        if getattr(sibling, 'name', None) == 'h2':
            break
        if getattr(sibling, 'name', None) == 'div' and 'spaceit_pad' in (sibling.get('class') or []):
            text = sibling.get_text()
            if ':' in text:
                label, _, value = text.partition(':')
                key = label.strip().lower().replace('-', '_').replace(' ', '_')
                stats[key] = _clean_int(value)

    return stats if stats else None


def parse_score_stats(html: str | None) -> dict | None:
    """Парсит Score Stats (разбивка оценок) со stats-страницы.

    Возвращает {10: 486578, 9: 235672, ...} (score → vote count) или None.
    """
    if not html:
        return None

    soup = BeautifulSoup(html, 'html.parser')
    table = soup.select_one('table.score-stats')
    if not table:
        return None

    result = {}
    for row in table.select('tr'):
        label_cell = row.select_one('td.score-label')
        if not label_cell:
            continue
        score = _clean_int(label_cell.get_text())
        if score is None:
            continue
        text = row.get_text()
        votes_m = re.search(r'\(([\d,]+)\s*votes?\)', text)
        if votes_m:
            result[score] = _clean_int(votes_m.group(1))

    return result if result else None


def is_not_found_page(html: str | None) -> bool:
    """Проверяет, является ли HTML 404-страницей."""
    if not html:
        return True
    lower = html[:5000].lower()
    return 'not found' in lower or 'page not found' in lower