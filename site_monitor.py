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

CONFIG_FILE = "courier_config.json"

CHECK_INTERVAL_SECONDS = 600
MAX_SEND_PER_RUN = 20
MAX_LINKS_PER_SITE = 30

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
    try:
        return urlparse(url).netloc.replace("www.", "")
    except:
        return "Mənbə tapılmadı"


def exists(link):
    cursor.execute("SELECT link FROM posts WHERE link=?", (link,))
    return cursor.fetchone() is not None


def save(link, title, source):
    cursor.execute(
        "INSERT OR IGNORE INTO posts (link, title, source) VALUES (?, ?, ?)",
        (link, title, source)
    )
    conn.commit()


def extract_keywords(config):
    keywords = set()

    try:
        rules = config.get("condition", {}).get("rules", [])

        for rule in rules:
            value = rule.get("value", "")

            for part in re.split(r"[\r\n]+", value):
                word = clean_text(part)
                if word and len(word) > 1:
                    keywords.add(word.lower())

    except:
        pass

    return list(keywords)


def load_sites():
    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)

    sites = []

    for item in data.get("list", []):
        config = item.get("config", {})

        url = config.get("url")
        title = config.get("title", "")
        selectors = config.get("selectors", [])
        keywords = extract_keywords(config)

        if not url:
            continue

        xpath_values = []

        for selector in selectors:
            if selector.get("type") == "xpath":
                value = selector.get("value")
                if value:
                    xpath_values.append(value)

        sites.append({
            "url": url,
            "title": title,
            "xpaths": xpath_values,
            "keywords": keywords
        })

    return sites


def keyword_match(title, keywords):
    if not keywords:
        return True

    title_lower = title.lower()

    for keyword in keywords:
        if keyword in title_lower:
            return True

    return False


def is_bad_link(title, link):
    title_lower = title.lower()
    link_lower = link.lower()

    bad_words = [
        "ana səhifə", "haqqımızda", "əlaqə", "reklam",
        "login", "giriş", "qeydiyyat", "axtarış",
        "facebook", "instagram", "youtube", "telegram",
        "twitter", "linkedin", "abunə"
    ]

    bad_domains = [
        "facebook.com",
        "instagram.com",
        "youtube.com",
        "t.me",
        "twitter.com",
        "x.com",
        "linkedin.com"
    ]

    if len(title) < 10:
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
            print("XPath xətası:", xpath, e)
            continue

        for block in blocks:
            try:
                links = block.xpath(".//a[@href]")
            except:
                continue

            for a in links:
                href = a.get("href")
                title = clean_text(a.text_content())

                if not href or not title:
                    continue

                link = urljoin(page_url, href)

                if "#" in link:
                    link = link.split("#")[0]

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
        link = urljoin(page_url, a["href"])

        if "#" in link:
            link = link.split("#")[0]

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
    xpaths = site["xpaths"]
    keywords = site["keywords"]

    headers = {
        "User-Agent": "Mozilla/5.0"
    }

    try:
        response = requests.get(page_url, headers=headers, timeout=20)
        response.encoding = "utf-8"

        if response.status_code != 200:
            print(f"Açılmadı: {page_url} | Status: {response.status_code}")
            return []

    except Exception as e:
        print(f"Sayt xətası: {page_url} | {e}")
        return []

    page_html = response.text

    items = []

    if xpaths:
        items = extract_links_from_xpath(page_url, page_html, xpaths, keywords)

    if not items:
        items = extract_links_fallback(page_url, page_html, keywords)

    unique = {}

    for item in items:
        unique[item["link"]] = item

    return list(unique.values())


def check_sites(first_run=False):
    sent_count = 0
    sites = load_sites()

    print(f"Yüklənən sayt sayı: {len(sites)}")

    for site in sites:
        print(f"Yoxlanır: {site['url']}")

        items = fetch_site(site)

        print(f"Tapılan uyğun link sayı: {len(items)}")

        if not items:
            continue

        for item in items[:MAX_LINKS_PER_SITE]:
            title = item["title"]
            link = item["link"]
            source = item["source"]

            if exists(link):
                continue

            save(link, title, source)

            if first_run:
                print(f"İlkin bazaya yazıldı: {source} | {title[:60]}")
                continue

            message = f"""
🆕 Yeni xəbər / paylaşım

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

            print(f"Göndərildi: {source} | {title[:60]}")

            if sent_count >= MAX_SEND_PER_RUN:
                print("Bu dövr üçün göndərmə limiti tamamlandı.")
                return


print("🚀 JSON əsaslı sayt monitorinq botu işə düşdü.")
send_telegram("✅ JSON əsaslı sayt monitorinq botu işə düşdü.")

print("📦 İlk yoxlama: mövcud xəbərlər bazaya yazılır, Telegram-a göndərilmir.")
check_sites(first_run=True)

print("✅ İlkin indeksləmə tamamlandı. Bundan sonra yalnız yeni uyğun xəbərlər göndəriləcək.")
send_telegram("✅ İlkin indeksləmə tamamlandı. Bot yeni xəbərləri izləyir.")

while True:
    print("🔎 Yeni xəbərlər yoxlanılır...")
    check_sites(first_run=False)
    time.sleep(CHECK_INTERVAL_SECONDS)
