import re
import json
import time
import base64
import requests
from typing import Optional, Tuple, Dict, Any, List


class InfobusClient:
    """
    Лёгкий клиент к infobus.eu:
      - держит одну сессию (cookies)
      - хранит и обновляет token + PHPSESSID_cf
      - делает POST get_routes с ретраями
      - парсит времена отправления/прибытия
    """

    TOKEN_PATTERNS = [
        re.compile(r"""var\s+token\s*=\s*'([^']*)'"""),
        re.compile(r'''var\s+token\s*=\s*"([^"]*)"'''),
    ]

    def __init__(
        self,
        base_url: str = "https://infobus.eu",
        user_agent: Optional[str] = "Mozilla/5.0",
        timeout: float = 20.0,
        max_retries: int = 4,
        backoff_base_seconds: float = 1.0,
        clock_skew_sec: int = 60,
    ):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.max_retries = max_retries
        self.backoff_base_seconds = backoff_base_seconds
        self.clock_skew_sec = clock_skew_sec

        self.s = requests.Session()
        if user_agent:
            self.s.headers.update({"User-Agent": user_agent})

        self.phpsessid_cf: Optional[str] = None
        self.token: Optional[str] = None
        self.token_exp: Optional[int] = None

    # ---------- публичные методы ----------

    def refresh_session_and_token(
        self,
        city_from_id: str,
        city_to_id: str,
        date_str: str,
    ) -> Tuple[Optional[str], Optional[str]]:
        """
        GET /en/<from>/<to>/<date>?cookies_cleared=1
        -> обновляет PHPSESSID_cf и token. Возвращает (phpsessid_cf, token).
        """
        url = f"{self.base_url}/en/{city_from_id}/{city_to_id}/{date_str}?cookies_cleared=1"

        r = self._request_with_retries("GET", url)
        html = r.text

        self.phpsessid_cf = self._get_cookie_case_insensitive("PHPSESSID_cf")
        self.token = self._extract_token_from_html(html)
        self.token_exp = self._parse_jwt_exp(self.token) if self.token else None

        return self.phpsessid_cf, self.token

    def get_routes(
        self,
        city_from_id: str,
        city_to_id: str,
        from_name: str,
        to_name: str,
        date_from: str,
        screen_width: int = 1920,
        screen_height: int = 1080,
    ) -> Dict[str, Any]:
        """
        Делает POST на /en/script (Function=get_routes) и возвращает JSON dict.
        Сам следит за тем, чтобы токен и сессия были свежими.
        """
        # если токена/сессии нет или они протухли → обновляем
        if not self._auth_is_fresh():
            self.refresh_session_and_token(city_from_id, city_to_id, date_from)

        if not self.token or not self.phpsessid_cf:
            raise RuntimeError("Auth is missing (token or PHPSESSID_cf).")

        url = f"{self.base_url}/en/script"

        headers = {
            "accept": "application/json, text/javascript, */*; q=0.01",
            "authorization": f"Bearer {self.token}",
            "content-type": "application/x-www-form-urlencoded; charset=UTF-8",
            "cookie": (
                f"cf_cookies_cleared=1; "
                f"PHPSESSID_cf={self.phpsessid_cf}; "
                f"lang=en; "
                f"search-items=bus%7C{city_from_id}%7C{city_to_id}"
            ),
            "origin": self.base_url,
            "referer": f"{self.base_url}/{city_from_id}/{city_to_id}/{date_from}",
            "x-requested-with": "XMLHttpRequest",
        }

        payload = {
            "transport_type": "all",
            "city_from_id": city_from_id,
            "city_to_id": city_to_id,
            "dateFrom": date_from,
            "dateTo": "",
            "Function": "get_routes",
            "period": "0",
            "route_id": "",
            "filter_time_from": "",
            "from_name": from_name,
            "to_name": to_name,
            "screen_width": str(screen_width),
            "screen_height": str(screen_height),
            "ws": "0",
        }

        r = self._request_with_retries("POST", url, headers=headers, data=payload)

        if r.status_code in (401, 403):
            # если сервер сказал "не авторизован" → обновляем и пробуем ещё раз
            self.refresh_session_and_token(city_from_id, city_to_id, date_from)
            headers["authorization"] = f"Bearer {self.token}"
            headers["cookie"] = (
                f"cf_cookies_cleared=1; "
                f"PHPSESSID_cf={self.phpsessid_cf}; "
                f"lang=en; "
                f"search-items=bus%7C{city_from_id}%7C{city_to_id}"
            )
            r = self._request_with_retries("POST", url, headers=headers, data=payload)

        r.raise_for_status()
        return r.json()

    @staticmethod
    def extract_times(routes_response: Dict[str, Any]) -> List[Dict[str, str]]:
        """
        Берёт JSON из get_routes и возвращает список:
        {depart: 'HH:MM', arrive: 'HH:MM', price_eur: '...', rating: '...'}
        """
        result = []
        if not routes_response or not routes_response.get("status"):
            return result

        for route in routes_response.get("routes", []):
            dep = route.get("ClearDepTime", "")
            arr = route.get("ClearArrTime", "")
            dep_f = f"{dep[:2]}:{dep[2:]}" if len(dep) == 4 else dep
            arr_f = f"{arr[:2]}:{arr[2:]}" if len(arr) == 4 else arr
            result.append({
                "depart": dep_f,
                "arrive": arr_f,
                "price_eur": route.get("price"),
                "rating": str(route.get("rating", "")),
            })
        result.sort(key=lambda x: x["depart"])
        return result


    def _request_with_retries(self, method: str, url: str, **kwargs) -> requests.Response:
        attempt = 0
        last_exc = None
        while attempt < self.max_retries:
            try:
                r = self.s.request(method, url, timeout=self.timeout, **kwargs)
                return r
            except (requests.Timeout, requests.ConnectionError) as e:
                last_exc = e
                sleep_for = self.backoff_base_seconds * (2 ** attempt)
                time.sleep(sleep_for)
                attempt += 1
        raise RuntimeError(f"Request failed after {self.max_retries} retries: {last_exc}")

    def _get_cookie_case_insensitive(self, name: str) -> Optional[str]:
        for key in (name, name.lower(), name.upper()):
            if key in self.s.cookies:
                return self.s.cookies.get(key)
        return None

    def _extract_token_from_html(self, html: str) -> Optional[str]:
        for pat in self.TOKEN_PATTERNS:
            m = pat.search(html)
            if m:
                return m.group(1).strip() or None
        return None

    def _auth_is_fresh(self) -> bool:
        if not self.token or not self.phpsessid_cf:
            return False
        if self.token_exp is None:
            return False
        now = int(time.time())
        return now + self.clock_skew_sec < self.token_exp

    @staticmethod
    def _parse_jwt_exp(token: Optional[str]) -> Optional[int]:
        if not token or token.count(".") != 2:
            return None
        try:
            payload_b64 = token.split(".")[1]
            padding = "=" * (-len(payload_b64) % 4)
            decoded = base64.urlsafe_b64decode(payload_b64 + padding).decode("utf-8")
            payload = json.loads(decoded)
            exp = payload.get("exp")
            if isinstance(exp, int):
                return exp
        except Exception:
            return None
        return None


if __name__ == "__main__":
    client = InfobusClient()

    routes_json = client.get_routes(
        city_from_id="78",
        city_to_id="2",
        from_name="Vilnius",
        to_name="Minsk",
        date_from="02.09.2025",
        screen_width=2560,
        screen_height=1305,
    )

    times = client.extract_times(routes_json)
    for t in times:
        print(f"{t['depart']} → {t['arrive']} | €{t['price_eur']} | rating {t['rating']}")
