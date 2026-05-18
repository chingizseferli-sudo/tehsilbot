import requests
from bs4 import BeautifulSoup
import sqlite3
import time
import re
from urllib.parse import urljoin, urlparse

BOT_TOKEN = "8820784481:AAGMe9uWrD97Xh1nET-JU8AgZAqggZ234fg"
CHAT_ID = "1271870098"

SITES = [
    {"url": "https://admiu.edu.az/x%c9%99b%c9%99rl%c9%99r/", "selector": "a"},
    {"url": "https://qu.edu.az/az/news", "selector": "a"},
    {"url": "https://www.ndu.edu.az/xeberler", "selector": "a"},
    {"url": "https://beu.edu.az/az/media/news", "selector": "a"},
    {"url": "https://news.unec.edu.az/xeber", "selector": "a"},
    {"url": "https://www.nmi.edu.az/", "selector": "a"},
    {"url": "https://sport.edu.az/az/news", "selector": "a"},
    {"url": "https://adu.edu.az/az/xeberler/xeberler/", "selector": "a"},
    {"url": "https://www.aztu.edu.az/az/news", "selector": "a"},
    {"url": "https://adpu.edu.az/index.php/az/x%C9%99b%C9%99rl%C9%99r", "selector": "a"},
    {"url": "https://www.au.edu.az/az/news/?show=2024", "selector": "a"},
    {"url": "https://khazar.org/az/news", "selector": "a"},
    {"url": "https://www.azmiu.edu.az/az/allnews", "selector": "a"},
    {"url": "https://asoiu.edu.az/allNews", "selector": "a"},
    {"url": "https://www.ufaz.az/az/news/", "selector": "a"},
    {"url": "http://bsu.edu.az/az/newsarchive", "selector": "a"},
    {"url": "https://amu.edu.az/news", "selector": "a"},
    {"url": "https://www.atu.edu.az/xeberler/1", "selector": "a"},
    {"url": "https://gdu.edu.az/category/x%c9%99b%c9%99rl%c9%99r/", "selector": "a"},
    {"url": "https://mdu.edu.az/xeberler2025/", "selector": "a"},
    {"url": "https://lsu.edu.az/new/NewsLister/index.php", "selector": "a"},
    {"url": "https://www.sdu.edu.az/az/news", "selector": "a"},
    {"url": "https://bdu-qazax.edu.az/index.php/az/kheberler", "selector": "a"},
    {"url": "https://www.bhos.edu.az/news", "selector": "a"},
    {"url": "https://adda.edu.az/az/news", "selector": "a"},
    {"url": "https://conservatory.edu.az/xeberler/", "selector": "a"},
    {"url": "https://atmu.edu.az/az/xeberler-uni", "selector": "a"},
    {"url": "https://dia.edu.az/xeberler", "selector": "a"},
    {"url": "https://www.adau.edu.az/xeberler/", "selector": "a"},
    {"url": "https://musicacademy.edu.az/az/kheberler.html", "selector": "a"},
    {"url": "https://adra.gov.az/az/haqqimizda/xeberler", "selector": "a"},
    {"url": "https://www.bsu-uni.edu.az/News", "selector": "a"},
    {"url": "https://economics.org.az/az/category/48", "selector": "a"},
    {"url": "https://isi.az/az/news", "selector": "a"},
    {"url": "https://ict.az/az/news", "selector": "a"},
    {"url": "https://www.genres.az/az/category/item/2", "selector": "a"},
    {"url": "https://botany.az/az/news", "selector": "a"},
    {"url": "https://www.imm.az/exp/", "selector": "a"},
    {"url": "https://imbb.az/az/news", "selector": "a"},
    {"url": "https://www.azmbi.az/index.php/az/", "selector": "a"},
    {"url": "https://www.gia.az/news", "selector": "a"},
    {"url": "https://www.nkpi.az/?page=news", "selector": "a"},
    {"url": "https://shao.az/az/news", "selector": "a"},
    {"url": "https://www.dilcilik.az/index.php", "selector": "a"},
    {"url": "https://zoologiya.az/az/news", "selector": "a"},
    {"url": "https://radiation.gov.az/az/xeberler", "selector": "a"},
    {"url": "https://physiology.az/az/news", "selector": "a"},
    {"url": "https://president.az/az/news", "selector": "a"},
    {"url": "https://azertag.az/bolme/official_chronicle", "selector": "a"},
    {"url": "https://azertag.az/bolme/official_documents", "selector": "a"},
    {"url": "https://president.az/az/documents", "selector": "a"},
    {"url": "https://nk.gov.az/az/senedler/hamisi", "selector": "a"},
    {"url": "https://nk.gov.az/az/xeberler/hamisi", "selector": "a"},
    {"url": "https://dim.gov.az/az/metbuat/xeberler", "selector": "a"},
    {"url": "https://science.gov.az/az/news", "selector": "a"},
    {"url": "http://www.yeb.science.gov.az/news", "selector": "a"},
    {"url": "https://ameagb.az/az/news", "selector": "a"},
    {"url": "https://tkta.edu.az/az/media/news?page=1", "selector": "a"},
    {"url": "https://baku.edu.gov.az/az/page/9", "selector": "a"},
    {"url": "https://tif.edu.az/xeber/", "selector": "a"},
    {"url": "https://www.stat.gov.az/news/macroeconomy.php?page=1&lang=az", "selector": "a"},
    {"url": "https://www.stat.gov.az/source/education/", "selector": "a"},
    {"url": "https://edu.gov.az/az/news-and-updates", "selector": "a"},
    {"url": "https://azstand.gov.az/az/xeberler", "selector": "a"},
    {"url": "https://arti.edu.az/media/news/", "selector": "a"},
    {"url": "https://vet.edu.gov.az/p/news", "selector": "a"},
    {"url": "https://dp.edu.az/az/index", "selector": "a"},
    {"url": "https://4sim.gov.az/az/page/media/news", "selector": "a"},
    {"url": "https://edu.gov.az/az/esas-senedler", "selector": "a"},
    {"url": "https://www.aef.gov.az/az/news/list/2", "selector": "a"},
    {"url": "https://alumni.dp.edu.az/az/index#", "selector": "a"}
]

CHECK_INTERVAL_SECONDS = 600
MAX_SEND_PER_RUN = 20
MAX_LINKS_PER_SITE = 30

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


def is_bad_link(title, link):
    title_lower = title.lower()
    link_lower = link.lower()

    bad_words = [
        "ana səhifə", "haqqımızda", "əlaqə", "reklam", "sitemap",
        "facebook", "instagram", "youtube", "telegram", "twitter",
        "linkedin", "login", "giriş", "qeydiyyat", "search"
    ]

    bad_domains = [
        "facebook.com", "instagram.com", "youtube.com", "t.me",
        "twitter.com", "x.com", "linkedin.com"
    ]

    if any(word in title_lower for word in bad_words):
        return True

    if any(domain in link_lower for domain in bad_domains):
        return True

    if len(title) < 12:
        return True

    return False


def extract_links(site):
    page_url = site["url"]
    selector = site.get("selector", "a")

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

    for a in soup.select(selector):
        href = a.get("href")

        if not href:
            continue

        title = clean_title(a.get_text(strip=True))
        link = urljoin(page_url, href)

        if "#" in link:
            link = link.split("#")[0]

        if not link.startswith("http"):
            continue

        if is_bad_link(title, link):
            continue

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

    for site in SITES:
        page_url = site["url"]

        print(f"Yoxlanır: {page_url}")

        items = extract_links(site)

        print(f"{page_url} üçün tapılan link sayı: {len(items)}")

        if not items:
            print(f"Link tapılmadı: {page_url}")
            continue

        for item in items[:MAX_LINKS_PER_SITE]:
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
