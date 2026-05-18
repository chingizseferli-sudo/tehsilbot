import requests
from bs4 import BeautifulSoup
import sqlite3
import time
import re
from urllib.parse import urljoin, urlparse

BOT_TOKEN = "8820784481:AAGMe9uWrD97Xh1nET-JU8AgZAqggZ234fg"
CHAT_ID = "1271870098"

PAGES = [
    "https://admiu.edu.az/x%c9%99b%c9%99rl%c9%99r/",
    "https://qu.edu.az/az/news",
    "https://www.ndu.edu.az/xeberler",
    "https://beu.edu.az/az/media/news",
    "https://news.unec.edu.az/xeber",
    "https://www.nmi.edu.az/",
    "https://sport.edu.az/az/news",
    "https://adu.edu.az/az/xeberler/xeberler/",
    "https://www.aztu.edu.az/az/news",
    "https://adpu.edu.az/index.php/az/x%C9%99b%C9%99rl%C9%99r",
    "https://www.au.edu.az/az/news/?show=2024",
    "https://khazar.org/az/news",
    "https://www.azmiu.edu.az/az/allnews",
    "https://asoiu.edu.az/allNews",
    "https://www.ufaz.az/az/news/",
    "http://bsu.edu.az/az/newsarchive",
    "https://amu.edu.az/news",
    "https://www.atu.edu.az/xeberler/1",
    "https://gdu.edu.az/category/x%c9%99b%c9%99rl%c9%99r/",
    "https://mdu.edu.az/xeberler2025/",
    "https://lsu.edu.az/new/NewsLister/index.php",
    "https://www.sdu.edu.az/az/news",
    "https://bdu-qazax.edu.az/index.php/az/kheberler",
    "https://www.bhos.edu.az/news",
    "https://adda.edu.az/az/news",
    "https://conservatory.edu.az/xeberler/",
    "https://atmu.edu.az/az/xeberler-uni",
    "https://dia.edu.az/xeberler",
    "https://www.adau.edu.az/xeberler/",
    "https://musicacademy.edu.az/az/kheberler.html",
    "https://adra.gov.az/az/haqqimizda/xeberler",
    "https://www.bsu-uni.edu.az/News",
    "https://economics.org.az/az/category/48",
    "https://isi.az/az/news",
    "https://ict.az/az/news",
    "https://www.genres.az/az/category/item/2",
    "https://botany.az/az/news",
    "https://www.imm.az/exp/",
    "https://imbb.az/az/news",
    "https://www.azmbi.az/index.php/az/",
    "https://www.gia.az/news",
    "https://www.nkpi.az/?page=news",
    "https://shao.az/az/news",
    "https://www.dilcilik.az/index.php",
    "https://ict.az/az/news",
    "https://zoologiya.az/az/news",
    "https://radiation.gov.az/az/xeberler",
    "https://physiology.az/az/news",
    "https://president.az/az/news",
    "https://azertag.az/bolme/official_chronicle",
    "https://azertag.az/bolme/official_documents",
    "https://president.az/az/documents",
    "https://nk.gov.az/az/senedler/hamisi",
    "https://nk.gov.az/az/xeberler/hamisi",
    "https://dim.gov.az/az/metbuat/xeberler",
    "https://science.gov.az/az/news",
    "http://www.yeb.science.gov.az/news",
    "https://ameagb.az/az/news",
    "https://tkta.edu.az/az/media/news?page=1",
    "https://baku.edu.gov.az/az/page/9",
    "https://tif.edu.az/xeber/",
    "https://www.stat.gov.az/news/macroeconomy.php?page=1&lang=az",
    "https://www.stat.gov.az/source/education/",
    "https://edu.gov.az/az/news-and-updates",
    "https://azstand.gov.az/az/xeberler",
    "https://arti.edu.az/media/news/",
    "https://vet.edu.gov.az/p/news",
    "https://dp.edu.az/az/index",
    "https://4sim.gov.az/az/page/media/news",
    "https://edu.gov.az/az/esas-senedler",
    "https://www.aef.gov.az/az/news/list/2",
    "https://alumni.dp.edu.az/az/index#"
]

CHECK_INTERVAL_SECONDS = 600
MAX_SEND_PER_RUN = 20

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

        print(f"{page_url} üçün tapılan link sayı: {len(items)}")

        if not items:
            print(f"Link tapılmadı: {page_url}")
            continue

        for item in items[:30]:
            title = item["title"]
            link = item["link"]
            source = item["source"]

            if exists(link):
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
            print(f"Göndərildi: {title[:60]}")

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
