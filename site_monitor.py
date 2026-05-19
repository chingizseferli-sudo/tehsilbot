import json
import re
import time
import sqlite3
import requests
from bs4 import BeautifulSoup
from lxml import html
from urllib.parse import urljoin, urlparse

BOT_TOKEN = "8820784481:AAGMe9uWrD97Xh1nET-JU8AgZAqggZ234fg"
CHAT_ID = "1271870098"

CONFIG_FILE = "courier_config_clean.json"

CHECK_INTERVAL_SECONDS = 600
MAX_SEND_PER_RUN = 20
MAX_LINKS_PER_SITE = 15

conn = sqlite3.connect("site_monitor.db")
cursor = conn.cursor()

cursor.execute("""
CREATE TABLE IF NOT EXISTS posts (
    link TEXT PRIMARY KEY,
    title TEXT,
    source TEXT
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

        print("Telegram:", response.status_code)

    except Exception as e:
        print("Telegram xətası:", e)


def clean_text(text):
    return re.sub(r"\s+", " ", text or "").strip()


def get_domain(url):
    return urlparse(url).netloc.replace("www.", "")


def exists(link):
    cursor.execute("SELECT link FROM posts WHERE link=?", (link,))
    return cursor.fetchone() is not None


def save(link, title, source):
    cursor.execute(
        "INSERT OR IGNORE INTO posts (link, title, source) VALUES (?, ?, ?)",
        (link, title, source)
    )
    conn.commit()


def load_sites():
    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)

    sites = data.get("sites", [])
    clean_sites = []

    for site in sites:
        if not site.get("enabled", True):
            continue

        clean_sites.append({
            "name": site.get("name") or get_domain(site.get("url", "")),
            "url": site.get("url"),
            "xpaths": site.get("xpaths", []),
            "keywords": [k.lower() for k in site.get("keywords", [])],
            "limit": site.get("limit", MAX_LINKS_PER_SITE)
        })

    return [s for s in clean_sites if s["url"]]


def keyword_match(title, keywords):
    if not keywords:
        return True

    title_lower = title.lower()
    return any(keyword in title_lower for keyword in keywords)


def is_bad_link(title, link):
    title_lower = title.lower()
    link_lower = link.lower()

    bad_words = [
        "ana səhifə", "haqqımızda", "əlaqə", "reklam",
        "giriş", "qeydiyyat", "axtarış", "abunə",
        "facebook", "instagram", "youtube", "telegram",
        "twitter", "linkedin"
    ]

    bad_domains = [
        "facebook.com", "instagram.com", "youtube.com",
        "t.me", "twitter.com", "x.com", "linkedin.com"
    ]

    if len(title) < 12:
        return True

    if any(word in title_lower for word in bad_words):
        return True

    if any(domain in link_lower for domain in bad_domains):
        return True

    return False


def extract_links_from_xpath(page_url, page_html, xpaths, keywords):
    results = []

    try:
        tree = html.fromstring(page_html)
    except Exception as e:
        print("HTML parse xətası:", e)
        return []

    for xpath in xpaths:
        try:
            blocks = tree.xpath(xpath)
        except Exception as e:
            print("XPath xətası:", e)
            continue

        print(f"XPath üzrə blok sayı: {len(blocks)}")

        for block in blocks:
            try:
                if hasattr(block, "tag") and block.tag == "a":
                    links = [block]
                else:
                    links = block.xpath(".//a[@href]")
            except Exception as e:
                print("Link çıxarma xətası:", e)
                continue

            print(f"Blok daxilində link sayı: {len(links)}")

            for a in links:
                href = a.get("href")
                title = clean_text(a.text_content())

                if not href or not title:
                    continue

                link = urljoin(page_url, href).split("#")[0]

                if not link.startswith("http"):
                    continue

                if is_bad_link(title, link):
                    continue

                if not keyword_match(title, keywords):
                    continue

                results.append({
                    "title": title,
                    "link": link,
                    "source": get_domain(page_url)
                })

    return results


def extract_links_fallback(page_url, page_html, keywords):
    soup = BeautifulSoup(page_html, "html.parser")
    results = []

    for a in soup.find_all("a", href=True):
        title = clean_text(a.get_text(strip=True))
        link = urljoin(page_url, a["href"]).split("#")[0]

        if not link.startswith("http"):
            continue

        if is_bad_link(title, link):
            continue

        if not keyword_match(title, keywords):
            continue

        results.append({
            "title": title,
            "link": link,
            "source": get_domain(page_url)
        })

    return results


def fetch_site(site):
    page_url = site["url"]

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "Accept-Language": "az-AZ,az;q=0.9,en-US;q=0.8,en;q=0.7",
        "Connection": "keep-alive",
        "Referer": "https://www.google.com/"
    }

    try:
        print(f"Sayt açılır: {page_url}")

        response = requests.get(
            page_url,
            headers=headers,
            timeout=10
        )

        print(f"Status: {response.status_code}")

        if response.status_code != 200:
            return []

        response.encoding = response.apparent_encoding

    except Exception as e:
        print(f"Sayt xətası: {page_url} | {e}")
        return []

    page_html = response.text

    items = extract_links_from_xpath(
        page_url,
        page_html,
        site["xpaths"],
        site["keywords"]
    )

    if not items:
        print("XPath nəticə vermədi, fallback işləyir...")
        items = extract_links_fallback(
            page_url,
            page_html,
            site["keywords"]
        )

    unique = {}

    for item in items:
        unique[item["link"]] = item

    return list(unique.values())


def check_sites(first_run=False):
    sent_count = 0
    sites = load_sites()

    print(f"Yüklənən sayt sayı: {len(sites)}")

    for site in sites:
        print(f"Yoxlanır: {site['name']} | {site['url']}")

        items = fetch_site(site)

        print(f"Tapılan uyğun link sayı: {len(items)}")

        if not items:
            continue

        limit = site.get("limit", MAX_LINKS_PER_SITE)
        latest_new_item = None

        for item in items[:limit]:
            if not exists(item["link"]):
                latest_new_item = item
                break

        if not latest_new_item:
            print("Bu saytda yeni uyğun xəbər yoxdur.")
            continue

        title = latest_new_item["title"]
        link = latest_new_item["link"]
        source = latest_new_item["source"]

        save(link, title, source)

        if first_run:
            print(f"İlkin bazaya yazıldı: {source} | {title[:70]}")
            continue

        message = f"""
🆕 Yeni uyğun xəbər

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

        print(f"Göndərildi: {source} | {title[:70]}")

        if sent_count >= MAX_SEND_PER_RUN:
            print("Bu dövr üçün göndərmə limiti tamamlandı.")
            return


print("🚀 Sayt monitorinq botu işə düşdü.")
send_telegram("✅ Sayt monitorinq botu işə düşdü.")

print("📦 İlk yoxlama: mövcud xəbərlər bazaya yazılır, Telegram-a göndərilmir.")
check_sites(first_run=True)

print("✅ İlkin indeksləmə tamamlandı. Bundan sonra yalnız yeni uyğun xəbərlər göndəriləcək.")
send_telegram("✅ İlkin indeksləmə tamamlandı. Bot yeni xəbərləri izləyir.")

while True:
    print("🔎 Yeni xəbərlər yoxlanılır...")
    check_sites(first_run=False)
    time.sleep(CHECK_INTERVAL_SECONDS)
