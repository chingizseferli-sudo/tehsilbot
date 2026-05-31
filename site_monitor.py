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
from zoneinfo import ZoneInfo

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
KEYWORDS_FILE = "keywords.json"

BAKU_TZ = ZoneInfo("Asia/Baku")

STRICT_WORDS = {
    "dim",
    "tkta",
    "pisa",
    "timss",
    "pirls",
    "bağça",
    "lisey",
    "kollec",
    "rektor",
    "dekan",
    "arti",
    "miq",
    "magistr",
    "doktorant",
    "abituriyent",
    "tələbə",
    "şagird",
    "müəllim",
    "məktəb",
    "sinif",
    "dərs",
    "elm"
}

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


def load_keywords():
    try:
        with open(KEYWORDS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data.get("keywords", [])
    except Exception:
        return []


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


def normalize_text(text):
    text = str(text or "").lower()
    text = text.replace("i̇", "i")
    return text


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
        if item.get("link"):
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

                normalized_url = url.rstrip("/").lower()

                if normalized_url in seen_urls:
                    continue

                seen_urls.add(normalized_url)

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
                    "limit": min(int(site.get("limit", MAX_LINKS_PER_SITE)), MAX_LINKS_PER_SITE)
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
    except Exception:
        return {}


GLOBAL_KEYWORDS = load_keywords()


def keyword_match(title, keywords):
    title_lower = normalize_text(title)

    all_keywords = set()

    for k in GLOBAL_KEYWORDS:
        k = str(k).strip().lower()
        if k:
            all_keywords.add(k)

    for k in keywords:
        k = str(k).strip().lower()
        if k:
            all_keywords.add(k)

    matched_keywords = []
    word_chars = r"a-zA-Z0-9əöğüçıƏÖĞÜÇŞşİı"

    for keyword in sorted(all_keywords, key=len, reverse=True):
        keyword = normalize_text(keyword)

        if keyword in STRICT_WORDS:
            pattern = (
                rf"(?<![{word_chars}])"
                + re.escape(keyword)
                + rf"(?![{word_chars}])"
            )

            if re.search(pattern, title_lower, flags=re.IGNORECASE):
                matched_keywords.append(keyword)
        else:
            if keyword in title_lower:
                matched_keywords.append(keyword)

    if matched_keywords:
        return True, matched_keywords

    return False, []


def is_probably_section_url(link):
    path = urlparse(link.lower()).path.strip("/").lower()

    if not path:
        return True

    section_paths = [
        "news", "xeber", "xeberler", "xəbərlər",
        "media", "media/news",
        "category", "kateqoriya",
        "archive", "arxiv",
        "allnews", "all-news", "newsarchive",
        "latest", "lastnews", "son-xeberler",
        "az/news", "az/xeber", "az/xeberler", "az/xəbərlər",
        "az/metbuat/xeberler",
        "az/page/media/news",
        "az/news-and-updates",
        "p/news",
        "tehsil", "elm", "elm-ve-tehsil"
    ]

    if path in section_paths:
        return True

    bad_section_words = [
        "news", "xeber", "xeberler", "xəbərlər",
        "category", "kateqoriya",
        "archive", "arxiv",
        "latest", "lastnews",
        "allnews", "all-news",
        "son-xeberler",
        "media"
    ]

    parts = [p for p in path.split("/") if p]

    if len(parts) <= 1 and any(word in path for word in bad_section_words):
        return True

    if len(parts) <= 2 and any(path.endswith(word) for word in [
        "news", "xeber", "xeberler", "xəbərlər",
        "media/news", "allnews", "all-news",
        "latest", "lastnews", "son-xeberler",
        "category", "kateqoriya",
        "archive", "arxiv"
    ]):
        return True

    return False


def is_bad_link(title, link):
    title_lower = title.lower()
    link_lower = link.lower()

    bad_words = [
        "ana səhifə", "haqqımızda", "əlaqə", "reklam",
        "giriş", "qeydiyyat", "axtarış", "abunə",
        "facebook", "instagram", "youtube", "telegram",
        "twitter", "linkedin", "rss", "bütün xəbərlər",
        "daha çox", "arxiv", "kateqoriya", "bütün bölmələr",
        "menu", "menyu"
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

    if is_probably_section_url(link):
        return True

    return False


def parse_datetime_to_baku(published_time):
    try:
        text = str(published_time).strip().lower()

        if not text or "tarix tapılmadı" in text:
            return None

        dt = parser.parse(text, fuzzy=True, dayfirst=True)

        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=BAKU_TZ)
        else:
            dt = dt.astimezone(BAKU_TZ)

        return dt

    except Exception as e:
        print(f"Tarix parse xətası: {published_time} | {e}", flush=True)
        return None


def is_today_news(published_time):
    dt = parse_datetime_to_baku(published_time)

    if not dt:
        return False

    now_baku = datetime.now(BAKU_TZ)

    if dt.date() != now_baku.date():
        print(
            f"Bugünkü xəbər deyil, keçildi: {published_time} | bugün: {now_baku.date()}",
            flush=True
        )
        return False

    return True


def is_recent_news(published_time):
    try:
        dt = parse_datetime_to_baku(published_time)

        if not dt:
            return False

        now_baku = datetime.now(BAKU_TZ)

        if dt.date() != now_baku.date():
            print(
                f"Bugünkü xəbər deyil, keçildi: {published_time} | bugün: {now_baku.date()}",
                flush=True
            )
            return False

        diff = now_baku - dt

        if diff.total_seconds() < 0:
            print(f"Gələcək tarix kimi göründü, keçildi: {published_time}", flush=True)
            return False

        if diff.days > 0:
            print(f"Gün fərqi var, xəbər köhnədir: {published_time}", flush=True)
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

        # og:updated_time qəsdən istifadə olunmur.
        # Çünki çox vaxt xəbərin yayım tarixi yox, səhifənin yenilənmə tarixi olur.
        possible_xpaths = [
            "//time/@datetime",
            "//time/text()",
            "//meta[@property='article:published_time']/@content",
            "//meta[@name='article:published_time']/@content",
            "//meta[@itemprop='datePublished']/@content",
            "//meta[@name='pubdate']/@content",
            "//meta[@name='date']/@content",
            "//meta[@name='DC.date.issued']/@content",
            "//meta[@name='publishdate']/@content",
            "//meta[@name='publish_date']/@content",
            "//span[contains(@class,'date')]/text()",
            "//div[contains(@class,'date')]/text()",
            "//span[contains(@class,'time')]/text()",
            "//div[contains(@class,'time')]/text()",
            "//*[contains(@class,'date')]/text()",
            "//*[contains(@class,'time')]/text()"
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


def is_article_like_link(link):
    link_lower = link.lower()

    article_patterns = [
        "/news/", "/xeber/", "/xeberler/", "/xəbərlər/",
        "/az/news/", "/az/xeber/", "/az/xeberler/", "/az/xəbərlər/",
        "/post/", "/article/", "/read/", "/item/",
        "/son-xeber/", "/sosial/", "/resmi-xeber/",
        "/hadise/", "/politic/", "/world/", "/economy/",
        "/education/", "/elm/", "/tehsil/",
        "/2024/", "/2025/", "/2026/"
    ]

    return any(pattern in link_lower for pattern in article_patterns)


def add_item(results, page_url, title, link, keywords):
    title = clean_text(title)
    link = link.split("#")[0]

    if not title or not link.startswith("http"):
        return

    if get_domain(page_url) != get_domain(link):
        return

    if is_bad_link(title, link):
        return

    if not is_article_like_link(link):
        return

    matched, matched_keywords = keyword_match(title, keywords)

    if not matched:
        return

    results.append({
        "title": title,
        "link": link,
        "source": get_domain(page_url),
        "matched_keywords": matched_keywords
    })


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
            except Exception:
                continue

            for a in links:
                href = a.get("href")
                title = clean_text(a.text_content())
                link = urljoin(page_url, href)

                add_item(results, page_url, title, link, keywords)

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

        if getattr(block, "name", None) == "a" and block.get("href"):
            links.append(block)

        for a in links:
            title = clean_text(a.get_text(" ", strip=True))
            link = urljoin(page_url, a["href"])

            add_item(results, page_url, title, link, keywords)

    return unique_items(results)


def extract_links_by_patterns(page_url, page_html, keywords, patterns):
    soup = BeautifulSoup(page_html, "html.parser")
    results = []

    for a in soup.find_all("a", href=True):
        title = clean_text(a.get_text(" ", strip=True))
        link = urljoin(page_url, a["href"])

        if not any(pattern.lower() in link.lower() for pattern in patterns):
            continue

        add_item(results, page_url, title, link, keywords)

    return unique_items(results)


def extract_links_fallback(page_url, page_html, keywords):
    soup = BeautifulSoup(page_html, "html.parser")
    results = []

    for a in soup.find_all("a", href=True):
        title = clean_text(a.get_text(" ", strip=True))
        link = urljoin(page_url, a["href"])

        add_item(results, page_url, title, link, keywords)

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
            matched_keywords = item.get("matched_keywords", [])

            if exists(link):
                continue

            published_time = extract_publish_time_from_article(link)

            print(
                f"Xəbər: {title[:70]} | Çıxarılan tarix: {published_time} | Link: {link}",
                flush=True
            )

            if not published_time:
                print(f"Tarix tapılmadı, xəbər keçildi: {title[:70]}", flush=True)
                continue

            if not is_today_news(published_time):
                print(f"Bugünkü xəbər deyil, keçildi: {title[:70]} | {published_time}", flush=True)
                continue

            if not is_recent_news(published_time):
                print(f"Köhnə xəbər keçildi: {title[:70]} | {published_time}", flush=True)
                continue

            matched_keywords_text = ", ".join(matched_keywords) if matched_keywords else "Açar söz tapılmadı"

            message = f"""
🆕 Yeni uyğun xəbər

📌 Başlıq:
{title}

🌐 Mənbə:
{source}

🔎 Açar sözlər:
{matched_keywords_text}

🕒 Tarix və saat:
{published_time}

🔗 Link:
{link}
"""

            send_telegram(message)
            save(link, title, source)

            print(
                f"Göndərildi: {source} | {title[:70]} | Açar sözlər: {matched_keywords_text}",
                flush=True
            )

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
