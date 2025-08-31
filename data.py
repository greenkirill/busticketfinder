# data.py
import json
import os
import time
from dataclasses import dataclass, asdict
from typing import List, Optional, Dict, Any
from threading import RLock
from tempfile import NamedTemporaryFile

@dataclass
class Subscription:
    id: int
    user_id: int
    city_from_id: str
    city_to_id: str
    from_name: str
    to_name: str
    date_str: str          # "DD.MM.YYYY"
    dep_from_hhmm: str     # "HH:MM"
    dep_to_hhmm: str       # "HH:MM"
    last_hash: str
    created_at: int
    updated_at: int

def _now() -> int:
    return int(time.time())

class Storage:
    """
    Простейшее хранилище на JSON-файле.
    Структура файла:
    {
      "last_id": 12,
      "subs": [ {Subscription...}, ... ],
      "meta": { "last_check_ts": "169...", "checks_count": "42" }
    }
    """
    def __init__(self, json_path: str):
        self.path = json_path
        self._lock = RLock()
        self._data: Dict[str, Any] = {"last_id": 0, "subs": [], "meta": {}}
        self._load()

    # ---------- файловые утилиты ----------

    def _load(self) -> None:
        with self._lock:
            if not os.path.exists(self.path):
                self._flush()
                return
            try:
                with open(self.path, "r", encoding="utf-8") as f:
                    self._data = json.load(f)
                # sanity
                self._data.setdefault("last_id", 0)
                self._data.setdefault("subs", [])
                self._data.setdefault("meta", {})
            except Exception:
                # если файл битый — переименуем в резерв и начнём с нуля
                try:
                    os.replace(self.path, self.path + ".corrupted")
                except Exception:
                    pass
                self._data = {"last_id": 0, "subs": [], "meta": {}}
                self._flush()

    def _flush(self) -> None:
        with self._lock:
            os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
            # атомарная запись: во временный файл, потом rename
            with NamedTemporaryFile("w", delete=False, encoding="utf-8", dir=os.path.dirname(self.path) or ".") as tmp:
                json.dump(self._data, tmp, ensure_ascii=False, indent=2)
                tmp_path = tmp.name
            os.replace(tmp_path, self.path)

    # ---------- helpers ----------

    def _next_id(self) -> int:
        self._data["last_id"] += 1
        return self._data["last_id"]

    def _subs_list(self) -> List[Subscription]:
        return [Subscription(**s) for s in self._data.get("subs", [])]

    def _save_subs(self, subs: List[Subscription]) -> None:
        self._data["subs"] = [asdict(s) for s in subs]
        self._flush()

    # ---------- public API (совместим с прежним) ----------

    def add_sub(
        self,
        user_id: int,
        city_from_id: str,
        city_to_id: str,
        from_name: str,
        to_name: str,
        date_str: str,
        dep_from_hhmm: str,
        dep_to_hhmm: str
    ) -> int:
        with self._lock:
            sid = self._next_id()
            ts = _now()
            s = Subscription(
                id=sid,
                user_id=user_id,
                city_from_id=city_from_id,
                city_to_id=city_to_id,
                from_name=from_name,
                to_name=to_name,
                date_str=date_str,
                dep_from_hhmm=dep_from_hhmm,
                dep_to_hhmm=dep_to_hhmm,
                last_hash="",
                created_at=ts,
                updated_at=ts
            )
            subs = self._subs_list()
            subs.append(s)
            self._save_subs(subs)
            return sid

    def list_subs(self, user_id: int) -> List[Subscription]:
        with self._lock:
            return [s for s in self._subs_list() if s.user_id == user_id]

    def list_all_subs(self) -> List[Subscription]:
        with self._lock:
            return self._subs_list()

    def del_sub(self, user_id: int, sub_id: int) -> bool:
        with self._lock:
            subs = self._subs_list()
            before = len(subs)
            subs = [s for s in subs if not (s.user_id == user_id and s.id == sub_id)]
            changed = len(subs) != before
            if changed:
                self._save_subs(subs)
            return changed

    def del_all_subs(self, user_id: int) -> int:
        with self._lock:
            subs = self._subs_list()
            kept = [s for s in subs if s.user_id != user_id]
            removed = len(subs) - len(kept)
            if removed:
                self._save_subs(kept)
            return removed

    def update_last_hash(self, sub_id: int, new_hash: str) -> None:
        with self._lock:
            subs = self._subs_list()
            for s in subs:
                if s.id == sub_id:
                    s.last_hash = new_hash
                    s.updated_at = _now()
                    break
            self._save_subs(subs)

    # --- meta (key/value) ---

    def get_meta(self, key: str) -> Optional[str]:
        with self._lock:
            return self._data.get("meta", {}).get(key)

    def set_meta(self, key: str, value: str) -> None:
        with self._lock:
            self._data.setdefault("meta", {})[key] = value
            self._flush()
