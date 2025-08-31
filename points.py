# points.py
from typing import Dict, List, Tuple, Optional

# БАЗОВЫЙ СПРАВОЧНИК
CITIES: Dict[str, Dict] = {
    "vilnius": {
        "id": "78",
        "canonical": "Vilnius",
        "aliases": ["vilnius", "вильнюс", "vilnyus", "wilno", "vilnius lt", "lt vilnius"],
    },
    "minsk": {
        "id": "2",
        "canonical": "Minsk",
        "aliases": ["minsk", "минск", "mensk", "by minsk"],
    },
    "vilnius_airport": {
        "id": "2376",
        "canonical": "Vilnius Airport",
        "aliases": [
            "vilnius airport", "аэропорт вильнюс", "ltu", "vno",
            "vilnius ltu", "аэропорт vilnius"
        ],
    },
    # добавляй сюда новые точки…
}

# === индексы ===
_ALIAS_INDEX: Dict[str, Tuple[str, str]] = {}        # alias -> (id, key)
_ID_INDEX: Dict[str, str] = {}                       # id -> key

for key, entry in CITIES.items():
    _ID_INDEX[entry["id"]] = key
    for a in entry["aliases"]:
        _ALIAS_INDEX[a.strip().lower()] = (entry["id"], key)
    # сам канон тоже считаем алиасом
    _ALIAS_INDEX[entry["canonical"].strip().lower()] = (entry["id"], key)

def normalize(q: str) -> str:
    return q.strip().lower()

def resolve_city(token: str) -> Optional[Tuple[str, str]]:
    """
    По имени/алиасу: возвращает (city_id, canonical_name) или None.
    Не принимает чистый ID — для этого есть resolve_city_or_id().
    """
    n = normalize(token)
    if n in _ALIAS_INDEX:
        cid, key = _ALIAS_INDEX[n]
        return cid, CITIES[key]["canonical"]
    # partial match (если уникально)
    matches: List[Tuple[str, str]] = []
    for alias, (cid, key) in _ALIAS_INDEX.items():
        if n in alias:
            matches.append((cid, key))
    # uniq, сохранив порядок
    seen = set()
    uniq = []
    for cid, key in matches:
        if (cid, key) not in seen:
            seen.add((cid, key))
            uniq.append((cid, key))
    if len(uniq) == 1:
        cid, key = uniq[0]
        return cid, CITIES[key]["canonical"]
    return None

def resolve_city_or_id(token: str) -> Optional[Tuple[str, str]]:
    """
    Универсальный резолвер: ID или алиас/имя.
    Возвращает (city_id, canonical_name) или None.
    """
    t = token.strip()
    if t.isdigit():
        key = _ID_INDEX.get(t)
        if key:
            return t, CITIES[key]["canonical"]
        return None  # неизвестный ID
    return resolve_city(t)

def canonical_by_id(city_id: str) -> Optional[str]:
    """Вернёт каноническое имя по ID, либо None если не нашли."""
    key = _ID_INDEX.get(city_id.strip())
    return CITIES[key]["canonical"] if key else None

def list_points() -> List[Tuple[str, str]]:
    """Список (id, CanonicalName) для отображения."""
    return [(v["id"], v["canonical"]) for v in CITIES.values()]

def search_points(q: str) -> List[Tuple[str, str]]:
    """Поиск по алиасам/каноничным/ключам. Возвращает (id, CanonicalName)."""
    n = normalize(q)
    found_keys = set()
    for key, entry in CITIES.items():
        if n in key or n in entry["canonical"].lower():
            found_keys.add(key)
        else:
            for a in entry["aliases"]:
                if n in a.lower():
                    found_keys.add(key)
                    break
    return [(CITIES[k]["id"], CITIES[k]["canonical"]) for k in found_keys]
