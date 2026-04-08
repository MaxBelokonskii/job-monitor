import asyncio
import logging
import re
import random
import os
import json
from datetime import datetime, date
from telethon import TelegramClient, events
from dotenv import load_dotenv
import atexit

load_dotenv()

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")

# --- Загрузка конфига с кешированием (TTL 30 сек) ---
import time as _time
_config_cache = {}
_config_cache_time = 0
_CONFIG_TTL = 30

def load_config() -> dict:
    global _config_cache, _config_cache_time
    now = _time.time()
    if _config_cache and (now - _config_cache_time) < _CONFIG_TTL:
        return _config_cache.copy()
    defaults = {
        "channels": ["itvacancykz","it_interns","jobfortester","workitkz","qajoboffer","jobforqa"],
        "keywords": ["qa","тестировщик","manual qa","junior","стажер","стажировка","intern","trainee","без опыта"],
        "exclude": ["senior","lead","middle","middle 3+","middle+","5+ лет","6+ лет","3+ года","4+ года"],
        "template": "",
        "delay_min": 60,
        "delay_max": 120,
        "max_per_day": 25,
        "history_limit": 50,
        "file_path": "",
    }
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            try:
                defaults.update(json.load(f))
            except Exception:
                pass
    _config_cache = defaults
    _config_cache_time = now
    return defaults.copy()

# --- API credentials ---
API_ID = os.getenv("TG_API_ID")
API_HASH = os.getenv("TG_API_HASH")

if not API_ID or not API_HASH:
    raise SystemExit("❌ TG_API_ID и TG_API_HASH не найдены в .env файле")

API_ID = int(API_ID)

# --- PID файл ---
PID_FILE = os.path.join(BASE_DIR, "monitor_pid.txt")

with open(PID_FILE, "w") as f:
    f.write(str(os.getpid()))

def remove_pid():
    if os.path.exists(PID_FILE):
        os.remove(PID_FILE)

atexit.register(remove_pid)

# --- Клиент ---
client = TelegramClient(os.path.join(BASE_DIR, "session"), API_ID, API_HASH)

# --- Режим работы из env ---
SAFE_MODE = os.getenv("SAFE_MODE", "true").lower() == "true"
PARSE_HISTORY = os.getenv("PARSE_HISTORY", "false").lower() == "true"

# --- Логирование ---
LOG_DIR = os.path.join(BASE_DIR, "logs")
ARCHIVE_DIR = os.path.join(LOG_DIR, "archive")
os.makedirs(LOG_DIR, exist_ok=True)
os.makedirs(ARCHIVE_DIR, exist_ok=True)

SYSTEM_LOG = os.path.join(LOG_DIR, "tg_system.log")

def rotate_log():
    """Оставляем последние 5000 строк"""
    if not os.path.exists(SYSTEM_LOG):
        return
    with open(SYSTEM_LOG, "r", encoding="utf-8") as f:
        lines = f.readlines()
    if len(lines) > 5000:
        with open(SYSTEM_LOG, "w", encoding="utf-8") as f:
            f.writelines(lines[-5000:])

rotate_log()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.FileHandler(SYSTEM_LOG, encoding="utf-8"),
        logging.StreamHandler()
    ]
)
log = logging.getLogger(__name__)

# --- Состояние ---
sent_users = set()
sent_today = 0
last_run_date = date.today()

ALL_SENT_FILE = os.path.join(BASE_DIR, "all_sent_users.txt")

def get_log_file(for_date=None):
    d = for_date or date.today()
    return os.path.join(LOG_DIR, f"sent_log_{d.strftime('%Y-%m-%d')}.txt")

LOG_FILE = get_log_file()

def load_sent_users():
    """Загружаем из постоянного файла всех кому когда-либо писали"""
    global sent_users
    # Сначала из постоянного файла
    if os.path.exists(ALL_SENT_FILE):
        with open(ALL_SENT_FILE, "r", encoding="utf-8") as f:
            for line in f:
                u = line.strip()
                if u:
                    sent_users.add(u)
    # Также из дневного лога (на случай если all_sent_users устарел)
    if os.path.exists(LOG_FILE):
        with open(LOG_FILE, "r", encoding="utf-8") as f:
            for line in f:
                parts = line.split("|")
                if parts:
                    sent_users.add(parts[0].strip())

def save_sent_user(username: str):
    """Сохраняем в постоянный файл (без дублей)"""
    if username not in sent_users:
        return
    with open(ALL_SENT_FILE, "a", encoding="utf-8") as f:
        f.write(username + "\n")

load_sent_users()

def extract_usernames(text):
    return re.findall(r'@[\w\d_]+', text)

def reset_daily_state():
    global sent_today, last_run_date, sent_users, LOG_FILE
    old_log = get_log_file(last_run_date)
    if os.path.exists(old_log):
        os.rename(old_log, os.path.join(ARCHIVE_DIR, os.path.basename(old_log)))
    sent_today = 0
    last_run_date = date.today()
    LOG_FILE = get_log_file()
    sent_users.clear()
    load_sent_users()
    log.info("[DAILY RESET] Новый день — счётчик сброшен")

# --- Обработка новых сообщений ---
@client.on(events.NewMessage())
async def handler(event):
    global sent_today

    # Загружаем актуальный конфиг при каждом сообщении
    cfg = load_config()
    channels = cfg["channels"]
    keywords = cfg["keywords"]
    exclude = cfg["exclude"]
    max_per_day = cfg["max_per_day"]
    delay_min = cfg["delay_min"]
    delay_max = cfg["delay_max"]
    message_text = cfg.get("template") or ""
    file_path = cfg.get("file_path") or os.path.join(BASE_DIR, "Пак_Виталий_Владимирович.pdf")

    # Проверяем что сообщение из нужного канала
    try:
        chat = await event.get_chat()
        chat_username = getattr(chat, 'username', None)
        if not chat_username or chat_username.lower() not in [c.lower() for c in channels]:
            return
    except Exception:
        return

    if date.today() != last_run_date:
        reset_daily_state()

    if sent_today >= max_per_day:
        return

    text = event.message.message
    if not text:
        return

    text_lower = text.lower()

    if not any(k in text_lower for k in keywords):
        return
    if any(bad in text_lower for bad in exclude):
        return

    usernames = extract_usernames(text)
    if not usernames:
        return

    log.info(f"[ВАКАНСИЯ] {text[:120].strip()}...")

    for username in usernames:
        username = username.strip()

        if username.lower().endswith("bot"):
            log.info(f"[SKIP] {username} — бот")
            continue
        if username in sent_users:
            log.info(f"[SKIP] {username} — уже отправляли")
            continue
        if sent_today >= max_per_day:
            log.warning(f"[LIMIT] Достигнут дневной лимит ({max_per_day})")
            return

        try:
            if SAFE_MODE:
                log.info(f"[SAFE MODE] Найден контакт: {username}")
                continue

            if not message_text:
                log.warning("[WARN] Шаблон сообщения пустой — пропускаем")
                continue

            await client.send_message(username, message_text)

            if file_path and os.path.exists(file_path):
                await client.send_file(username, file_path)

            sent_users.add(username)
            save_sent_user(username)
            sent_today += 1

            with open(LOG_FILE, "a", encoding="utf-8") as f:
                f.write(f"{username} | {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} | {text[:80].strip()}...\n")

            log.info(f"[OK] Отправлено: {username} | Сегодня: {sent_today}/{max_per_day}")

            await asyncio.sleep(random.randint(delay_min, delay_max))

        except Exception as e:
            from telethon.errors import FloodWaitError
            if isinstance(e, FloodWaitError):
                wait = e.seconds + 10
                log.warning(f"[FLOOD] Telegram просит подождать {wait} сек. Пауза...")
                await asyncio.sleep(wait)
            else:
                log.error(f"[ERROR] {username}: {e}")

# --- Парсинг истории ---
async def parse_history(cfg: dict):
    global sent_today
    channels = cfg["channels"]
    keywords = cfg["keywords"]
    exclude = cfg["exclude"]
    max_per_day = cfg["max_per_day"]
    delay_min = cfg["delay_min"]
    delay_max = cfg["delay_max"]
    message_text = cfg.get("template") or ""
    file_path = cfg.get("file_path") or os.path.join(BASE_DIR, "Пак_Виталий_Владимирович.pdf")
    history_limit = int(os.getenv("HISTORY_LIMIT", str(cfg.get("history_limit", 50))))

    log.info(f"[HISTORY] Читаю последние {history_limit} сообщений из каждого канала...")

    for channel in channels:
        try:
            messages = await client.get_messages(channel, limit=history_limit)
            found = 0
            for msg in messages:
                if not msg.text:
                    continue
                text_lower = msg.text.lower()
                if not any(k in text_lower for k in keywords):
                    continue
                if any(bad in text_lower for bad in exclude):
                    continue
                usernames = extract_usernames(msg.text)
                if not usernames:
                    continue
                found += 1
                for username in usernames:
                    username = username.strip()
                    if username.lower().endswith("bot"):
                        continue
                    if username in sent_users:
                        continue
                    if sent_today >= max_per_day:
                        log.warning(f"[HISTORY] Достигнут дневной лимит ({max_per_day}), останавливаем парсинг")
                        return  # полная остановка, не только break
                    try:
                        if SAFE_MODE:
                            log.info(f"[HISTORY][SAFE MODE] Найден: {username} в @{channel}")
                            continue
                        if not message_text:
                            log.warning("[WARN] Шаблон пустой — пропускаем")
                            continue
                        await client.send_message(username, message_text)
                        if file_path and os.path.exists(file_path):
                            await client.send_file(username, file_path)
                        sent_users.add(username)
                        save_sent_user(username)
                        sent_today += 1
                        with open(LOG_FILE, "a", encoding="utf-8") as f:
                            f.write(f"{username} | {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} | [HISTORY] {msg.text[:80].strip()}...\n")
                        log.info(f"[HISTORY][OK] Отправлено: {username} | Сегодня: {sent_today}/{max_per_day}")
                        await asyncio.sleep(random.randint(delay_min, delay_max))
                    except Exception as e:
                        from telethon.errors import FloodWaitError
                        if isinstance(e, FloodWaitError):
                            wait = e.seconds + 10
                            log.warning(f"[FLOOD] Telegram просит подождать {wait} сек. Пауза...")
                            await asyncio.sleep(wait)
                        else:
                            log.error(f"[HISTORY][ERROR] {username}: {e}")
            log.info(f"[HISTORY] @{channel}: найдено {found} подходящих вакансий")
        except Exception as e:
            log.error(f"[HISTORY][ERROR] Канал @{channel}: {e}")

    log.info("[HISTORY] Готово. Перехожу в режим мониторинга...")

# --- Главная функция ---
async def main():
    cfg = load_config()
    log.info(f"[CONFIG] SAFE_MODE = {SAFE_MODE}")
    log.info(f"[CONFIG] PARSE_HISTORY = {PARSE_HISTORY}")
    log.info(f"[CONFIG] MAX_MESSAGES_PER_DAY = {cfg['max_per_day']}")
    log.info(f"[CONFIG] DELAY = {cfg['delay_min']}-{cfg['delay_max']}s")
    log.info(f"[CONFIG] Каналы: {', '.join(cfg['channels'])}")
    log.info(f"[CONFIG] Шаблон: {'задан' if cfg.get('template') else '⚠️ НЕ ЗАДАН'}")
    log.info("-" * 40)

    while True:
        try:
            await client.start()
            if PARSE_HISTORY:
                await parse_history(load_config())
            log.info("[START] Скрипт запущен, мониторинг активен...")
            await client.run_until_disconnected()
        except Exception as e:
            log.error(f"[ERROR] {e}. Перезапуск через 60 секунд...")
            await asyncio.sleep(60)

if __name__ == "__main__":
    asyncio.run(main())
