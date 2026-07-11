"""Парсер HTML страниц MyAnimeList.

Извлекает все поля со страниц аниме, сезона и characters/staff.
Возвращает плоский dict, готовый для записи в Neo4j через loader.py.

Структура данных:
  - Основная страница (/anime/{id}/{title}): titles, info, stats, synopsis,
    background, related entries, resources, streaming platforms
  - Страница characters (/anime/{id}/{title}/characters): characters + voice actors,
    staff (два блока на одной странице)
"""
from __future__ import annotations

import re
from html import unescape

from bs4 import BeautifulSoup


# ---------------------------------------------------------------------------
# Утилиты
# ---------------------------------------------------------------------------

def _clean(text: str | None) -> str | None:
    """Очищает текст: убирает лишние пробелы, переносы, HTML-entities."""
    if text is None:
        return None
    text = unescape(text)
    text = re.sub(r'\s+', ' ', text).strip()
    return text if text else None


def _clean_int(text: str | None) -> int | None:
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


def _clean_float(text: str | None) -> float | None:
    if text is None:
        return None
    text = text.strip()
    if not text or text == 'N/A':
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _extract_id_from_url(url: str) -> int | None:
    """Извлекает ID из URL вида https://myanimelist.net/anime/62001/... или
    https://myanimelist.net/manga/141772/... или
    https://myanimelist.net/character/204764/... или
    https://myanimelist.net/people/819/..."""
    m = re.search(r'/(anime|manga|character|people)/(\d+)', url or '')
    if m:
        return int(m.group(2))
    return None


def _extract_type_from_url(url: str) -> str | None:
    """Определяет тип сущности (anime/manga/character/people) из URL."""
    m = re.search(r'/(anime|manga|character|people)/(\d+)', url or '')
    if m:
        return m.group(1)
    return None


def extract_slug_from_url(html: str) -> str | None:
    """Извлекает slug аниме из canonical/og:url URL основной страницы.

    URL вида https://myanimelist.net/anime/5249/Mitsume_ga_Tooru
    возвращает 'Mitsume_ga_Tooru'. Нужно для построения полного URL
    страницы /characters — MAL требует полный URL со slug, иначе
    редиректит на основную страницу (где staff неполный).
    """
    soup = BeautifulSoup(html, 'html.parser')
    # Сначала canonical, потом og:url
    link = soup.select_one('link[rel="canonical"]')
    url = link.get('href') if link and link.get('href') else ''
    if not url:
        og = soup.select_one('meta[property="og:url"]')
        url = og.get('content') if og and og.get('content') else ''
    if not url:
        return None
    # URL: https://myanimelist.net/anime/5249/Mitsume_ga_Tooru
    m = re.search(r'/anime/\d+/([^/?#]+)', url)
    if m:
        return m.group(1)
    return None


# ---------------------------------------------------------------------------
# Парсер основной страницы аниме
# ---------------------------------------------------------------------------

def parse_anime_page(html: str) -> dict | None:
    """Парсит HTML основной страницы аниме.

    Возвращает dict со всеми полями или None если страница невалидна.
    """
    soup = BeautifulSoup(html, 'html.parser')

    # Проверка, что это страница аниме
    title_tag = soup.select_one('h1.title-name')
    if not title_tag:
        return None

    result = {}

    # --- Базовые поля ---
    og_url_tag = soup.select_one('meta[property="og:url"]')
    og_url = og_url_tag.get('content', '') if og_url_tag else ''
    result['mal_id'] = _extract_id_from_url(og_url or _find_canonical_url(soup))
    # Fallback: ищем ID вCanonical
    if result['mal_id'] is None:
        link = soup.select_one('a[href*="/anime/"]')
        if link:
            result['mal_id'] = _extract_id_from_url(link.get('href', ''))

    result['title_original'] = _clean(title_tag.get_text())
    title_english_tag = soup.select_one('p.title-english')
    result['title_english'] = _clean(title_english_tag.get_text()) if title_english_tag else None

    # --- Poster ---
    poster_tag = soup.select_one('div.leftside img[itemprop="image"]')
    if poster_tag:
        result['poster_url'] = poster_tag.get('data-src') or poster_tag.get('src')
    else:
        result['poster_url'] = None

    # --- Alternative Titles ---
    result['title_synonyms'] = _parse_alt_titles(soup, 'Synonyms')
    result['title_japanese'] = _parse_alt_titles(soup, 'Japanese')
    result['title_english_official'] = _parse_alt_titles(soup, 'English')

    # --- Information ---
    result.update(_parse_information(soup))

    # --- Statistics ---
    result.update(_parse_statistics(soup))

    # --- Synopsis ---
    synopsis_tag = soup.select_one('p[itemprop="description"]')
    result['synopsis'] = _clean(synopsis_tag.get_text()) if synopsis_tag else None

    # --- Background ---
    bg_header = soup.find('h2', id='background')
    if bg_header:
        # h2 внутри div. Текст background — в следующем элементе-брате этого div
        parent_div = bg_header.parent
        next_el = parent_div.find_next_sibling() if parent_div else None
        if next_el and next_el.name in ('i', 'p', 'div'):
            result['background'] = _clean(next_el.get_text())
        elif next_el:
            result['background'] = _clean(next_el.get_text())
        else:
            result['background'] = None
    else:
        result['background'] = None

    # --- Related Entries ---
    result['related'] = _parse_related_entries(soup)

    # --- Available At (official links) ---
    result['available_at'] = _parse_external_links(soup, 'Available At')

    # --- Resources ---
    result['resources'] = _parse_external_links(soup, 'Resources')

    # Добавляем ссылку на саму страницу MAL в resources
    mal_url = _find_canonical_url(soup)
    if mal_url:
        result['resources'].insert(0, {
            'url': mal_url,
            'name': 'MyAnimeList',
        })

    # --- Streaming Platforms ---
    result['streaming_platforms'] = _parse_streaming_platforms(soup)

    return result


def _find_canonical_url(soup: BeautifulSoup) -> str:
    # Сначала canonical link, потом og:url
    link = soup.select_one('link[rel="canonical"]')
    if link and link.get('href'):
        return link['href']
    og = soup.select_one('meta[property="og:url"]')
    if og and og.get('content'):
        return og['content']
    return ''


def _parse_alt_titles(soup: BeautifulSoup, label: str) -> str | None:
    """Парсит альтернативные названия из секции Alternative Titles."""
    # Ищем span с dark_text содержащий label
    for span in soup.select('div.spaceit_pad span.dark_text'):
        text = _clean(span.get_text())
        if text and text.rstrip(':').strip().lower() == label.lower():
            # Текст после span — это значение
            parent = span.parent
            # Получаем весь текст родителя, убираем label
            full_text = parent.get_text()
            value = full_text.replace(span.get_text(), '', 1).strip()
            value = _clean(value)
            if value:
                return value
    return None


def _parse_information(soup: BeautifulSoup) -> dict:
    """Парсит секцию Information — все поля под заголовком <h2>Information</h2>."""
    info = {}
    h2_info = soup.find('h2', string='Information')
    if not h2_info:
        return info

    # Идём по всем div.spaceit_pad после h2 Information до h2 Statistics
    current = h2_info
    while current:
        current = current.find_next_sibling()
        if current is None:
            break
        if current.name == 'h2':
            break

        # Проверяем, есть ли dark_text span
        dark_span = current.select_one('span.dark_text') if current.name == 'div' else None
        if dark_span:
            label = _clean(dark_span.get_text())
            if label:
                label = label.rstrip(':')
            full_text = current.get_text()
            value = full_text.replace(dark_span.get_text(), '', 1).strip()
            value = _clean(value)

            if not label or not value:
                continue

            # Различные поля
            label_lower = label.lower()

            if label_lower == 'type':
                info['type'] = value
            elif label_lower == 'episodes':
                info['episodes'] = _clean_int(value)
            elif label_lower == 'status':
                info['mal_status'] = value
            elif label_lower == 'aired':
                info['aired'] = value
            elif label_lower == 'premiered':
                info['premiered'] = value
                # Парсим season и year из "Spring 2026"
                m = re.match(r'(\w+)\s+(\d{4})', value)
                if m:
                    info['season'] = m.group(1).lower()
                    info['year'] = int(m.group(2))
            elif label_lower == 'broadcast':
                info['broadcast'] = value
            elif label_lower == 'producers':
                info['producers'] = _parse_links(current)
            elif label_lower == 'licensors':
                info['licensors'] = _parse_links(current) or []
            elif label_lower == 'studios':
                info['studios'] = _parse_links(current)
            elif label_lower == 'source':
                info['source'] = value
            elif label_lower == 'genres':
                info['genres'] = _parse_genre_links(current)
            elif label_lower == 'themes':
                info['themes'] = _parse_genre_links(current)
            elif label_lower == 'demographic':
                info['demographic'] = _parse_genre_links(current)
            elif label_lower == 'duration':
                info['duration'] = value
            elif label_lower == 'rating':
                info['rating'] = value

    return info


def _parse_links(element) -> list[str]:
    """Извлекает тексты всех <a> ссылок внутри элемента."""
    links = element.select('a')
    result = []
    for a in links:
        text = _clean(a.get_text())
        if text and text not in result:
            # Пропускаем служебные ссылки
            if 'add some' not in text.lower():
                result.append(text)
    return result if result else []


def _parse_genre_links(element) -> list[str]:
    """Извлекает названия жанров/тем/демографий из ссылок."""
    links = element.select('a')
    result = []
    for a in links:
        text = _clean(a.get_text())
        if text and text not in result:
            result.append(text)
    return result if result else []


def _parse_statistics(soup: BeautifulSoup) -> dict:
    """Парсит секцию Statistics."""
    stats = {}

    # Score — берём из itemprop, если есть (надёжнее)
    rating_div = soup.select_one('div[itemprop="aggregateRating"]')
    if rating_div:
        rv = rating_div.select_one('span[itemprop="ratingValue"]')
        rc = rating_div.select_one('span[itemprop="ratingCount"]')
        if rv:
            stats['score'] = _clean_float(rv.get_text())
        if rc:
            stats['scored_by'] = _clean_int(rc.get_text())

    h2_stats = soup.find('h2', string='Statistics')
    if not h2_stats:
        return stats

    current = h2_stats
    while current:
        current = current.find_next_sibling()
        if current is None:
            break
        if current.name == 'h2':
            break

        dark_span = current.select_one('span.dark_text') if current.name == 'div' else None
        if dark_span:
            label = _clean(dark_span.get_text())
            if label:
                label = label.rstrip(':')

            # Удаляем <sup> теги, чтобы не мешали
            for sup in current.find_all('sup'):
                sup.decompose()

            full_text = current.get_text()
            value = full_text.replace(dark_span.get_text(), '', 1).strip()
            value = _clean(value)

            if not label or not value:
                continue

            label_lower = label.lower()

            if label_lower == 'score' and 'score' not in stats:
                # Fallback, если itemprop не сработал
                # "N/A" — аниме не вышло, оценок нет
                if value and value != 'N/A':
                    score_match = re.search(r'([\d.]+)', value)
                    if score_match:
                        try:
                            stats['score'] = float(score_match.group(1))
                        except ValueError:
                            pass
                users_match = re.search(r'scored by ([\d,]+) users', value)
                if users_match and 'scored_by' not in stats:
                    stats['scored_by'] = _clean_int(users_match.group(1))
            elif label_lower == 'ranked':
                # "N/A" — нет ранга (не вышло)
                if value and value != 'N/A':
                    ranked_match = re.search(r'#(\d+)', value)
                    if ranked_match:
                        stats['ranked'] = int(ranked_match.group(1))
            elif label_lower == 'popularity':
                pop_match = re.search(r'#(\d+)', value)
                if pop_match:
                    stats['popularity'] = int(pop_match.group(1))
            elif label_lower == 'members':
                stats['members'] = _clean_int(value)
            elif label_lower == 'favorites':
                stats['favorites'] = _clean_int(value)

    return stats


def _parse_related_entries(soup: BeautifulSoup) -> list[dict]:
    """Парсит секцию Related Entries."""
    related = []
    rel_div = soup.select_one('div.related-entries')
    if not rel_div:
        return related

    entries = rel_div.select('div.entry')
    for entry in entries:
        # Пропускаем пустые entries
        if not entry.select_one('a[href]'):
            continue

        link = entry.select_one('div.image a[href]')
        if not link:
            link = entry.select_one('a[href]')
        if not link:
            continue

        url = link.get('href', '')
        mal_id = _extract_id_from_url(url)
        mal_type = _extract_type_from_url(url)

        relation_div = entry.select_one('div.relation')
        relation_text = _clean(relation_div.get_text()) if relation_div else None

        title_div = entry.select_one('div.title a')
        title = _clean(title_div.get_text()) if title_div else None

        if mal_id and relation_text:
            # Разделяем relation и type: "Adaptation (Manga)" -> "Adaptation", "Manga"
            rel_match = re.match(r'(\w+(?:\s\w+)?)\s*\((\w+)\)', relation_text)
            if rel_match:
                relation = rel_match.group(1).strip()
                entry_type = rel_match.group(2).strip()
            else:
                relation = relation_text
                entry_type = mal_type or None

            related.append({
                'mal_id': mal_id,
                'mal_type': mal_type,
                'relation': relation,
                'type': entry_type,
                'title': title,
                'url': url,
            })

    return related


def _parse_external_links(soup: BeautifulSoup, section_name: str) -> list[dict]:
    """Парсит секции Available At и Resources."""
    links_list = []
    h2 = soup.find('h2', string=section_name)
    if not h2:
        return links_list

    container = h2.find_next_sibling('div', class_='external_links')
    if not container:
        return links_list

    for a in container.select('a.link'):
        url = a.get('href', '')
        caption_div = a.select_one('div.caption')
        caption = _clean(caption_div.get_text()) if caption_div else None
        if url:
            links_list.append({
                'url': url,
                'name': caption,
            })

    return links_list


def _parse_streaming_platforms(soup: BeautifulSoup) -> list[dict]:
    """Парсит секцию Streaming Platforms."""
    platforms = []
    h2 = soup.find('h2', string='Streaming Platforms')
    if not h2:
        return platforms

    container = h2.find_next_sibling('div', class_='broadcasts')
    if not container:
        return platforms

    for a in container.select('a.broadcast-item'):
        url = a.get('href', '')
        title = a.get('title', '')
        available = a.get('data-available', '') == '1'
        caption_div = a.select_one('div.caption')
        caption = _clean(caption_div.get_text()) if caption_div else title
        if url:
            platforms.append({
                'name': caption,
                'url': url,
                'available': available,
            })

    return platforms


# ---------------------------------------------------------------------------
# Парсер страницы characters & staff
# ---------------------------------------------------------------------------

def parse_characters_page(html: str) -> dict:
    """Парсит HTML страницы /anime/{id}/{title}/characters.

    Возвращает dict с двумя ключами:
      - 'characters': [{mal_id, name, url, role, voice_actors: [{mal_id, name, url, language}]}]
      - 'staff': [{mal_id, name, url, roles: [str]}]
    """
    soup = BeautifulSoup(html, 'html.parser')

    return {
        'characters': _parse_characters(soup),
        'staff': _parse_staff(soup),
    }


def _parse_characters(soup: BeautifulSoup) -> list[dict]:
    """Парсит секцию Characters & Voice Actors.

    Работает с обеими страницами:
      - Основная страница аниме: h3.h3_characters_voice_actors
      - Отдельная страница /characters: h3.h3_character_name (внутри <a>)
    """
    characters = []

    # Ищем все h3 с именами персонажей (оба варианта)
    h3s = soup.select('h3.h3_characters_voice_actors, h3.h3_character_name')

    for h3 in h3s:
        # Ссылка может быть внутри h3 или h3 внутри a
        link = h3.find('a')
        if not link:
            # h3 itself might be inside an <a>
            link = h3.find_parent('a')
        if not link:
            continue

        url = link.get('href', '')
        mal_id = _extract_id_from_url(url)
        name = _clean(link.get_text())

        if not mal_id or not name:
            continue

        # Роль (Main/Supporting) — ищем в ближайшем родительском td
        parent_td = h3.find_parent('td')
        role = None
        if parent_td:
            # На основной странице: <small>Main</small>
            role_small = parent_td.select_one('small')
            if role_small:
                role = _clean(role_small.get_text())
            if not role:
                # На странице /characters: div.spaceit_pad без dark_text,
                # второй div после имени содержит роль
                divs = parent_td.select('div.spaceit_pad')
                for d in divs:
                    text = _clean(d.get_text())
                    if text in ('Main', 'Supporting'):
                        role = text
                        break

        # Voice actors — в соседнем td справа
        va_list = []
        va_td = None
        if parent_td:
            tr = parent_td.find_parent('tr')
            if tr:
                tds = tr.find_all('td')
                for td in tds:
                    if td is parent_td:
                        continue
                    if td.select_one('a[href*="/people/"]'):
                        va_td = td
                        break

        if va_td:
            # На отдельной странице /characters: VA в tr.js-anime-character-va-lang
            va_rows = va_td.select('tr.js-anime-character-va-lang')
            if not va_rows:
                # На основной странице — один tr с VA
                va_rows = va_td.select('tr')

            for va_row in va_rows:
                va_links = va_row.select('a[href*="/people/"]')
                for va_link in va_links:
                    va_url = va_link.get('href', '')
                    va_id = _extract_id_from_url(va_url)
                    va_name = _clean(va_link.get_text())
                    if not va_id or not va_name:
                        continue

                    # Язык VA
                    lang = None
                    # На /characters: div.js-anime-character-language
                    lang_div = va_row.select_one('div.js-anime-character-language')
                    if lang_div:
                        lang = _clean(lang_div.get_text())
                    if not lang:
                        # На основной странице: <small>Japanese</small>
                        small = va_row.find('small')
                        if small:
                            lang = _clean(small.get_text())

                    # Не дублируем VA
                    if not any(v['mal_id'] == va_id for v in va_list):
                        va_list.append({
                            'mal_id': va_id,
                            'name': va_name,
                            'url': va_url,
                            'language': lang,
                        })

        characters.append({
            'mal_id': mal_id,
            'name': name,
            'url': url,
            'role': role,
            'voice_actors': va_list,
        })

    return characters


def _parse_staff(soup: BeautifulSoup) -> list[dict]:
    """Парсит секцию Staff.

    Работает с обеими страницами (основная и /characters).
    На обеих: h2 'Staff', затем таблицы с ссылками на /people/.
    """
    staff = []

    # Ищем h2 Staff — может быть <h2>Staff</h2> или <h2 class="h2_overwrite">Staff</h2>
    h2_staff = None
    for h2 in soup.find_all('h2'):
        if h2.get_text().strip() == 'Staff':
            h2_staff = h2
            break

    if not h2_staff:
        return staff

    # После h2 Staff ищем все таблицы с ссылками на /people/
    # Ограничиваем: идём до следующего h2 (Opening Theme, Reviews, etc.)
    staff_tables = []

    current = h2_staff
    while True:
        current = current.find_next('table')
        if current is None:
            break
        # Проверяем, не перешли ли мы в другой h2-секцию
        prev_h2 = current.find_previous('h2')
        if prev_h2 and prev_h2 is not h2_staff:
            break
        # Проверяем, что в таблице есть ссылка на /people/
        if current.select_one('a[href*="/people/"]'):
            staff_tables.append(current)

    for table in staff_tables:
        for a in table.select('a[href*="/people/"]'):
            _process_staff_link(a, staff)

    return staff


def _process_staff_link(a, staff: list[dict]):
    """Обрабатывает одну ссылку на человека из секции Staff."""
    url = a.get('href', '')
    mal_id = _extract_id_from_url(url)
    name = _clean(a.get_text())
    if not mal_id or not name:
        # Ссылки без текста (картинки) — пропускаем,
        # имя будет в следующей ссылке с тем же URL
        return

    # Роль — в ближайшем <small> после ссылки
    parent_td = a.find_parent('td')
    roles = []
    if parent_td:
        small = parent_td.select_one('small')
        if small:
            role_text = _clean(small.get_text())
            if role_text:
                roles = [r.strip() for r in role_text.split(',') if r.strip()]

    # Проверяем, не дублируется ли человек (по mal_id)
    existing = next((s for s in staff if s['mal_id'] == mal_id), None)
    if existing:
        for r in roles:
            if r not in existing['roles']:
                existing['roles'].append(r)
    else:
        staff.append({
            'mal_id': mal_id,
            'name': name,
            'url': url,
            'roles': roles,
        })


# ---------------------------------------------------------------------------
# Парсер страницы сезона
# ---------------------------------------------------------------------------

def parse_season_page(html: str) -> list[dict]:
    """Парсит HTML страницы сезона (например /anime/season/2026/summer).

    Возвращает список [{mal_id, title, url}, ...] — все тайтлы сезона.
    """
    soup = BeautifulSoup(html, 'html.parser')
    titles = []

    # Каждый тайтл в сезоне — это div с классом seasonal-anime js-seasonal-anime
    anime_divs = soup.select('div.js-seasonal-anime')

    for div in anime_divs:
        # Ищем ссылку на аниме
        title_link = div.select_one('a.link-title') or div.select_one('h2 a[href*="/anime/"]')
        if not title_link:
            # Fallback — любая ссылка на /anime/
            title_link = div.select_one('a[href*="/anime/"]')
        if not title_link:
            continue

        url = title_link.get('href', '')
        mal_id = _extract_id_from_url(url)
        title = _clean(title_link.get_text())

        if mal_id and title:
            titles.append({
                'mal_id': mal_id,
                'title': title,
                'url': url,
            })

    return titles