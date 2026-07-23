"""Общие утилиты парсинга HTML для MyAnimeList.

Дублировались в mal_scraper.py и user_scraper.py — вынесены сюда.
Будущие MAL-парсеры могут наследовать или импортировать эти функции.
"""
from __future__ import annotations

import re
from html import unescape


def clean(text: str | None) -> str | None:
    """Очищает текст: убирает лишние пробелы, переносы, HTML-entities."""
    if text is None:
        return None
    text = unescape(text)
    text = re.sub(r'\s+', ' ', text).strip()
    return text if text else None


def clean_int(text: str | None) -> int | None:
    """Парсит int из текста, убирая запятые и прочий мусор."""
    if text is None:
        return None
    text = re.sub(r'[,#\s]', '', text)
    if not text:
        return None
    try:
        return int(text)
    except ValueError:
        return None


def is_captcha_page(html: str | None) -> bool:
    """Проверяет, является ли HTML страницей CAPTCHA / Cloudflare challenge.

    MAL embeds recaptcha site key in meta tags on every page, so we can't
    just search for "captcha". Instead look for actual challenge indicators:
    - Cloudflare challenge body ("cf-challenge", "Just a moment")
    - <title> containing "captcha" or "attention required"
    """
    if not html:
        return False
    lower = html[:5000].lower()
    if 'cf-challenge' in lower or 'just a moment' in lower:
        return True
    title_m = re.search(r'<title[^>]*>(.*?)</title>', html, re.IGNORECASE | re.DOTALL)
    if title_m:
        title = title_m.group(1).lower()
        if 'captcha' in title or 'attention required' in title:
            return True
    return False