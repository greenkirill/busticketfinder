import asyncio
from datetime import datetime
import time
from typing import Any, Dict, List, Tuple
from aiogram import Bot, Dispatcher, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message
import re

from config import CHECK_EVERY_SEC, REPORT_EVERY_SEC, TELEGRAM_BOT_TOKEN, SUBS_JSON
from data import Storage
from infobus_client import InfobusClient
from points import list_points, resolve_city_or_id, search_points

client = InfobusClient()
storage = Storage(SUBS_JSON)
router = Router()

async def checker_loop(bot: Bot):
    while True:
        try:
            subs = storage.list_all_subs()
            checks_count = int(storage.get_meta("checks_count") or "0") + 1

            for s in subs:
                try:
                    # 1) получаем маршруты
                    routes_json: Dict[str, Any] = client.get_routes(
                        city_from_id=s.city_from_id,
                        city_to_id=s.city_to_id,
                        from_name=s.from_name,
                        to_name=s.to_name,
                        date_from=s.date_str,
                        screen_width=2560,
                        screen_height=1305,
                    )
                    # 2) форматируем пары времен
                    times = client.extract_times(routes_json)

                    # есть ли в диапазоне вообще что-то?
                    matches = [t for t in times if in_range(t["depart"], s.dep_from_hhmm, s.dep_to_hhmm)]
                    has_matches = len(matches) > 0

                    # 3) считаем хэш по диапазону (для антидублей)
                    new_hash = hash_times_in_range(times, s.dep_from_hhmm, s.dep_to_hhmm)

                    # 4) периодический отчёт: раз в REPORT_EVERY_SEC, если есть что показать
                    now_ts = int(time.time())
                    last_report_ts = int(storage.get_meta(f"sub:{s.id}:last_report_ts") or "0")
                    must_periodic_report = has_matches and (now_ts - last_report_ts >= REPORT_EVERY_SEC)

                    # 5) отправляем, если: (А) появились изменения, или (Б) пора периодически отчитаться
                    is_change = bool(new_hash) and (new_hash != s.last_hash)
                    should_send = is_change or must_periodic_report

                    if should_send and has_matches:
                        header = "⚡️ Обновление" if is_change else "⏱ Периодический отчёт"
                        lines = [
                            f"{header} по подписке #{s.id}:",
                            f"{s.date_str} {s.from_name}({s.city_from_id}) → {s.to_name}({s.city_to_id})",
                            f"диапазон отправления {s.dep_from_hhmm}–{s.dep_to_hhmm}",
                            "",
                        ]
                        for t in matches:
                            lines.append(f"• {t['depart']} → {t['arrive']}  (€{t['price_eur']}, ⭐ {t['rating']})")

                        await bot.send_message(chat_id=s.user_id, text="\n".join(lines))

                        # фиксируем состояние и время последнего отчёта
                        if new_hash:
                            storage.update_last_hash(s.id, new_hash)
                        storage.set_meta(f"sub:{s.id}:last_report_ts", str(now_ts))

                except Exception as e:
                    # Локальная ошибка по конкретной подписке — логируем и идём дальше
                    print(f"[checker] sub {s.id} error: {e}")

            storage.set_meta("last_check_ts", str(int(time.time())))
            storage.set_meta("checks_count", str(checks_count))

        except Exception as e:
            print(f"[checker] loop error: {e}")

        await asyncio.sleep(CHECK_EVERY_SEC)


def hhmm_to_int(hhmm: str) -> int:
    m = re.match(r"^(\d{1,2}):(\d{2})$", hhmm)
    if not m:
        return 0
    return int(m.group(1)) * 100 + int(m.group(2))

def in_range(hhmm: str, start_hhmm: str, end_hhmm: str) -> bool:
    """Поддерживает диапазон через полночь (если end < start)."""
    v = hhmm_to_int(hhmm)
    a = hhmm_to_int(start_hhmm)
    b = hhmm_to_int(end_hhmm)
    if a <= b:
        return a <= v <= b
    else:
        return v >= a or v <= b

def hash_times_in_range(times: List[Dict[str, str]], dep_from: str, dep_to: str) -> str:
    picked = [f"{t['depart']}->{t['arrive']}" for t in times if in_range(t['depart'], dep_from, dep_to)]
    return "|".join(picked)

def format_last_results(last_hash: str) -> str:
    """
    Превращает last_hash вида 'HH:MM->HH:MM|HH:MM->HH:MM|...' в читабельный список.
    Если пусто — возвращает плейсхолдер.
    """
    if not last_hash:
        return "— нет сохранённых результатов (ещё не было подходящих рейсов)"
    parts = [p for p in last_hash.split("|") if p.strip()]
    if not parts:
        return "— нет сохранённых результатов (ещё не было подходящих рейсов)"
    lines = []
    for p in parts:
        if "->" in p:
            dep, arr = p.split("->", 1)
            lines.append(f"• {dep} → {arr}")
    return "\n".join(lines) if lines else "— нет сохранённых результатов"

HELP = (
    "Команды:\n"
    "/subscribe <date> <from> <to> <fromHH:MM> <toHH:MM>\n"
    "  где <from>/<to> — ЛИБО id (например 78/2), ЛИБО имя точки (Vilnius/Minsk)\n"
    "  пример: /subscribe 01.09.2025 78 2 20:00 23:00\n"
    "          /subscribe 01.09.2025 Vilnius Minsk 20:00 23:00\n"
    "/subs — список подписок\n"
    "/status — когда был последний сниф и какие были сохранённые результаты\n"
    "/unsubscribe <id> — удалить подписку\n"
    "/points [query] — показать доступные точки (или поиск)\n"
)

def ensure_city(token: str) -> Tuple[str, str]:
    """
    Возвращает (city_id, canonical_name).
    Поддерживает чистый ID и алиасы/имена.
    """
    res = resolve_city_or_id(token)
    if not res:
        raise ValueError(f"Не нашёл такую точку: {token}")
    return res  # (id, canonical)

@router.message(Command("start"))
async def start_cmd(m: Message):
    await m.answer("Привет! Я бот для слежения за билетами.\n" + HELP)

@router.message(Command("points"))
async def points_cmd(m: Message, command: CommandObject):
    q = (command.args or "").strip()
    rows = search_points(q) if q else list_points()
    if not rows:
        await m.answer("Ничего не нашёл. Попробуй /points без параметров.")
        return
    rows = sorted(rows, key=lambda x: (x[1].lower(), x[0]))
    text = "Доступные точки:\n" + "\n".join([f"• {name} — id {cid}" for cid, name in rows])
    await m.answer(text)

@router.message(Command("subscribe"))
async def subscribe_cmd(m: Message, command: CommandObject):
    """
    Варианты:
      /subscribe 01.09.2025 78 2 20:00 23:00
      /subscribe 01.09.2025 Vilnius Minsk 20:00 23:00
    """
    if not command.args:
        await m.answer("Формат:\n" + HELP)
        return

    parts = command.args.split()
    if len(parts) < 5:
        await m.answer("Мало аргументов. Пример: /subscribe 01.09.2025 78 2 20:00 23:00")
        return

    date_str = parts[0]
    from_token = parts[1]
    to_token = parts[2]
    dep_from_hhmm = parts[-2]
    dep_to_hhmm = parts[-1]

    # резолвим точки в id
    try:
        city_from_id, from_canonical = ensure_city(from_token)
        city_to_id, to_canonical = ensure_city(to_token)
    except ValueError as e:
        await m.answer(str(e) + "\nПодсказка: /points для списка точек.")
        return
    
    if m.from_user is None:
        await m.answer("Не могу определить твоего пользователя.")
        return
    

    
    sid = storage.add_sub(
        user_id=m.from_user.id,
        city_from_id=city_from_id,
        city_to_id=city_to_id,
        from_name=from_canonical,
        to_name=to_canonical,
        date_str=date_str,
        dep_from_hhmm=dep_from_hhmm,
        dep_to_hhmm=dep_to_hhmm,
    )

    await m.answer(
        f"✅ Подписка #{sid} добавлена:\n"
        f"{date_str} {from_canonical}({city_from_id}) → {to_canonical({city_to_id}) if False else to_canonical}({city_to_id}) "
        f"в {dep_from_hhmm}–{dep_to_hhmm}"
    )

@router.message(Command("unsubscribe"))
async def unsubscribe_cmd(m: Message, command: CommandObject):
    if not command.args:
        await m.answer("Укажи ID: /unsubscribe <id>")
        return
    try:
        sid = int(command.args.strip())
    except ValueError:
        await m.answer("ID должен быть числом.")
        return
    if m.from_user is None:
        await m.answer("Не могу определить твоего пользователя.")
        return
    
    ok = storage.del_sub(m.from_user.id, sid)
    await m.answer(f"🗑 Подписка #{sid} удалена." if ok else "Не нашёл такую подписку.")

@router.message(Command("subs"))
async def subs_cmd(m: Message):
    items = storage.list_subs(m.from_user.id if m.from_user else 0)
    if not items:
        await m.answer("Подписок нет.")
        return
    lines = []
    for s in items:
        lines.append(
            f"#{s.id} {s.date_str} {s.from_name}({s.city_from_id}) → {s.to_name}({s.city_to_id}) "
            f"в {s.dep_from_hhmm}–{s.dep_to_hhmm}"
        )
    await m.answer("\n".join(lines))

@router.message(Command("status"))
async def status_cmd(m: Message):
    # глобальный статус проверок
    last_ts = storage.get_meta("last_check_ts")
    checks_count = storage.get_meta("checks_count") or "0"
    if last_ts:
        dt = datetime.fromtimestamp(int(last_ts)).strftime("%Y-%m-%d %H:%M:%S")
        header = f"Последняя проверка: {dt}\nВсего проверок: {checks_count}\n"
    else:
        header = "Пока ни одной проверки не было.\n"

    # по подпискам текущего пользователя — какие были сохранённые результаты
    items = storage.list_subs(m.from_user.id if m.from_user else 0)
    if not items:
        await m.answer(header + "\nПодписок нет.")
        return

    chunks = [header, "Последние сохранённые результаты по твоим подпискам:"]
    for s in items:
        chunks.append(
            f"\n#{s.id} {s.date_str} {s.from_name}({s.city_from_id}) → {s.to_name}({s.city_to_id}) "
            f"в {s.dep_from_hhmm}–{s.dep_to_hhmm}\n{format_last_results(s.last_hash)}"
        )

    # Telegram лимит ~4096 символов на сообщение; если переживаешь — можно порезать на несколько сообщений
    await m.answer("\n".join(chunks))

async def main():
    bot = Bot(TELEGRAM_BOT_TOKEN)
    dp = Dispatcher()
    dp.include_router(router)

    asyncio.create_task(checker_loop(bot))
    
    print("Bot started.")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
