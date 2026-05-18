import requests
from bs4 import BeautifulSoup
import sqlite3
import time
import re
from urllib.parse import urljoin, urlparse

BOT_TOKEN = "8820784481:AAGMe9uWrD97Xh1nET-JU8AgZAqggZ234fg"
CHAT_ID = "1271870098"

PAGES = [
    "https://example.com/news",
    "https://example.com/latest"
]

CHECK_INTERVAL_SECONDS = 600
MAX_SEND_PER_RUN = 5

conn = sqlite3.connect("site_monitor.db")
cursor = conn.cursor()

cursor.execute("""
CREATE TABLE IF NOT EXISTS posts (
    link TEXT PRIMARY KEY,
    title TEXT
)
""")
conn.commit()


def send_telegram(message):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"

    try:
        response = requests.post(
            url,
            data={
                "chat_id": CHAT_ID,
                "text": message,
                "disable_web_page_preview": False
            },
            timeout=15
        )

        if response.status_code == 429:
            retry_after = response.json().get("parameters", {}).get("retry_after", 30)
            print(f"Telegram limit verdi: {retry_after} saniyə")
            time.sleep(retry_after + 2)

        print("Telegram:", response.status_code, response.text)

    except Exception as e:
        print("Telegram xətası:", e)


def clean_title(title):
    return re.sub(r"\s+", " ", title).strip()


def get_domain(url):
    try:
        return urlparse(url).netloc.replace("www.", "")
    except:
        return "Mənbə tapılmadı"


def exists(link):
    cursor.execute("SELECT link FROM posts WHERE link=?", (link,))
    return cursor.fetchone() is not None


def save(link, title):
    cursor.execute(
        "INSERT OR IGNORE INTO posts (link, title) VALUES (?, ?)",
        (link, title)
    )
    conn.commit()


def extract_links(page_url):
    headers = {
        "User-Agent": "Mozilla/5.0"
    }

    try:
        response = requests.get(page_url, headers=headers, timeout=15)
        response.encoding = "utf-8"

        if response.status_code != 200:
            print(f"Açılmadı: {page_url} | Status: {response.status_code}")
            return []

    except Exception as e:
        print(f"Sayt xətası: {page_url} | {e}")
        return []

    soup = BeautifulSoup(response.text, "html.parser")
    items = []

    for a in soup.find_all("a", href=True):
        title = clean_title(a.get_text(strip=True))
        link = urljoin(page_url, a["href"])

        if not title:
            continue

        if len(title) < 12:
            continue

        if not link.startswith("http"):
            continue

        if "#" in link:
            link = link.split("#")[0]

        items.append({
            "title": title,
            "link": link,
            "source": get_domain(page_url)
        })

    unique = {}
    for item in items:
        unique[item["link"]] = item

    return list(unique.values())


def check_pages(first_run=False):
    sent_count = 0

    for page_url in PAGES:
        print(f"Yoxlanır: {page_url}")

        items = extract_links(page_url)

        if not items:
            print(f"Link tapılmadı: {page_url}")
            continue

        latest_item = items[0]

        title = latest_item["title"]
        link = latest_item["link"]
        source = latest_item["source"]

        if exists(link):
            print(f"Artıq göndərilib: {title[:60]}")
            continue

        save(link, title)

        if first_run:
            print(f"İlkin bazaya yazıldı: {title[:60]}")
            continue

        message = f"""
🆕 Yeni paylaşım

📌 Başlıq:
{title}

🌐 Mənbə:
{source}

🔗 Link:
{link}
"""

        send_telegram(message)
        time.sleep(3)

        sent_count += 1

        if sent_count >= MAX_SEND_PER_RUN:
            print("Bu dövr üçün göndərmə limiti tamamlandı.")
            return


print("🚀 Sayt monitorinq botu işə düşdü.")
send_telegram("✅ Sayt monitorinq botu işə düşdü.")

print("📦 İlk yoxlama: mövcud son paylaşımlar bazaya yazılır, Telegram-a göndərilmir.")
check_pages(first_run=True)

print("✅ İlkin indeksləmə tamamlandı. Bundan sonra yalnız yeni paylaşımlar göndəriləcək.")
send_telegram("✅ İlkin indeksləmə tamamlandı. Bot yeni paylaşımları izləyir.")

while True:
    print("🔎 Yeni paylaşımlar yoxlanılır...")
    check_pages(first_run=False)
    time.sleep(CHECK_INTERVAL_SECONDS)
