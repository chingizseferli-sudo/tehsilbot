print("PYTHON STARTED", flush=True)

import json
import os
import re
import time
import sqlite3
import requests
from bs4 import BeautifulSoup
from lxml import html
from urllib.parse import urljoin, urlparse
from datetime import datetime, timedelta
from dateutil import parser

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
CHAT_ID = os.getenv("CHAT_ID", "").strip()

CHECK_INTERVAL_SECONDS = 60
MAX_SEND_PER_RUN = 10
MAX_LINKS_PER_SITE = 1
NEWS_TIME_LIMIT_HOURS = 1

CONFIG_FILES = [
    "courier_config_clean.json",
    "discovered_sites.json"
]

PATTERNS_FILE = "patterns.json"
DB_FILE = "site_monitor.db"

conn = sqlite3.connect(DB_FILE)
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
    if not BOT_TOKEN or not CHAT_ID:
        print("BOT_TOKEN və ya CHAT_ID yoxdur.", flush=True)
        return

    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"

    try:
        r = requests.post(
            url,
            data={
                "chat_id": CHAT_ID,
                "text": message,
                "disable_web_page_preview": False
            },
            timeout=15
        )

        print("Telegram:", r.status_code, flush=True)

        if r.status_code == 429:
            retry_after = r.json().get("parameters", {}).get("retry_after", 30)
            time.sleep(retry_after + 2)

    except Exception as e:
        print("Telegram xətası:", e, flush=True)


def clean_text(text):
    return re.sub(r"\s+", " ", text or "").strip()


def get_domain(url):
    return urlparse(url).netloc.replace("www.", "").lower()


def exists(link):
    cursor.execute("SELECT link FROM posts WHERE link=?", (link,))
    return cursor.fetchone() is not None


def save(link, title, source):
    cursor.execute(
        "INSERT OR IGNORE INTO posts (link, title, source) VALUES (?, ?, ?)",
        (link, title, source)
    )
    conn.commit()


def unique_items(items):
    unique = {}
    for item in items:
        unique[item["link"]] = item
    return list(unique.values())


def extract_keywords_from_rules(site):
    keywords = set()

    for k in site.get("keywords", []):
        if str(k).strip():
            keywords.add(str(k).lower().strip())

    for rule in site.get("condition_rules", []):
        value = rule.get("value", "")

        for part in re.split(r"[|\r\n]+", value):
            word = clean_text(part)
            word = word.replace(".*", "").strip()

            if word and len(word) > 1:
                keywords.add(word.lower())

    return list(keywords)


def load_sites():
    all_sites = []
    seen_urls = set()

    for config_file in CONFIG_FILES:
        try:
            with open(config_file, "r", encoding="utf-8") as f:
                data = json.load(f)

            for site in data.get("sites", []):
                if not site.get("enabled", True):
                    continue

                url = clean_text(site.get("url", ""))

                if not url:
                    continue

                if url in seen_urls:
                    continue

                seen_urls.add(url)

                xpaths = site.get("xpaths", [])

                if not xpaths and site.get("selectors"):
                    for s in site.get("selectors", []):
                        if s.get("type") == "xpath" and s.get("value"):
                            xpaths.append(s.get("value"))

                all_sites.append({
                    "name": site.get("name") or get_domain(url),
                    "url": url,
                    "xpaths": xpaths,
                    "selector": site.get("selector"),
                    "keywords": extract_keywords_from_rules(site),
                    "limit": site.get("limit", MAX_LINKS_PER_SITE)
                })

        except FileNotFoundError:
            print(f"Fayl tapılmadı: {config_file}", flush=True)

        except Exception as e:
            print(f"JSON oxunmadı: {config_file} | {e}", flush=True)

    print(f"Toplam sayt sayı: {len(all_sites)}", flush=True)
    return all_sites


def load_patterns():
    try:
        with open(PATTERNS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return {}


def keyword_match(title, keywords):
    default_keywords = [
        "təhsil", "məktəb", "şagird", "müəllim",
        "universitet", "imtahan", "tələbə", "elm",
        "tədris", "abituriyent", "magistr", "doktorant",
        "kollec", "lisey", "sertifikasiya", "sertifikatlaşdırma",
        "dim", "tkta", "məktəbəqədər", "institut"
    ]

    all_keywords = keywords if keywords else default_keywords
    title_lower = title.lower()

    return any(k.lower() in title_lower for k in all_keywords)


def is_bad_link(title, link):
    title_lower = title.lower()
    link_lower = link.lower()

    bad_words = [
        "ana səhifə", "haqqımızda", "əlaqə", "reklam",
        "giriş", "qeydiyyat", "axtarış", "abunə",
        "facebook", "instagram", "youtube", "telegram",
        "twitter", "linkedin", "rss", "bütün xəbərlər",
        "daha çox", "arxiv", "kateqoriya"
    ]

    bad_domains = [
        "facebook.com", "instagram.com", "youtube.com",
        "t.me", "twitter.com", "x.com", "linkedin.com"
    ]

    bad_extensions = [
        ".jpg", ".jpeg", ".png", ".gif", ".webp",
        ".pdf", ".doc", ".docx", ".xls", ".xlsx",
        ".zip", ".rar", ".mp4", ".mp3"
    ]

    if len(title) < 15:
        return True

    if any(w in title_lower for w in bad_words):
        return True

    if any(d in link_lower for d in bad_domains):
        return True

    if any(link_lower.endswith(ext) for ext in bad_extensions):
        return True

    path = urlparse(link_lower).path.strip("/").lower()

    section_paths = [
        "news", "xeber", "xeberler", "xəbərlər",
        "media/news", "category", "kateqoriya",
        "archive", "arxiv", "allnews", "newsarchive",
        "az/news", "az/xeberler", "az/xəbərlər",
        "az/metbuat/xeberler", "az/page/media/news",
        "az/news-and-updates", "p/news", "lastnews"
    ]

    if path in section_paths:
        return True

    if path.endswith("/news") or path.endswith("/xeberler") or path.endswith("/xəbərlər"):
        return True

    return False


def is_recent_news(published_time):
    try:
        if not published_time:
            return False

        text = str(published_time).strip().lower()

        if "tarix tapılmadı" in text:
            return False

        dt = parser.parse(text, fuzzy=True, dayfirst=True)

        if dt.tzinfo is not None:
            dt = dt.replace(tzinfo=None)

        diff = datetime.now() - dt

        if diff.total_seconds() < 0:
            return False

        hours = diff.total_seconds() / 3600
        print(f"Tarix yoxlanır: {published_time} | fərq: {hours:.1f} saat", flush=True)

        return diff <= timedelta(hours=NEWS_TIME_LIMIT_HOURS)

    except Exception as e:
        print(f"Tarix yoxlama xətası: {published_time} | {e}", flush=True)
        return False


def extract_publish_time_from_article(article_url):
    headers = {
        "User-Agent": "Mozilla/5.0",
        "Accept-Language": "az-AZ,az;q=0.9,en-US;q=0.8"
    }

    try:
        r = requests.get(article_url, headers=headers, timeout=10)
        r.encoding = r.apparent_encoding
        tree = html.fromstring(r.text)

        possible_xpaths = [
            "//time/@datetime",
            "//time/text()",
            "//meta[@property='article:published_time']/@content",
            "//meta[@name='article:published_time']/@content",
            "//meta[@itemprop='datePublished']/@content",
            "//meta[@name='pubdate']/@content",
            "//meta[@property='og:updated_time']/@content",
            "//meta[@name='date']/@content",
            "//meta[@name='DC.date.issued']/@content",
            "//span[contains(@class,'date')]/text()",
            "//div[contains(@class,'date')]/text()",
            "//span[contains(@class,'time')]/text()",
            "//div[contains(@class,'time')]/text()"
        ]

        for xp in possible_xpaths:
            result = tree.xpath(xp)

            if result:
                value = clean_text(str(result[0]))

                if len(value) > 5:
                    return value

    except Exception as e:
        print("Tarix çıxarma xətası:", e, flush=True)

    return None


def extract_links_from_xpath(page_url, page_html, xpaths, keywords):
    results = []

    if not xpaths:
        return []

    try:
        tree = html.fromstring(page_html)
    except Exception as e:
        print("HTML parse xətası:", e, flush=True)
        return []

    for xp in xpaths:
        try:
            blocks = tree.xpath(xp)
        except Exception as e:
            print("XPath xətası:", e, flush=True)
            continue

        print(f"XPath üzrə blok sayı: {len(blocks)}", flush=True)

        for block in blocks:
            try:
                links = [block] if hasattr(block, "tag") and block.tag == "a" else block.xpath(".//a[@href]")
            except:
                continue

            for a in links:
                href = a.get("href")
                title = clean_text(a.text_content())
                link = urljoin(page_url, href).split("#")[0]

                if not href or not title:
                    continue

                if not link.startswith("http"):
                    continue

                if get_domain(page_url) != get_domain(link):
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

    return unique_items(results)


def extract_links_by_selector(page_url, page_html, selector, keywords):
    soup = BeautifulSoup(page_html, "html.parser")
    results = []

    try:
        blocks = soup.select(selector)
    except Exception as e:
        print("Selector xətası:", e, flush=True)
        return []

    for block in blocks:
        links = block.find_all("a", href=True)

        if block.name == "a" and block.get("href"):
            links.append(block)

        for a in links:
            title = clean_text(a.get_text(" ", strip=True))
            link = urljoin(page_url, a["href"]).split("#")[0]

            if not title or not link.startswith("http"):
                continue

            if get_domain(page_url) != get_domain(link):
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

    return unique_items(results)


def extract_links_by_patterns(page_url, page_html, keywords, patterns):
    soup = BeautifulSoup(page_html, "html.parser")
    results = []

    for a in soup.find_all("a", href=True):
        title = clean_text(a.get_text(" ", strip=True))
        link = urljoin(page_url, a["href"]).split("#")[0]

        if not title or not link.startswith("http"):
            continue

        if get_domain(page_url) != get_domain(link):
            continue

        if len(title) < 20:
            continue

        link_lower = link.lower()

        if not any(pattern.lower() in link_lower for pattern in patterns):
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

    return unique_items(results)


def extract_links_fallback(page_url, page_html, keywords):
    soup = BeautifulSoup(page_html, "html.parser")
    results = []

    article_patterns = [
        "/news/", "/xeber/", "/xeberler/", "/xəbərlər/",
        "/az/news/", "/az/xeber/", "/az/xeberler/",
        "/post/", "/article/", "/read/", "/item/",
        "/son-xeber/", "/sosial/", "/resmi-xeber/",
        "/hadise/", "/politic/", "/world/", "/economy/",
        "/education/", "/elm/", "/tehsil/"
    ]

    for a in soup.find_all("a", href=True):
        title = clean_text(a.get_text(" ", strip=True))
        link = urljoin(page_url, a["href"]).split("#")[0]

        if not title or not link.startswith("http"):
            continue

        if get_domain(page_url) != get_domain(link):
            continue

        if len(title) < 20:
            continue

        if not any(p in link.lower() for p in article_patterns):
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

    return unique_items(results)


def fetch_site(site, patterns_data):
    page_url = site["url"]
    selector = site.get("selector")
    xpaths = site.get("xpaths", [])
    keywords = site.get("keywords", [])

    headers = {
        "User-Agent": "Mozilla/5.0",
        "Accept-Language": "az-AZ,az;q=0.9,en-US;q=0.8",
        "Referer": "https://www.google.com/"
    }

    try:
        print(f"Sayt açılır: {page_url}", flush=True)

        r = requests.get(page_url, headers=headers, timeout=12)
        print(f"Status: {r.status_code}", flush=True)

        if r.status_code != 200:
            return []

        r.encoding = r.apparent_encoding

    except Exception as e:
        print(f"Sayt xətası: {page_url} | {e}", flush=True)
        return []

    page_html = r.text
    domain = get_domain(page_url)
    site_patterns = patterns_data.get(domain, [])

    items = []

    if selector:
        items = extract_links_by_selector(page_url, page_html, selector, keywords)

    if not items and xpaths:
        items = extract_links_from_xpath(page_url, page_html, xpaths, keywords)

    if not items and site_patterns:
        print(f"Pattern fallback işləyir: {domain}", flush=True)
        items = extract_links_by_patterns(page_url, page_html, keywords, site_patterns)

    if not items:
        print("HTML fallback işləyir...", flush=True)
        items = extract_links_fallback(page_url, page_html, keywords)

    return unique_items(items)


def check_sites():
    sent_count = 0
    sites = load_sites()
    patterns_data = load_patterns()

    print(f"Yüklənən sayt sayı: {len(sites)}", flush=True)

    for site in sites:
        print(f"Yoxlanır: {site['name']} | {site['url']}", flush=True)

        items = fetch_site(site, patterns_data)

        print(f"Tapılan uyğun link sayı: {len(items)}", flush=True)

        if not items:
            print("Bu saytda uyğun xəbər tapılmadı.", flush=True)
            continue

        limit = site.get("limit", MAX_LINKS_PER_SITE)
        sent_for_this_site = False

        for item in items[:limit]:
            title = item["title"]
            link = item["link"]
            source = item["source"]

            if exists(link):
                continue

            published_time = extract_publish_time_from_article(link)

            if not published_time:
                print(f"Tarix tapılmadı, xəbər keçildi: {title[:70]}", flush=True)
                continue

            if not is_recent_news(published_time):
                print(f"Köhnə xəbər keçildi: {title[:70]} | {published_time}", flush=True)
                continue

            message = f"""
🆕 Yeni uyğun xəbər

📌 Başlıq:
{title}

🌐 Mənbə:
{source}

🕒 Tarix və saat:
{published_time}

🔗 Link:
{link}
"""

            send_telegram(message)
            save(link, title, source)

            print(f"Göndərildi: {source} | {title[:70]}", flush=True)

            sent_count += 1
            sent_for_this_site = True

            time.sleep(1)
            break

        if not sent_for_this_site:
            print("Bu saytda yeni uyğun xəbər yoxdur.", flush=True)

        if sent_count >= MAX_SEND_PER_RUN:
            print("Bu dövr üçün göndərmə limiti tamamlandı.", flush=True)
            return


print("🚀 Sayt monitorinq botu işə düşdü.", flush=True)
send_telegram("✅ Bot işə düşdü və saytları yoxlamağa başladı.")

while True:
    print("🔎 Yeni xəbərlər yoxlanılır...", flush=True)
    check_sites()
    time.sleep(CHECK_INTERVAL_SECONDS)
