print("PYTHON STARTED", flush=True)

import json
import os
import re
import time
import sqlite3
import requests
import feedparser
from bs4 import BeautifulSoup
from lxml import html
from urllib.parse import urljoin, urlparse, quote_plus
from datetime import datetime, timedelta
from dateutil import parser

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
CHAT_ID = os.getenv("CHAT_ID", "").strip()

if not BOT_TOKEN:
    print("⚠️ BOT_TOKEN tapılmadı. Railway Variables bölməsinə BOT_TOKEN əlavə et.", flush=True)

if not CHAT_ID:
    print("⚠️ CHAT_ID tapılmadı. Railway Variables bölməsinə CHAT_ID əlavə et.", flush=True)

CHECK_INTERVAL_SECONDS = 600
MAX_SEND_PER_RUN = 20
MAX_LINKS_PER_SITE = 5
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

print("BAZA HAZIRDIR", flush=True)


def send_telegram(message):
    if not BOT_TOKEN or not CHAT_ID:
        print("Telegram göndərilmədi: BOT_TOKEN və ya CHAT_ID yoxdur.", flush=True)
        return

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

        print("Telegram:", response.status_code, flush=True)

        if response.status_code == 429:
            try:
                retry_after = response.json().get("parameters", {}).get("retry_after", 30)
            except Exception:
                retry_after = 30

            print(f"Telegram limit verdi: {retry_after} saniyə", flush=True)
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

                url = (site.get("url") or "").strip()

                if not url:
                    continue

                if url in seen_urls:
                    continue

                seen_urls.add(url)

                all_sites.append({
                    "name": site.get("name") or get_domain(url),
                    "url": url,
                    "xpaths": site.get("xpaths", []),
                    "selector": site.get("selector"),
                    "keywords": [str(k).lower() for k in site.get("keywords", [])],
                    "limit": site.get("limit", MAX_LINKS_PER_SITE),
                    "source_type": site.get("source_type", config_file)
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
    except FileNotFoundError:
        print("patterns.json tapılmadı. Pattern fallback keçiləcək.", flush=True)
        return {}
    except Exception as e:
        print(f"patterns.json oxunmadı: {e}", flush=True)
        return {}


def keyword_match(title, keywords):
    default_keywords = [
        "təhsil", "məktəb", "şagird", "müəllim",
        "universitet", "imtahan", "tələbə",
        "elm", "araşdırma", "tədqiqat",
        "akademik", "laboratoriya",
        "abituriyent", "kollec",
        "lisey", "steam", "pisa",
        "doktorant", "magistr",
        "tədris", "elmi", "institut",
        "sertifikasiya", "sertifikatlaşdırma",
        "dim", "tkta", "məktəbəqədər"
    ]

    all_keywords = keywords if keywords else default_keywords
    title_lower = title.lower()

    return any(str(keyword).lower() in title_lower for keyword in all_keywords)


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

    if any(word in title_lower for word in bad_words):
        return True

    if any(domain in link_lower for domain in bad_domains):
        return True

    if any(link_lower.endswith(ext) for ext in bad_extensions):
        return True

    parsed = urlparse(link_lower)
    path = parsed.path.strip("/").lower()

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

    if "/category/" in path or "/kateqoriya/" in path:
        return True

    return False


def get_google_news_time(entry):
    try:
        if hasattr(entry, "published_parsed") and entry.published_parsed:
            dt = datetime(*entry.published_parsed[:6])
            return dt.strftime("%d.%m.%Y | %H:%M")
    except Exception:
        pass

    return None


def google_news_fallback(site_url, keywords):
    domain = get_domain(site_url)
    results = []

    if not keywords:
        keywords = [
            "təhsil", "məktəb", "şagird", "müəllim",
            "universitet", "imtahan", "tələbə", "elm"
        ]

    for keyword in keywords:
        query = f"site:{domain} {keyword}"

        rss_url = (
            "https://news.google.com/rss/search?"
            f"q={quote_plus(query)}"
            "&hl=az"
            "&gl=AZ"
            "&ceid=AZ:az"
        )

        feed = feedparser.parse(rss_url)

        for entry in feed.entries[:5]:
            title = clean_text(entry.get("title", ""))
            link = entry.get("link", "")
            published_time = get_google_news_time(entry)

            if not title or not link:
                continue

            if exists(link):
                continue

            if is_bad_link(title, link):
                continue

            if not keyword_match(title, keywords):
                continue

            if not published_time:
                continue

            if not is_recent_news(published_time):
                continue

            results.append({
                "title": title,
                "link": link,
                "source": domain,
                "published_time": published_time
            })

    return results


def extract_publish_time_from_article(article_url):
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "az-AZ,az;q=0.9,en-US;q=0.8,en;q=0.7",
        "Referer": "https://www.google.com/",
        "Connection": "keep-alive"
    }

    try:
        response = requests.get(article_url, headers=headers, timeout=10)
        response.encoding = response.apparent_encoding
        tree = html.fromstring(response.text)

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

        for xpath in possible_xpaths:
            result = tree.xpath(xpath)

            if result:
                value = clean_text(str(result[0]))

                if len(value) > 5:
                    return value

        return None

    except Exception as e:
        print("Tarix çıxarma xətası:", e, flush=True)
        return None


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

        difference = datetime.now() - dt

        if difference.total_seconds() < 0:
            return False

        hours = difference.total_seconds() / 3600
        print(f"Tarix yoxlanır: {published_time} | fərq: {hours:.1f} saat", flush=True)

        return difference <= timedelta(hours=NEWS_TIME_LIMIT_HOURS)

    except Exception as e:
        print(f"Tarix yoxlama xətası: {published_time} | {e}", flush=True)
        return False


def extract_links_from_xpath(page_url, page_html, xpaths, keywords):
    results = []

    if not xpaths:
        return []

    try:
        tree = html.fromstring(page_html)
    except Exception as e:
        print("HTML parse xətası:", e, flush=True)
        return []

    for xpath in xpaths:
        try:
            blocks = tree.xpath(xpath)
        except Exception as e:
            print("XPath xətası:", e, flush=True)
            continue

        print(f"XPath üzrə blok sayı: {len(blocks)}", flush=True)

        for block in blocks:
            try:
                if hasattr(block, "tag") and block.tag == "a":
                    links = [block]
                else:
                    links = block.xpath(".//a[@href]")
            except Exception as e:
                print("Link çıxarma xətası:", e, flush=True)
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
        for a in block.find_all("a", href=True):
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
        "/hadise/", "/politic/", "/world/", "/economy/"
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

        link_lower = link.lower()

        if not any(pattern in link_lower for pattern in article_patterns):
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
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "Accept-Language": "az-AZ,az;q=0.9,en-US;q=0.8,en;q=0.7",
        "Connection": "keep-alive",
        "Referer": "https://www.google.com/"
    }

    try:
        print(f"Sayt açılır: {page_url}", flush=True)

        response = requests.get(page_url, headers=headers, timeout=12)
        print(f"Status: {response.status_code}", flush=True)

        if response.status_code in [403, 500, 502, 503, 504]:
            print("Google News fallback işləyir...", flush=True)
            return google_news_fallback(page_url, keywords)

        if response.status_code != 200:
            return []

        response.encoding = response.apparent_encoding

    except Exception as e:
        print(f"Sayt xətası: {page_url} | {e}", flush=True)
        return google_news_fallback(page_url, keywords)

    page_html = response.text
    domain = get_domain(page_url)
    site_patterns = patterns_data.get(domain, [])

    items = []

    if selector:
        items = extract_links_by_selector(page_url, page_html, selector, keywords)

    elif xpaths:
        items = extract_links_from_xpath(page_url, page_html, xpaths, keywords)

    elif site_patterns:
        print(f"Pattern fallback işləyir: {domain} | {site_patterns}", flush=True)
        items = extract_links_by_patterns(page_url, page_html, keywords, site_patterns)

    if not items:
        print("HTML fallback işləyir...", flush=True)
        items = extract_links_fallback(page_url, page_html, keywords)

    if not items:
        print("Google News fallback işləyir...", flush=True)
        items = google_news_fallback(page_url, keywords)

    return unique_items(items)


def check_sites(first_run=False):
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

            published_time = item.get("published_time")

            if not published_time:
                published_time = extract_publish_time_from_article(link)

            # Discovery/pattern ilə tapılan saytlarda bəzi xəbərlərin tarixini çıxarmaq olmur.
            # Belə hallarda link bazada yoxdursa, test və praktik istifadə üçün göndəririk.
            # Köhnə linklər isə bazada saxlandığı üçün təkrar getməyəcək.
            allow_undated = (
                site.get("source_type") == "discovered_rss_or_sitemap"
                or site.get("source_type") == "discovered_google_news"
                or get_domain(site.get("url", "")) in patterns_data
            )

            if not published_time:
                if allow_undated:
                    published_time = "Tarix tapılmadı"
                    print(f"Tarix tapılmadı, yeni link kimi qəbul edildi: {title[:70]}", flush=True)
                else:
                    print(f"Tarix tapılmadı, xəbər keçildi: {title[:70]}", flush=True)
                    continue

            if published_time != "Tarix tapılmadı" and not is_recent_news(published_time):
                print(f"Köhnə xəbər keçildi: {title[:70]} | {published_time}", flush=True)
                continue

            if first_run:
                save(link, title, source)
                print(f"İlkin bazaya yazıldı: {source} | {title[:70]}", flush=True)
                sent_for_this_site = True
                break

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

            time.sleep(2)
            break

        if not sent_for_this_site:
            print("Bu saytda yeni uyğun xəbər yoxdur.", flush=True)

        if sent_count >= MAX_SEND_PER_RUN:
            print("Bu dövr üçün göndərmə limiti tamamlandı.", flush=True)
            return


print("🚀 Sayt monitorinq botu işə düşdü.", flush=True)
print("🚀 Bot birbaşa monitorinq rejimində işə düşdü.", flush=True)
send_telegram("✅ Bot işə düşdü və saytları yoxlamağa başladı.")

while True:
    print("🔎 Yeni xəbərlər yoxlanılır...", flush=True)
    check_sites(first_run=False)
    time.sleep(CHECK_INTERVAL_SECONDS)
