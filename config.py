import os
from dotenv import load_dotenv

load_dotenv()

TELEGRAM_BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
CHECK_EVERY_SEC: int = int(os.getenv("CHECK_EVERY_SEC", "120"))
SUBS_JSON: str = os.getenv("SUBS_JSON", "./subs.db")
REPORT_EVERY_SEC = int(os.getenv("REPORT_EVERY_SEC", "1800"))

INFOBUS_BASE_URL: str = os.getenv("INFOBUS_BASE_URL", "https://infobus.eu")
INFOBUS_USER_AGENT: str = os.getenv("INFOBUS_USER_AGENT", "Mozilla/5.0")

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
LOG_TO_FILE = os.getenv("LOG_TO_FILE", "0") == "1"
LOG_FILE = os.getenv("LOG_FILE", "./bot.log")

if not TELEGRAM_BOT_TOKEN:
    raise RuntimeError("ENV TELEGRAM_BOT_TOKEN не задан")
