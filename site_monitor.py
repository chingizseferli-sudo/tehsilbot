import argparse
import json
import os
import re
import sqlite3
import time
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from email.utils import parsedate_to_datetime
from urllib.parse import urljoin, urlparse
from zoneinfo import ZoneInfo

import feedparser
import requests
from bs4 import BeautifulSoup
from dateutil import parser as date_parser
from lxml import html

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
CHAT_ID = os.getenv("CHAT_ID", "").strip()

CONFIG_FILES = ["courier_config_clean.json", "discovered_sites.json"]
PATTERNS_FILE = "patterns.json"
KEYWORDS_FILE = "keywords.json"
DB_FILE = os.getenv("DB_FILE", "news.db")
HEALTH_FILE = "site_health.json"
MAX_SITE_FAILS = int(os.getenv("MAX_SITE_FAILS", "3"))

BAKU_TZ = ZoneInfo("Asia/Baku")
REQUEST_TIMEOUT = 8
NEWS_TIME_LIMIT_MINUTES = int(os.getenv("NEWS_TIME_LIMIT_MINUTES", "60"))
MAX_SEND_PER_RUN = int(os.getenv("MAX_SEND_PER_RUN", "25"))
MAX_CANDIDATES_PER_SITE = int(os.getenv("MAX_CANDIDATES_PER_SITE", "5"))
MAX_WORKERS = int(os.getenv("MAX_WORKERS", "10"))

DB_LOCK = threading.Lock()
HEALTH_LOCK = threading.Lock()
TELEGRAM_LOCK = threading.Lock()

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; TehsilBot/2.0; +https://example.com/bot)",
    "Accept-Language": "az-AZ,az;q=0.9,en-US;q=0.8,en;q=0.7",
    "Referer": "https://www.google.com/",
}

STRICT_WORDS = {
    "dim", "tkta", "pisa", "timss", "pirls", "bağça", "lisey", "kollec",
    "rektor", "dekan", "magistr", "ARTİ", "Arti", "miq", "arti", "MİQ", "doktorant", "abituriyent", "tələbə",
    "şagird", "müəllim", "məktəb", "sinif", "dərs", "elm", "steam",
}

BAD_TITLE_WORDS = [
    "ana səhifə", "haqqımızda", "haqqinda", "əlaqə", "elaqe", "reklam",
    "giriş", "qeydiyyat", "axtarış", "axtaris", "abunə", "bütün xəbərlər",
    "daha çox", "daha ətraflı", "arxiv", "kateqoriya", "menyu", "menu",
]

BAD_DOMAINS = [
    "facebook.com", "instagram.com", "youtube.com", "youtu.be", "t.me",
    "twitter.com", "x.com", "linkedin.com", "whatsapp.com",
]

BAD_EXTENSIONS = [
    ".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg", ".pdf", ".doc",
    ".docx", ".xls", ".xlsx", ".zip", ".rar", ".mp4", ".mp3", ".avi",
]

ARTICLE_PATTERNS = [
    "/news/", "/xeber/", "/xeberler/", "/xəbərlər/", "/az/news/", "/az/xeber/",
    "/az/xeberler/", "/az/xəbərlər/", "/post/", "/article/", "/read/",
    "/item/", "/son-xeber/", "/sosial/", "/resmi-xeber/", "/education/",
    "/elm/", "/tehsil/", "/2024/", "/2025/", "/2026/",
]

SECTION_PATHS = {
    "", "news", "xeber", "xeberler", "xəbərlər", "media", "media/news",
    "category", "kateqoriya", "archive", "arxiv", "allnews", "all-news",
    "newsarchive", "latest", "lastnews", "son-xeberler", "az/news",
    "az/xeber", "az/xeberler", "az/xəbərlər", "p/news", "tehsil", "elm",
    "elm-ve-tehsil",
}


def now_baku() -> datetime:
    return datetime.now(BAKU_TZ)


def clean_text(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def normalize_text(text: str) -> str:
    return clean_text(text).lower().replace("i̇", "i")


def get_domain(url: str) -> str:
    domain = urlparse(url).netloc.lower().strip()
    if domain.startswith("www."):
        domain = domain[4:]
    return domain


def normalize_url(url: str) -> str:
    url = clean_text(url).split("#")[0].strip()
    return url.rstrip("/")


def read_json(filename: str, default):
    try:
        with open(filename, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        print(f"Fayl tapılmadı: {filename}", flush=True)
        return default
    except Exception as e:
        print(f"JSON oxunmadı: {filename} | {e}", flush=True)
        return default


def write_json(filename: str, data):
    try:
        with open(filename, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"JSON yazılmadı: {filename} | {e}", flush=True)


def load_health() -> dict:
    data = read_json(HEALTH_FILE, {})
    return data if isinstance(data, dict) else {}


def save_health(health: dict):
    write_json(HEALTH_FILE, health)


def deactivate_site_in_files(domain: str, reason: str):
    changed_files = []

    for filename in CONFIG_FILES:
        data = read_json(filename, {"sites": []})
        if not isinstance(data, dict) or not isinstance(data.get("sites"), list):
            continue

        changed = False
        for site in data.get("sites", []):
            url = clean_text(site.get("url", ""))
            if url and get_domain(url) == domain and site.get("enabled", True):
                site["enabled"] = False
                site["disabled_reason"] = reason
                site["disabled_at"] = now_baku().isoformat()
                changed = True

        if changed:
            write_json(filename, data)
            changed_files.append(filename)

    if changed_files:
        print(f"❌ Sayt avtomatik deaktiv edildi: {domain} | fayllar: {', '.join(changed_files)}", flush=True)


def record_site_success(domain: str):
    if not domain:
        return

    with HEALTH_LOCK:
        health = load_health()
        item = health.get(domain, {})
        if item.get("fails", 0) or item.get("disabled"):
            print(f"✅ Sayt bərpa olundu: {domain}", flush=True)

        health[domain] = {
            "fails": 0,
            "disabled": False,
            "last_success": now_baku().isoformat(),
            "last_error": None,
        }
        save_health(health)


def record_site_failure(domain: str, url: str, error: str):
    if not domain:
        return

    should_deactivate = False
    reason = ""

    with HEALTH_LOCK:
        health = load_health()
        item = health.get(domain, {})
        fails = int(item.get("fails", 0)) + 1

        item.update({
            "fails": fails,
            "disabled": fails >= MAX_SITE_FAILS,
            "last_fail": now_baku().isoformat(),
            "last_url": url,
            "last_error": clean_text(error)[:300],
        })
        health[domain] = item
        save_health(health)

        print(f"⏩ {domain} açılmadı ({fails}/{MAX_SITE_FAILS}) | {error}", flush=True)

        if fails >= MAX_SITE_FAILS:
            should_deactivate = True
            reason = f"{fails} ardıcıl açılma xətası: {clean_text(error)[:150]}"

    if should_deactivate:
        deactivate_site_in_files(domain, reason)


def init_db():
    conn = sqlite3.connect(DB_FILE, check_same_thread=False)
    conn.execute("""
    CREATE TABLE IF NOT EXISTS sent_news (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        link TEXT UNIQUE NOT NULL,
        title TEXT,
        source TEXT,
        published_at TEXT,
        sent_at TEXT NOT NULL
    )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_sent_news_link ON sent_news(link)")
    conn.commit()
    return conn


DB = init_db()


def was_sent(link: str) -> bool:
    with DB_LOCK:
        row = DB.execute("SELECT 1 FROM sent_news WHERE link=?", (normalize_url(link),)).fetchone()
        return row is not None


def mark_sent(link: str, title: str, source: str, published_at: datetime | None):
    with DB_LOCK:
        DB.execute(
            "INSERT OR IGNORE INTO sent_news(link, title, source, published_at, sent_at) VALUES (?, ?, ?, ?, ?)",
            (
                normalize_url(link),
                clean_text(title),
                source,
                published_at.isoformat() if published_at else None,
                now_baku().isoformat(),
            ),
        )
        DB.commit()


def load_global_keywords() -> list[str]:
    data = read_json(KEYWORDS_FILE, {"keywords": []})
    keywords = data.get("keywords", []) if isinstance(data, dict) else []
    return [normalize_text(k) for k in keywords if clean_text(k)]


GLOBAL_KEYWORDS = load_global_keywords()


def extract_keywords_from_site(site: dict) -> list[str]:
    """
    YEKUN QAYDA:
    Bot yalnız keywords.json faylındakı təmiz açar sözlərə baxır.
    courier_config_clean.json və discovered_sites.json içində qalmış köhnə/parazit
    açar sözlər, məsələn "qəbul", "konfrans", "tədbir", nəzərə alınmır.
    """
    return sorted(set(GLOBAL_KEYWORDS), key=len, reverse=True)

def load_sites() -> list[dict]:
    sites = []
    seen_domains_urls = set()

    for filename in CONFIG_FILES:
        data = read_json(filename, {"sites": []})
        for site in data.get("sites", []) if isinstance(data, dict) else []:
            if not site.get("enabled", True):
                continue

            url = clean_text(site.get("url", ""))
            if not url.startswith("http"):
                continue

            normalized = normalize_url(url).lower()
            if normalized in seen_domains_urls:
                continue
            seen_domains_urls.add(normalized)

            xpaths = list(site.get("xpaths", []) or [])
            for selector_item in site.get("selectors", []) or []:
                if selector_item.get("type") == "xpath" and selector_item.get("value"):
                    xpaths.append(selector_item["value"])

            sites.append({
                "name": clean_text(site.get("name")) or get_domain(url),
                "url": url,
                "domain": get_domain(url),
                "selector": site.get("selector"),
                "xpaths": list(dict.fromkeys(xpaths)),
                "keywords": extract_keywords_from_site(site),
            })

    print(f"Yüklənən aktiv sayt sayı: {len(sites)}", flush=True)
    return sites


def load_patterns() -> dict:
    data = read_json(PATTERNS_FILE, {})
    return data if isinstance(data, dict) else {}


def keyword_match(title: str, keywords: list[str]) -> tuple[bool, list[str]]:
    title_lower = normalize_text(title)
    matched = []
    word_chars = r"a-zA-Z0-9əöğüçıƏÖĞÜÇŞşİı"

    for keyword in keywords:
        if not keyword:
            continue
        if keyword in STRICT_WORDS:
            pattern = rf"(?<![{word_chars}]){re.escape(keyword)}(?![{word_chars}])"
            if re.search(pattern, title_lower, flags=re.IGNORECASE):
                matched.append(keyword)
        elif keyword in title_lower:
            matched.append(keyword)

    return bool(matched), matched[:8]


def is_section_url(link: str) -> bool:
    path = urlparse(link.lower()).path.strip("/")
    if path in SECTION_PATHS:
        return True
    parts = [p for p in path.split("/") if p]
    if len(parts) <= 1 and any(w in path for w in ["news", "xeber", "xəbər", "category", "archive", "media"]):
        return True
    return False


def is_article_like(link: str) -> bool:
    link_lower = link.lower()
    if any(link_lower.endswith(ext) for ext in BAD_EXTENSIONS):
        return False
    if any(domain in link_lower for domain in BAD_DOMAINS):
        return False
    if is_section_url(link):
        return False
    return any(pattern in link_lower for pattern in ARTICLE_PATTERNS)


def is_bad_candidate(title: str, link: str, page_domain: str) -> bool:
    title_lower = normalize_text(title)
    link_lower = link.lower()

    if len(title) < 12:
        return True
    if get_domain(link) != page_domain:
        return True
    if any(w in title_lower for w in BAD_TITLE_WORDS):
        return True
    if any(d in link_lower for d in BAD_DOMAINS):
        return True
    if any(link_lower.endswith(ext) for ext in BAD_EXTENSIONS):
        return True
    if not is_article_like(link):
        return True
    return False


def add_candidate(results: list[dict], page_url: str, title: str, link: str, keywords: list[str]):
    title = clean_text(title)
    link = normalize_url(urljoin(page_url, link))
    page_domain = get_domain(page_url)

    if not link.startswith("http") or is_bad_candidate(title, link, page_domain):
        return

    matched, matched_keywords = keyword_match(title, keywords)
    if not matched:
        return

    results.append({
        "title": title,
        "link": link,
        "source": page_domain,
        "matched_keywords": matched_keywords,
    })


def unique_candidates(items: list[dict]) -> list[dict]:
    seen = set()
    out = []
    for item in items:
        link = normalize_url(item.get("link", ""))
        if not link or link in seen:
            continue
        seen.add(link)
        out.append(item)
    return out


def fetch_html(session: requests.Session, url: str) -> tuple[str | None, str | None]:
    try:
        response = session.get(url, timeout=REQUEST_TIMEOUT, allow_redirects=True)
        print(f"Sayt açılır: {url} | status: {response.status_code}", flush=True)

        if response.status_code != 200:
            return None, f"HTTP status {response.status_code}"

        response.encoding = response.apparent_encoding
        return response.text, None

    except requests.exceptions.RequestException as e:
        return None, str(e)

    except Exception as e:
        return None, f"Naməlum xəta: {e}"


def discover_rss_links(page_url: str, page_html: str) -> list[str]:
    soup = BeautifulSoup(page_html, "html.parser")
    rss_links = []
    for tag in soup.find_all("link", href=True):
        tag_type = (tag.get("type") or "").lower()
        title = (tag.get("title") or "").lower()
        if "rss" in tag_type or "atom" in tag_type or "rss" in title:
            rss_links.append(urljoin(page_url, tag["href"]))
    for path in ["/rss", "/rss.xml", "/feed", "/feed.xml", "/az/rss", "/az/rss.xml"]:
        root = f"{urlparse(page_url).scheme}://{urlparse(page_url).netloc}"
        rss_links.append(urljoin(root, path))
    return list(dict.fromkeys(rss_links))[:5]


def extract_from_rss(session: requests.Session, site: dict, page_html: str) -> list[dict]:
    results = []
    for rss_url in discover_rss_links(site["url"], page_html):
        try:
            raw = session.get(rss_url, timeout=REQUEST_TIMEOUT).text
            feed = feedparser.parse(raw)
            if not feed.entries:
                continue
            print(f"RSS tapıldı: {rss_url} | xəbər sayı: {len(feed.entries)}", flush=True)
            for entry in feed.entries[:MAX_CANDIDATES_PER_SITE]:
                title = clean_text(entry.get("title", ""))
                link = entry.get("link", "")
                add_candidate(results, site["url"], title, link, site["keywords"])
                if results:
                    results[-1]["rss_published"] = entry.get("published") or entry.get("updated")
        except Exception:
            continue
    return unique_candidates(results)


def extract_by_xpath(site: dict, page_html: str) -> list[dict]:
    results = []
    if not site.get("xpaths"):
        return results
    try:
        tree = html.fromstring(page_html)
    except Exception:
        return results
    for xp in site["xpaths"]:
        try:
            blocks = tree.xpath(xp)
        except Exception as e:
            print(f"XPath xətası: {xp} | {e}", flush=True)
            continue
        for block in blocks:
            try:
                links = [block] if getattr(block, "tag", None) == "a" else block.xpath(".//a[@href]")
            except Exception:
                continue
            for a in links:
                add_candidate(results, site["url"], a.text_content(), a.get("href"), site["keywords"])
                if len(results) >= MAX_CANDIDATES_PER_SITE:
                    return unique_candidates(results)
    return unique_candidates(results)


def extract_by_selector(site: dict, page_html: str) -> list[dict]:
    selector = site.get("selector")
    if not selector:
        return []
    soup = BeautifulSoup(page_html, "html.parser")
    results = []
    try:
        blocks = soup.select(selector)
    except Exception as e:
        print(f"Selector xətası: {selector} | {e}", flush=True)
        return []
    for block in blocks:
        links = block.find_all("a", href=True)
        if getattr(block, "name", None) == "a" and block.get("href"):
            links.append(block)
        for a in links:
            add_candidate(results, site["url"], a.get_text(" ", strip=True), a["href"], site["keywords"])
            if len(results) >= MAX_CANDIDATES_PER_SITE:
                return unique_candidates(results)
    return unique_candidates(results)


def extract_by_patterns(site: dict, page_html: str, patterns_data: dict) -> list[dict]:
    patterns = patterns_data.get(site["domain"], []) or []
    if not patterns:
        return []
    soup = BeautifulSoup(page_html, "html.parser")
    results = []
    for a in soup.find_all("a", href=True):
        link = urljoin(site["url"], a["href"])
        if not any(pattern.lower() in link.lower() for pattern in patterns):
            continue
        add_candidate(results, site["url"], a.get_text(" ", strip=True), link, site["keywords"])
        if len(results) >= MAX_CANDIDATES_PER_SITE:
            break
    return unique_candidates(results)


def extract_fallback(site: dict, page_html: str) -> list[dict]:
    soup = BeautifulSoup(page_html, "html.parser")
    results = []
    for a in soup.find_all("a", href=True):
        add_candidate(results, site["url"], a.get_text(" ", strip=True), a["href"], site["keywords"])
        if len(results) >= MAX_CANDIDATES_PER_SITE:
            break
    return unique_candidates(results)


AZ_MONTHS = {
    "yanvar": 1, "fevral": 2, "mart": 3, "aprel": 4, "may": 5, "iyun": 6,
    "iyul": 7, "avqust": 8, "sentyabr": 9, "oktyabr": 10, "noyabr": 11, "dekabr": 12,
    "yan": 1, "fev": 2, "mar": 3, "apr": 4, "iyn": 6, "iyl": 7, "avq": 8,
    "sen": 9, "okt": 10, "noy": 11, "dek": 12,
}


def parse_az_datetime(value: str | None) -> datetime | None:
    value = clean_text(value).lower()
    if not value:
        return None

    value = value.replace("—", "-").replace("–", "-")

    patterns = [
        r"(\d{1,2})\s+([a-zəöğıçşü]+)\s+(\d{4})\s*[,\-]?\s*(\d{1,2})[:.](\d{2})",
        r"(\d{1,2})[.\-/](\d{1,2})[.\-/](\d{4})\s*[,\-]?\s*(\d{1,2})[:.](\d{2})",
        r"(\d{1,2})[:.](\d{2})\s*[,\-]?\s*(\d{1,2})\s+([a-zəöğıçşü]+)\s*,?\s*(\d{4})",
        r"(\d{1,2})[:.](\d{2})\s*[,\-]?\s*(\d{1,2})[.\-/](\d{1,2})[.\-/](\d{4})",
    ]

    for idx, pattern in enumerate(patterns):
        match = re.search(pattern, value, re.IGNORECASE)
        if not match:
            continue
        try:
            groups = match.groups()
            if idx == 0:
                day, month_name, year, hour, minute = groups
                month = AZ_MONTHS.get(month_name.lower())
            elif idx == 1:
                day, month, year, hour, minute = groups
                month = int(month)
            elif idx == 2:
                hour, minute, day, month_name, year = groups
                month = AZ_MONTHS.get(month_name.lower())
            else:
                hour, minute, day, month, year = groups
                month = int(month)

            if not month:
                continue

            return datetime(
                int(year), int(month), int(day), int(hour), int(minute), tzinfo=BAKU_TZ
            )
        except Exception:
            continue

    date_only_patterns = [
        r"(\d{1,2})\s+([a-zəöğıçşü]+)\s+(\d{4})",
        r"(\d{1,2})[.\-/](\d{1,2})[.\-/](\d{4})",
    ]

    for idx, pattern in enumerate(date_only_patterns):
        match = re.search(pattern, value, re.IGNORECASE)
        if not match:
            continue
        try:
            groups = match.groups()
            if idx == 0:
                day, month_name, year = groups
                month = AZ_MONTHS.get(month_name.lower())
            else:
                day, month, year = groups
                month = int(month)

            if not month:
                continue

            return datetime(int(year), int(month), int(day), 0, 0, tzinfo=BAKU_TZ)
        except Exception:
            continue

    return None


def parse_datetime(value: str | None) -> datetime | None:
    value = clean_text(value)
    if not value:
        return None

    az_dt = parse_az_datetime(value)
    if az_dt:
        return az_dt

    try:
        try:
            dt = parsedate_to_datetime(value)
        except Exception:
            dt = date_parser.parse(value, fuzzy=True, dayfirst=True)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=BAKU_TZ)
        else:
            dt = dt.astimezone(BAKU_TZ)
        return dt
    except Exception:
        return None


def choose_best_datetime(title_dt: datetime | None, article_dt: datetime | None) -> datetime | None:
    if title_dt and article_dt:
        if article_dt.hour == 0 and article_dt.minute == 0 and (title_dt.hour != 0 or title_dt.minute != 0):
            return title_dt
        return article_dt
    return article_dt or title_dt


def extract_publish_time_from_article(session: requests.Session, article_url: str, rss_published: str | None = None) -> datetime | None:
    rss_dt = parse_datetime(rss_published)
    if rss_dt:
        return rss_dt

    try:
        response = session.get(article_url, timeout=REQUEST_TIMEOUT, allow_redirects=True)
        if response.status_code != 200:
            return None
        response.encoding = response.apparent_encoding
        tree = html.fromstring(response.text)
        soup = BeautifulSoup(response.text, "html.parser")

        xpaths = [
            "//meta[@property='article:published_time']/@content",
            "//meta[@name='article:published_time']/@content",
            "//meta[@itemprop='datePublished']/@content",
            "//meta[@name='pubdate']/@content",
            "//meta[@name='date']/@content",
            "//meta[@name='DC.date.issued']/@content",
            "//meta[@name='publishdate']/@content",
            "//meta[@name='publish_date']/@content",
            "//time/@datetime",
            "//time/text()",
            "//*[contains(@class,'date')]/@datetime",
            "//*[contains(@class,'date')]/text()",
            "//*[contains(@class,'time')]/text()",
        ]
        for xp in xpaths:
            try:
                values = tree.xpath(xp)
            except Exception:
                continue
            for value in values[:3]:
                dt = parse_datetime(str(value))
                if dt:
                    return dt

        scripts = soup.find_all("script", type=lambda t: t and "ld+json" in t.lower())
        for script in scripts:
            try:
                data = json.loads(script.get_text(" ", strip=True))
                stack = data if isinstance(data, list) else [data]
                for obj in stack:
                    if isinstance(obj, dict):
                        for key in ["datePublished", "dateCreated", "uploadDate"]:
                            dt = parse_datetime(obj.get(key))
                            if dt:
                                return dt
            except Exception:
                continue
    except Exception as e:
        print(f"Tarix çıxarma xətası: {article_url} | {e}", flush=True)
    return None


def is_recent_today(dt: datetime | None) -> bool:
    if not dt:
        return False
    now = now_baku()
    if dt.date() != now.date():
        return False
    diff = now - dt
    if diff.total_seconds() < 0:
        return False
    return diff <= timedelta(minutes=NEWS_TIME_LIMIT_MINUTES)


def send_telegram(message: str) -> bool:
    if not BOT_TOKEN or not CHAT_ID:
        print("BOT_TOKEN və ya CHAT_ID yoxdur. Telegram göndərilmədi.", flush=True)
        return False

    api_url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    try:
        with TELEGRAM_LOCK:
            response = requests.post(
                api_url,
            data={
                "chat_id": CHAT_ID,
                "text": message,
                "disable_web_page_preview": False,
            },
                timeout=20,
            )
        print(f"Telegram status: {response.status_code} | {response.text[:200]}", flush=True)
        if response.status_code == 429:
            retry_after = response.json().get("parameters", {}).get("retry_after", 30)
            time.sleep(int(retry_after) + 2)
            return False
        if response.status_code == 400 and "migrate_to_chat_id" in response.text:
            print("Telegram qrupu supergroup-a keçib. CHAT_ID-ni migrate_to_chat_id ilə yenilə.", flush=True)
        return response.status_code == 200
    except Exception as e:
        print(f"Telegram xətası: {e}", flush=True)
        return False


def build_message(item: dict, published_dt: datetime) -> str:
    matched = ", ".join(item.get("matched_keywords", [])[:6]) or "uyğun açar söz"
    return f"""🆕 Yeni təhsil xəbəri

📌 {item['title']}

🌐 Mənbə: {item['source']}
🔎 Açar söz: {matched}
🕒 Dərc olunub: {published_dt.strftime('%d.%m.%Y %H:%M')} AZT

🔗 {item['link']}"""


def collect_candidates(session: requests.Session, site: dict, patterns_data: dict) -> tuple[list[dict], bool, str | None]:
    page_html, fetch_error = fetch_html(session, site["url"])
    if not page_html:
        return [], False, fetch_error

    methods = [
        ("selector", lambda: extract_by_selector(site, page_html)),
        ("xpath", lambda: extract_by_xpath(site, page_html)),
        ("rss", lambda: extract_from_rss(session, site, page_html)),
        ("patterns", lambda: extract_by_patterns(site, page_html, patterns_data)),
        ("fallback", lambda: extract_fallback(site, page_html)),
    ]

    merged = []
    for method_name, method in methods:
        items = method()
        if items:
            print(f"{site['domain']} | {method_name} ilə uyğun namizəd: {len(items)}", flush=True)
            merged.extend(items)
        if len(merged) >= MAX_CANDIDATES_PER_SITE:
            break

    return unique_candidates(merged)[:MAX_CANDIDATES_PER_SITE], True, None


def process_site(index: int, total: int, site: dict, patterns_data: dict) -> int:
    started_at = time.time()
    print(f"[{index}/{total}] Yoxlanır: {site['name']} | {site['url']}", flush=True)

    session = requests.Session()
    session.headers.update(HEADERS)

    candidates, site_opened, fetch_error = collect_candidates(session, site, patterns_data)

    if not site_opened:
        record_site_failure(site["domain"], site["url"], fetch_error or "Sayt açılmadı")
        elapsed = time.time() - started_at
        print(
            f"📊 [{index}/{total}] {site['domain']} | status=açılmadı | namizəd=0 | göndərildi=0 | vaxt={elapsed:.1f}s",
            flush=True,
        )
        return 0

    record_site_success(site["domain"])
    print(f"{site['domain']} | uyğun namizəd sayı: {len(candidates)}", flush=True)

    if not candidates:
        elapsed = time.time() - started_at
        print(
            f"📊 [{index}/{total}] {site['domain']} | status=açıldı | namizəd=0 | nəticə=açar sözə uyğun xəbər yoxdur | vaxt={elapsed:.1f}s",
            flush=True,
        )
        return 0

    duplicate_count = 0
    old_count = 0
    checked_count = 0
    no_date_count = 0

    for item in candidates:
        checked_count += 1

        if was_sent(item["link"]):
            duplicate_count += 1
            print(f"Təkrar xəbər keçildi: {item['link']}", flush=True)
            continue

        title_dt = parse_datetime(item.get("title"))
        article_dt = extract_publish_time_from_article(session, item["link"], item.get("rss_published"))
        published_dt = choose_best_datetime(title_dt, article_dt)
        print(
            f"Namizəd: {item['title'][:80]} | title_tarix: {title_dt} | article_tarix: {article_dt} | seçilən: {published_dt}",
            flush=True,
        )

        if not published_dt:
            no_date_count += 1
            print("Tarix tapılmadı, keçildi.", flush=True)
            continue

        if not is_recent_today(published_dt):
            old_count += 1
            print("Bugünkü son 1 saat xəbəri deyil, keçildi.", flush=True)
            continue

        # İki paralel worker eyni xəbəri eyni anda göndərməsin deyə burada yenidən yoxlayırıq.
        if was_sent(item["link"]):
            duplicate_count += 1
            print(f"Təkrar xəbər keçildi: {item['link']}", flush=True)
            continue

        message = build_message(item, published_dt)
        if send_telegram(message):
            mark_sent(item["link"], item["title"], item["source"], published_dt)
            elapsed = time.time() - started_at
            print(f"✅ Göndərildi və bazaya yazıldı: {item['source']} | {item['title'][:80]}", flush=True)
            print(
                f"📊 [{index}/{total}] {site['domain']} | namizəd={len(candidates)} | yoxlandı={checked_count} | təkrar={duplicate_count} | köhnə={old_count} | tarixsiz={no_date_count} | göndərildi=1 | vaxt={elapsed:.1f}s",
                flush=True,
            )
            return 1

        print("Telegram göndərilmədi; bazaya yazılmadı.", flush=True)

    elapsed = time.time() - started_at
    print("Bu saytdan göndəriləcək yeni son xəbər yoxdur.", flush=True)
    print(
        f"📊 [{index}/{total}] {site['domain']} | namizəd={len(candidates)} | yoxlandı={checked_count} | təkrar={duplicate_count} | köhnə={old_count} | tarixsiz={no_date_count} | göndərildi=0 | vaxt={elapsed:.1f}s",
        flush=True,
    )
    return 0

def check_sites(once_limit_sites: int | None = None) -> int:
    run_started_at = time.time()
    sites = load_sites()
    patterns_data = load_patterns()

    if once_limit_sites:
        sites = sites[:once_limit_sites]

    print(
        f"Monitorinq başladı | paralel işçi: {MAX_WORKERS} | son {NEWS_TIME_LIMIT_MINUTES} dəqiqə | {now_baku().strftime('%d.%m.%Y %H:%M:%S')} AZT",
        flush=True,
    )

    sent_count = 0
    completed_count = 0
    error_count = 0
    max_workers = max(1, min(MAX_WORKERS, len(sites) or 1))

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(process_site, index, len(sites), site, patterns_data): site
            for index, site in enumerate(sites, start=1)
        }

        for future in as_completed(futures):
            site = futures[future]
            try:
                sent_count += int(future.result() or 0)
                completed_count += 1
            except Exception as e:
                error_count += 1
                print(f"⚠️ Worker xətası: {site.get('domain')} | {e}", flush=True)

            if sent_count >= MAX_SEND_PER_RUN:
                print("Bu dövr üçün göndərmə limiti tamamlandı. Qalan worker-lər tamamlananda dövr bağlanacaq.", flush=True)
                # Artıq başlamamış task-ları ləğv etməyə çalışırıq.
                for pending in futures:
                    if not pending.done():
                        pending.cancel()
                break

    elapsed = time.time() - run_started_at
    print("=" * 60, flush=True)
    print("📈 MONİTORİNQ YEKUNU", flush=True)
    print(f"🌐 Aktiv sayt sayı: {len(sites)}", flush=True)
    print(f"✅ Tamamlanan sayt sayı: {completed_count}", flush=True)
    print(f"📤 Göndərilən xəbər sayı: {sent_count}", flush=True)
    print(f"⚠️ Worker xətası: {error_count}", flush=True)
    print(f"⚙️ Paralel işçi sayı: {max_workers}", flush=True)
    print(f"⏱️ Ümumi vaxt: {elapsed:.1f} saniyə", flush=True)
    print(f"🕒 Bitmə vaxtı: {now_baku().strftime('%d.%m.%Y %H:%M:%S')} AZT", flush=True)
    print("=" * 60, flush=True)
    return sent_count

def main():
    parser = argparse.ArgumentParser(description="Peşəkar TəhsilBot monitorinq sistemi")
    parser.add_argument("--once", action="store_true", help="Bir dəfə yoxla və dayan")
    parser.add_argument("--interval", type=int, default=600, help="Daimi rejimdə yoxlama intervalı, saniyə ilə")
    parser.add_argument("--limit-sites", type=int, default=None, help="Test üçün ilk N saytı yoxla")
    args = parser.parse_args()

    if args.once:
        check_sites(once_limit_sites=args.limit_sites)
        return

    print("🚀 TəhsilBot monitorinq sistemi işə düşdü", flush=True)
    while True:
        check_sites(once_limit_sites=args.limit_sites)
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
