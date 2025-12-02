import os
import json
import re
import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv

# Загружаем переменные из .env
load_dotenv()

BASE_URL = "https://itd.rada.gov.ua"
BILLS_URL = f"{BASE_URL}/billinfo/Bills/period"

SEEN_FILE = "seen_bills.json"

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

KEYWORDS = [
    # Податки / налоги
    "податк", "податков", "оподаткуван",
    "податковий", "податкова", "податкового кодексу",

    # Кодекси / изменения в кодексах
    "кодекс", "зміни до кодексу", "внести зміни до кодексу",
    "внесення змін до кодексу", "кримінального кодексу",
    "цивільного кодексу", "податкового кодексу", "бюджетного кодексу",
    "митного кодексу",

    # Експорт
    "експорт", "експортн", "експортний", "експортерам",

    # Імпорт
    "імпорт", "імпортн", "імпортний", "імпортер",

    # Ліцензування
    "ліценз", "ліцензійн", "ліцензія", "ліцензування",
    "ліцензованої діяльності",

    # Юридичні особи
    "юридичн", "юридична особа", "юридичні особи",
    "юрособ", "суб'єкт господарювання", "суб’єкти господарювання",
]



# ---------- Вспомогательные функции ----------

def load_seen():
    """Считываем уже отправленные ID законопроектов."""
    if not os.path.exists(SEEN_FILE):
        return set()
    try:
        with open(SEEN_FILE, "r", encoding="utf-8") as f:
            return set(json.load(f))
    except Exception:
        return set()


def save_seen(seen_ids: set):
    """Сохраняем ID законопроектов, которые уже обработали."""
    with open(SEEN_FILE, "w", encoding="utf-8") as f:
        json.dump(list(seen_ids), f, ensure_ascii=False, indent=2)


def matches_keywords(text: str) -> bool:
    """Проверка по ключевым словам в названии."""
    if not text:
        return False
    t = text.lower()
    return any(k in t for k in KEYWORDS)


def send_to_telegram(text: str):
    """Отправка сообщения в телеграм-канал."""
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print("[WARN] TELEGRAM_TOKEN или TELEGRAM_CHAT_ID не заданы, сообщение не отправлено")
        print(text)
        return

    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "disable_web_page_preview": False,
        "parse_mode": "HTML",
    }
    try:
        resp = requests.post(url, json=payload, timeout=30)
        resp.raise_for_status()
        print("[INFO] Отправлено в Telegram")
    except Exception as e:
        print(f"[ERROR] Не удалось отправить в Telegram: {e}")
        print(text)


# ---------- Парсинг списка и карточек ----------

def fetch_list():
    """
    Получаем со страницы Bills/period только ID и номера законопроектов.
    """
    resp = requests.get(BILLS_URL, timeout=30)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "lxml")

    bills = []

    for a in soup.select("a[href*='/billinfo/Bills/Card/']"):
        href = a.get("href", "").strip()
        if not href:
            continue

        if not href.startswith("http"):
            href = BASE_URL + href

        bill_id = href.split("/")[-1]
        number = a.get_text(strip=True)

        bills.append(
            {
                "id": bill_id,
                "number": number,
                "url": href,
            }
        )

    return bills


TITLE_START_KEYWORDS = [
    "проект закону",
    "проєкт закону",
    "проект постанови",
    "проєкт постанови",
    "закон україни",
    "закон украины",
]

TITLE_STOP_KEYWORDS = [
    "номер, дата реєстрац",
    "номер, дата реєстрацi",
    "номер реєстрацiї",
    "номер реєстрації",
]


def extract_clean_title(raw_text: str) -> str:
    """
    Из большого куска текста вытаскиваем только строку типа
    'Проект Закону про ...' или 'Закон України про ...'.
    """
    if not raw_text:
        return ""

    text = " ".join(raw_text.split())
    low = text.lower()

    # ищем, с какого места начинается реальное название
    start = None    # индекс начала названия
    for kw in TITLE_START_KEYWORDS:
        i = low.find(kw)
        if i != -1 and (start is None or i < start):
            start = i

    if start is None:
        return ""

    # ищем, где обрубить (до служебной части с номерами/датами)
    end = len(text)
    for kw in TITLE_STOP_KEYWORDS:
        j = low.find(kw, start)
        if j != -1 and j < end:
            end = j

    title = text[start:end].strip(" :;\n\t")
    return title


def fetch_details(bill_id: str):
    """
    Заходим в карточку законопроекту и вытаскиваем нормальное название и дату.
    Возвращает dict или None в случае ошибки.
    """
    url = f"{BASE_URL}/billinfo/Bills/Card/{bill_id}"
    try:
        resp = requests.get(url, timeout=30)
        resp.raise_for_status()
    except Exception as e:
        print(f"[ERROR] Не удалось загрузить карточку {bill_id}: {e}")
        return None

    soup = BeautifulSoup(resp.text, "lxml")

    # --- Название ---
    name_candidates = []

    try:
        for tag in soup.find_all(["td", "div", "span", "p", "a", "h1", "h2", "h3"]):
            text = tag.get_text(" ", strip=True)
            if not text:
                continue
            low = text.lower()
            if any(kw in low for kw in TITLE_START_KEYWORDS):
                name_candidates.append(text)

        clean_titles = [extract_clean_title(t) for t in name_candidates]
        clean_titles = [t for t in clean_titles if 10 <= len(t) <= 300]

        if clean_titles:
            # берём самое длинное адекватное название
            title = max(clean_titles, key=len)
        else:
            title = soup.title.get_text(strip=True) if soup.title else ""
    except Exception as e:
        print(f"[ERROR] Ошибка при разборе названия для {bill_id}: {e}")
        title = ""

    # --- Дата регистрации ---
    date = ""
    try:
        # Берём весь текст страницы одной строкой
        page_text = " ".join(soup.get_text(" ", strip=True).split())

        # Пример строки: "Номер, дата реєстрації: 14269 від 01.12.2025 ..."
        m = re.search(
            r"Номер,?\s*дата\s*реєстрац[іїi].{0,80}?(\d{2}\.\d{2}\.\d{4})",
            page_text,
            flags=re.IGNORECASE,
        )
        if m:
            date = m.group(1)

        # Если вдруг не нашли, ищем по "Дата реєстрації"
        if not date:
            m2 = re.search(
                r"Дата\s*реєстрац[іїi].{0,40}?(\d{2}\.\d{2}\.\d{4})",
                page_text,
                flags=re.IGNORECASE,
            )
            if m2:
                date = m2.group(1)
    except Exception as e:
        print(f"[ERROR] Ошибка при разборе даты для {bill_id}: {e}")
        date = ""

    return {
        "title": title,
        "date": date,
        "url": url,
    }


# ---------- Основная логика ----------

def main():
    print("[INFO] Старт скрипта")
    seen = load_seen()
    print(f"[INFO] Уже обработано ID: {len(seen)}")

    list_bills = fetch_list()
    print(f"[INFO] В списке найдено законопроектов: {len(list_bills)}")

    updated_seen = set(seen)
    new_count = 0
    sent_count = 0

    for b in list_bills:
        bill_id = b["id"]
        if bill_id in seen:
            continue  # уже отправляли или обрабатывали

        details = fetch_details(bill_id)
        if not details:
            print(f"[WARN] Не удалось разобрать карточку {bill_id}, пропускаю")
            updated_seen.add(bill_id)
            continue

        new_count += 1

        title = details["title"]
        date = details["date"]
        url = details["url"]
        number = b["number"]

        print("---------------")
        print(f"ID: {bill_id}")
        print(f"Номер: {number}")
        print(f"Дата: {date}")
        print(f"Название: {title}")
        print(f"URL: {url}")

        if matches_keywords(title):
            sent_count += 1
            msg = (
                f"<b>{title}</b>\n"
                f"Номер: <b>{number}</b>\n"
                f"Дата реєстрації: {date or '—'}\n\n"
                f"{url}"
            )
            send_to_telegram(msg)
        else:
            print("[INFO] Не подходит по ключевым словам")

        updated_seen.add(bill_id)

    save_seen(updated_seen)

    print("========== ИТОГО ==========")
    print(f"Новых законопроектов в списке: {new_count}")
    print(f"Отправлено в Telegram (по ключевым словам): {sent_count}")
    print(f"Всего ID в seen_bills.json: {len(updated_seen)}")


if __name__ == "__main__":
    import time

    print("[INFO] Запуск бота-мониторинга Рады (каждый час)")

    while True:
        try:
            main()
        except Exception as e:
            # чтобы бот не умирал от одной ошибки
            print(f"[ERROR] Во время выполнения main() произошла ошибка: {e}")

        # Ждать 1 час (3600 секунд)
        print("[INFO] Сон 1 час до следующей проверки...")
        time.sleep(3600)
