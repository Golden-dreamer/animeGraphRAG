"""raw JSON (Jikan /anime/{id}/full) -> плоский dict нужных нам полей.
Все поля через .get(...) — сайт может не отдать что-то, падать нельзя.
"""


def extract_fields(raw: dict) -> dict | None:
    data = raw.get("data")
    if not data:
        return None

    images = data.get("images") or {}
    jpg = images.get("jpg") or {}

    return {
        "mal_id": data.get("mal_id"),
        "poster_url": jpg.get("large_image_url") or jpg.get("image_url"),
        "title_original": data.get("title"),
        "title_english": data.get("title_english") or data.get("title"),
        "genres": [g["name"] for g in (data.get("genres") or []) if "name" in g],
        "studios": [s["name"] for s in (data.get("studios") or []) if "name" in s],
        "type": data.get("type"),
        "year": data.get("year"),
        "season": data.get("season"),
        "mal_status": data.get("status"),  # 'Currently Airing' / 'Finished Airing' / 'Not yet aired'
    }
