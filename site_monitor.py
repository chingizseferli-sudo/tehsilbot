print("PYTHON STARTED", flush=True)

import json
import os
import re
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
from dateutil import parser
from lxml import html

HEALTH_FILE = "site_health.json"

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
CHAT_ID = os.getenv("CHAT_ID", "").strip()
SUPABASE_URL = os.getenv("SUPABASE_URL", "").strip().rstrip("/")
SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "").strip()

CHECK_INTERVAL_SECONDS = int(os.getenv("CHECK_INTERVAL_SECONDS", "60"))
MAX_SEND_PER_RUN = int(os.getenv("MAX_SEND_PER_RUN", "10"))
MAX_LINKS_PER_SITE = int(os.getenv("MAX_LINKS_PER_SITE", "10"))
NEWS_TIME_LIMIT_HOURS = int(os.getenv("NEWS_TIME_LIMIT_HOURS", "1"))
MAX_WORKERS = int(os.getenv("MAX_WORKERS", "10"))
REQUEST_TIMEOUT = int(os.getenv("REQUEST_TIMEOUT", "10"))

PATTERNS_FILE = "patterns.json"
KEYWORDS_FILE = "keywords.json"
BAKU_TZ = ZoneInfo("Asia/Baku")

STRICT_WORDS = {
    "dim", "tkta", "arti", "pisa", "timss", "pirls", "bağça", "magistr",
    "peşə", "elm", "miq", "diplom"
}

NEWS_CATEGORIES = {
    "sosial", "siyasət", "hadisə", "cəmiyyət", "iqtisadiyyat", "dünya",
    "ölkə", "təhsil", "elm", "mədəniyyət", "idman", "kriminal",
    "region", "bölgə", "maraqlı", "şou", "sağlamlıq", "texnologiya",
}

COMMON_LATEST_PATHS = [
    "/news", "/xeberler", "/xeber", "/az/news", "/az/xeberler", "/az/xeber",
    "/son-xeberler", "/latest", "/lastnews", "/gundem", "/cemiyyet",
    "/sosial", "/tehsil", "/elm", "/media", "/press", "/articles", "/posts",
]

COMMON_RSS_PATHS = [
    "/rss", "/rss.xml", "/feed", "/feed.xml", "/atom.xml",
    "/az/rss", "/az/rss.xml", "/az/feed", "/az/feed.xml",
    "/xeberler/rss", "/news/rss",
]

ARTICLE_URL_PATTERNS = [
    "/news/", "/xeber/", "/xeberler/", "/xəbərlər/", "/az/news/",
    "/az/xeber/", "/az/xeberler/", "/az/xəbərlər/", "/post/",
    "/article/", "/read/", "/item/", "/son-xeber/", "/sosial/",
    "/resmi-xeber/", "/hadise/", "/politic/", "/world/", "/economy/",
    "/education/", "/elm/", "/tehsil/", "/2024/", "/2025/", "/2026/",
]

DB_LOCK = threading.Lock()
TELEGRAM_LOCK = threading.Lock()


def supabase_headers(extra=None):
    headers = {
        "apikey": SUPABASE_SERVICE_ROLE_KEY,
        "Authorization": f"Bearer {SUPABASE_SERVICE_ROLE_KEY}",
        "Content-Type": "application/json",
    }
    if extra:
        headers.update(extra)
    return headers


def supabase_ready():
    if not SUPABASE_URL or not SUPABASE_SERVICE_ROLE_KEY:
        print("SUPABASE_URL və ya SUPABASE_SERVICE_ROLE_KEY yoxdur.", flush=True)
        return False
    return True


def clean_text(text):
    return re.sub(r"\s+", " ", str(text or "")).strip()


def normalize_text(text):
    text = str(text or "").lower()
    text = text.replace("i̇", "i")
    return text


def get_domain(url):
    return urlparse(url or "").netloc.replace("www.", "").lower()


def get_base_url(url):
    parsed = urlparse(url or "")
    if not parsed.scheme or not parsed.netloc:
        return ""
    return f"{parsed.scheme}://{parsed.netloc}"


def normalize_link(link):
    link = clean_text(link)
    if not link:
        return ""
    link = link.split("?")[0].split("#")[0]
    link = link.replace("://www.", "://")
    link = link.rstrip("/")
    return link.lower()


def normalize_title_key(title):
    title = clean_title_for_message(title)
    title = normalize_text(title)
    title = re.sub(r"[^a-zA-Z0-9əöğüçıƏÖĞÜÇŞşİı\s]", " ", title)
    title = re.sub(r"\s+", " ", title).strip()
    return title


def load_json_file(path, default):
    try:
        with open(path, "r", encoding="utf-8") as file:
            return json.load(file)
    except Exception:
        return default


def load_keywords():
    data = load_json_file(KEYWORDS_FILE, {})
    if isinstance(data, dict):
        return data.get("keywords", []) or []
    if isinstance(data, list):
        return data
    return []


def load_patterns():
    return load_json_file(PATTERNS_FILE, {})


GLOBAL_KEYWORDS = load_keywords()


def load_health():
    return load_json_file(HEALTH_FILE, {})


def save_health(data):
    try:
        with open(HEALTH_FILE, "w", encoding="utf-8") as file:
            json.dump(data, file, ensure_ascii=False, indent=2)
    except Exception as exc:
        print("Health save xətası:", exc, flush=True)


def update_site_health(domain, status):
    health = load_health()
    if domain not in health:
        health[domain] = {
            "checked": 0,
            "success": 0,
            "no_candidate": 0,
            "error": 0,
            "last_check": None,
        }
    health[domain]["checked"] += 1
    health[domain]["last_check"] = datetime.now(BAKU_TZ).isoformat()
    if status in health[domain]:
        health[domain][status] += 1
    save_health(health)


def send_telegram(message):
    if not BOT_TOKEN or not CHAT_ID:
        print("BOT_TOKEN və ya CHAT_ID yoxdur.", flush=True)
        return False

    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    try:
        with TELEGRAM_LOCK:
            response = requests.post(
                url,
                data={
                    "chat_id": CHAT_ID,
                    "text": message,
                    "disable_web_page_preview": False,
                },
                timeout=15,
            )
        print("Telegram:", response.status_code, flush=True)

        if response.status_code == 429:
            retry_after = response.json().get("parameters", {}).get("retry_after", 30)
            time.sleep(retry_after + 2)
            return False

        if response.status_code == 400 and "migrate_to_chat_id" in response.text:
            print("Telegram qrupu supergroup-a keçib. CHAT_ID-ni yenilə.", flush=True)

        return response.status_code == 200
    except Exception as exc:
        print("Telegram xətası:", exc, flush=True)
        return False


def clean_title_for_message(title):
    title = clean_text(title)
    category_pattern = r"^(" + "|".join(re.escape(c) for c in NEWS_CATEGORIES) + r")\s+"
    title = re.sub(category_pattern, "", title, flags=re.IGNORECASE)
    title = re.sub(r"^\d{1,2}[:.]\d{2}\s+", "", title)
    title = re.sub(r"^[-–—|]+\s*", "", title)
    title = re.sub(r"^\d{1,2}[:.]\d{2}\s*[-–—|]?\s*", "", title)
    title = re.sub(
        r"\s+\d{1,2}\s+[a-zəöğıçşü]+\s+\d{4}\s*,?\s*\d{1,2}[:.]\d{2}$",
        "",
        title,
        flags=re.IGNORECASE,
    )
    title = re.sub(r"\s+\d{1,2}[./-]\d{1,2}[./-]\d{4}\s+\d{1,2}[:.]\d{2}$", "", title)
    title = re.sub(r"\s+\d{1,2}[./-]\d{1,2}[./-]\d{4}$", "", title)
    return clean_text(title)


def clean_matched_keywords(keywords):
    cleaned = []
    seen = set()
    for keyword in keywords or []:
        normalized = normalize_text(keyword)
        if not normalized or normalized in NEWS_CATEGORIES or normalized in seen:
            continue
        seen.add(normalized)
        cleaned.append(keyword)
    return cleaned


def keyword_match(title, keywords):
    title_lower = normalize_text(title)
    all_keywords = set()

    for keyword in GLOBAL_KEYWORDS:
        keyword = str(keyword).strip().lower()
        if keyword:
            all_keywords.add(keyword)

    for keyword in keywords or []:
        keyword = str(keyword).strip().lower()
        if keyword:
            all_keywords.add(keyword)

    matched_keywords = []
    word_chars = r"a-zA-Z0-9əöğüçıƏÖĞÜÇŞşİı"

    for keyword in sorted(all_keywords, key=len, reverse=True):
        keyword = normalize_text(keyword)
        if keyword in STRICT_WORDS:
            pattern = rf"(?<![{word_chars}])" + re.escape(keyword) + rf"(?![{word_chars}])"
            if re.search(pattern, title_lower, flags=re.IGNORECASE):
                matched_keywords.append(keyword)
        else:
            if keyword in title_lower:
                matched_keywords.append(keyword)

    return (bool(matched_keywords), matched_keywords)


def exists(link, title=None):
    if not supabase_ready():
        return False

    normalized_link = normalize_link(link)
    title_key = normalize_title_key(title) if title else ""
    if not normalized_link:
        return False

    try:
        with DB_LOCK:
            response = requests.get(
                f"{SUPABASE_URL}/rest/v1/sent_news",
                headers=supabase_headers(),
                params={"select": "link,title", "link": f"eq.{normalized_link}", "limit": "1"},
                timeout=REQUEST_TIMEOUT,
            )
        if response.status_code == 200 and response.json():
            print(f"⛔ Təkrar xəbər link üzrə bazada var: {normalized_link}", flush=True)
            return True

        if title_key:
            with DB_LOCK:
                title_response = requests.get(
                    f"{SUPABASE_URL}/rest/v1/sent_news",
                    headers=supabase_headers(),
                    params={"select": "link,title", "title": f"eq.{title_key}", "limit": "1"},
                    timeout=REQUEST_TIMEOUT,
                )
            if title_response.status_code == 200 and title_response.json():
                print(f"⛔ Təkrar xəbər başlıq üzrə bazada var: {title_key[:80]}", flush=True)
                return True
        return False
    except Exception as exc:
        print(f"Supabase exists istisnası: {exc}", flush=True)
        return False


def reserve_news(link, title, source):
    if not supabase_ready():
        return True

    normalized_link = normalize_link(link)
    title_key = normalize_title_key(title)
    if not normalized_link:
        return False

    if exists(normalized_link, title_key):
        return False

    payload = {"link": normalized_link, "title": title_key or clean_text(title), "source": source}
    try:
        with DB_LOCK:
            response = requests.post(
                f"{SUPABASE_URL}/rest/v1/sent_news",
                headers=supabase_headers({"Prefer": "return=minimal"}),
                json=payload,
                timeout=REQUEST_TIMEOUT,
            )
        if response.status_code in (200, 201, 204):
            print(f"✅ Supabase rezerv edildi: {normalized_link}", flush=True)
            return True
        if response.status_code == 409:
            print(f"⛔ Supabase duplicate rezerv: {normalized_link}", flush=True)
            return False
        print(f"Supabase reserve xətası: {response.status_code} | {response.text[:300]}", flush=True)
        return False
    except Exception as exc:
        print(f"Supabase reserve istisnası: {exc}", flush=True)
        return False


def release_reserved_news(link):
    if not supabase_ready():
        return False
    normalized_link = normalize_link(link)
    if not normalized_link:
        return False
    try:
        with DB_LOCK:
            response = requests.delete(
                f"{SUPABASE_URL}/rest/v1/sent_news",
                headers=supabase_headers(),
                params={"link": f"eq.{normalized_link}"},
                timeout=REQUEST_TIMEOUT,
            )
        return response.status_code in (200, 204)
    except Exception as exc:
        print(f"Rezerv silmə istisnası: {exc}", flush=True)
        return False


def get_or_create_monitor_source(site_name, source_domain, page_url):
    if not supabase_ready():
        return None

    base_url = get_base_url(page_url)
    if not base_url:
        return None

    try:
        response = requests.get(
            f"{SUPABASE_URL}/rest/v1/sources",
            headers=supabase_headers(),
            params={"select": "id", "base_url": f"eq.{base_url}", "limit": "1"},
            timeout=REQUEST_TIMEOUT,
        )
        if response.status_code == 200 and response.json():
            return response.json()[0]["id"]

        payload = {
            "name": site_name or source_domain,
            "base_url": base_url,
            "latest_url": page_url,
            "source_type": "news_site",
            "status": "active",
            "trust_level": "medium",
        }
        create_response = requests.post(
            f"{SUPABASE_URL}/rest/v1/sources",
            headers=supabase_headers({"Prefer": "return=representation"}),
            json=payload,
            timeout=REQUEST_TIMEOUT,
        )
        if create_response.status_code in (200, 201) and create_response.json():
            return create_response.json()[0]["id"]
        print(f"Monitor source yaradılmadı: {create_response.status_code} | {create_response.text[:200]}", flush=True)
        return None
    except Exception as exc:
        print(f"Monitor source xətası: {exc}", flush=True)
        return None


AZ_MONTHS = {
    "yanvar": 1, "yan": 1,
    "fevral": 2, "fev": 2,
    "mart": 3, "mar": 3,
    "aprel": 4, "apr": 4,
    "may": 5,
    "iyun": 6, "iyn": 6, "jun": 6, "june": 6,
    "iyul": 7, "iyl": 7, "jul": 7, "july": 7,
    "avqust": 8, "avq": 8, "aug": 8, "august": 8,
    "sentyabr": 9, "sen": 9, "sep": 9, "september": 9,
    "oktyabr": 10, "okt": 10, "oct": 10, "october": 10,
    "noyabr": 11, "noy": 11, "nov": 11, "november": 11,
    "dekabr": 12, "dek": 12, "dec": 12, "december": 12,
}


def normalize_date_text(value):
    text = clean_text(str(value or "")).lower()

    text = text.replace("ı", "i")
    text = text.replace("İ", "i").replace("i̇", "i")
    text = text.replace("ə", "e")
    text = text.replace("ö", "o")
    text = text.replace("ğ", "g")
    text = text.replace("ü", "u")
    text = text.replace("ç", "c")
    text = text.replace("ş", "s")

    text = text.replace("—", "-").replace("–", "-")
    text = text.replace("|", " ")
    text = text.replace("/", " ")
    text = re.sub(r"\s+", " ", text).strip()

    return text


def month_number(month_name):
    raw = clean_text(month_name).lower()

    variants = {
        raw,
        raw.replace("ı", "i").replace("İ", "i").replace("i̇", "i"),
        raw.replace("ə", "e").replace("ö", "o").replace("ğ", "g").replace("ü", "u").replace("ç", "c").replace("ş", "s").replace("ı", "i"),
    }

    aliases = {
        "yanvar": 1, "yan": 1,
        "fevral": 2, "fev": 2,
        "mart": 3, "mar": 3,
        "aprel": 4, "apr": 4,
        "may": 5,
        "iyun": 6, "iyn": 6, "jun": 6, "june": 6,
        "iyul": 7, "iyl": 7, "jul": 7, "july": 7,
        "avqust": 8, "avq": 8, "aug": 8, "august": 8,
        "sentyabr": 9, "sen": 9, "sep": 9, "september": 9,
        "oktyabr": 10, "okt": 10, "oct": 10, "october": 10,
        "noyabr": 11, "noy": 11, "nov": 11, "november": 11,
        "dekabr": 12, "dek": 12, "dec": 12, "december": 12,

        "yanvar": 1, "fevral": 2, "aprel": 4,
        "iyun": 6, "iyul": 7,
    }

    for item in variants:
        if item in aliases:
            return aliases[item]

    return None


def safe_datetime(year, month, day, hour=0, minute=0):
    try:
        year = int(year)
        month = int(month)
        day = int(day)
        hour = int(hour)
        minute = int(minute)

        if year < 2020 or year > 2035:
            return None

        if not (1 <= month <= 12):
            return None

        if not (1 <= day <= 31):
            return None

        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            return None

        return datetime(year, month, day, hour, minute, tzinfo=BAKU_TZ)
    except Exception:
        return None


def parse_az_datetime(value):
    original = clean_text(str(value or ""))
    text = normalize_date_text(original)

    if not text:
        return None

    # 1) Azərbaycan formatı:
    # 12 İyun 2026, 17:41
    # 12 İyun 2026, Cümə
    pattern = r"(\d{1,2})\s+([a-z]+)\s+(\d{4})(?:\s*,?\s*(?:[a-z]+)?)?(?:\s+(\d{1,2})[:.](\d{2}))?"
    m = re.search(pattern, text, re.IGNORECASE)
    if m:
        day, month_name, year, hour, minute = m.groups()
        month = month_number(month_name)

        if month:
            return safe_datetime(
                year,
                month,
                day,
                hour or 0,
                minute or 0,
            )

    # 2) Qısa ay əvvəl:
    # İyn 11, 2026 | 04:34
    # May 26, 2026 | 02:49
    pattern = r"([a-z]+)\s+(\d{1,2})\s*,?\s+(\d{4})(?:\s+(\d{1,2})[:.](\d{2}))?"
    m = re.search(pattern, text, re.IGNORECASE)
    if m:
        month_name, day, year, hour, minute = m.groups()
        month = month_number(month_name)

        if month:
            return safe_datetime(
                year,
                month,
                day,
                hour or 0,
                minute or 0,
            )

    # 3) Rəqəmli tarix + saat:
    # 12.06.2026 17:41
    pattern = r"(\d{1,2})[.\-/](\d{1,2})[.\-/](\d{4})(?:\s+(\d{1,2})[:.](\d{2}))?"
    m = re.search(pattern, text)
    if m:
        day, month, year, hour, minute = m.groups()
        return safe_datetime(
            year,
            month,
            day,
            hour or 0,
            minute or 0,
        )

    # 4) Saat əvvəldə:
    # 17:41 12 İyun 2026
    pattern = r"(\d{1,2})[:.](\d{2})\s+(\d{1,2})\s+([a-z]+)\s+(\d{4})"
    m = re.search(pattern, text, re.IGNORECASE)
    if m:
        hour, minute, day, month_name, year = m.groups()
        month = month_number(month_name)

        if month:
            return safe_datetime(year, month, day, hour, minute)

    # 5) Başlıq əvvəlində yalnız saat:
    # 09:41 Müəllimlərin...
    time_only = re.search(r"^\s*(\d{1,2})[:.](\d{2})(?:\s|$)", text)
    if time_only:
        hour = int(time_only.group(1))
        minute = int(time_only.group(2))
        today = datetime.now(BAKU_TZ).date()
        return safe_datetime(today.year, today.month, today.day, hour, minute)

    return None


def parse_datetime_to_baku(published_time):
    text = clean_text(str(published_time or ""))

    if not text or "tarix tapılmadı" in text.lower():
        return None

    try:
        dt = parsedate_to_datetime(text)

        if dt:
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=BAKU_TZ)
            else:
                dt = dt.astimezone(BAKU_TZ)

            return dt
    except Exception:
        pass

    az_dt = parse_az_datetime(text)
    if az_dt:
        return az_dt

    try:
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
        print(f"Bugünkü xəbər deyil, keçildi: {published_time} | bugün: {now_baku.date()}", flush=True)
        return False
    return True


def is_recent_news(published_time):
    dt = parse_datetime_to_baku(published_time)
    if not dt:
        return False
    now_baku = datetime.now(BAKU_TZ)
    if dt.date() != now_baku.date():
        print(f"Bugünkü xəbər deyil, keçildi: {published_time} | bugün: {now_baku.date()}", flush=True)
        return False
    diff = now_baku - dt
    if diff.total_seconds() < 0:
        print(f"Gələcək tarix kimi göründü, keçildi: {published_time}", flush=True)
        return False
    if diff <= timedelta(hours=NEWS_TIME_LIMIT_HOURS):
        print(f"Tarix uyğundur: {published_time} | fərq: {diff.total_seconds() / 3600:.2f} saat", flush=True)
        return True
    print(f"Köhnə xəbər keçildi: {published_time} | fərq: {diff.total_seconds() / 3600:.2f} saat", flush=True)
    return False


def choose_publish_time(title, article_time):
    title_dt = parse_datetime_to_baku(title)
    article_dt = parse_datetime_to_baku(article_time)
    if title_dt:
        return title_dt.strftime("%d.%m.%Y %H:%M")
    if article_dt:
        return article_dt.strftime("%d.%m.%Y %H:%M")
    return None


def extract_publish_time_from_article(article_url):
    headers = {"User-Agent": "Mozilla/5.0", "Accept-Language": "az-AZ,az;q=0.9,en-US;q=0.8"}
    try:
        response = requests.get(article_url, headers=headers, timeout=REQUEST_TIMEOUT)
        response.encoding = response.apparent_encoding
        tree = html.fromstring(response.text)
        possible_xpaths = [
            "//time/@datetime", "//time/text()",
            "//meta[@property='article:published_time']/@content",
            "//meta[@name='article:published_time']/@content",
            "//meta[@itemprop='datePublished']/@content",
            "//meta[@name='pubdate']/@content", "//meta[@name='date']/@content",
            "//meta[@name='DC.date.issued']/@content", "//meta[@name='publishdate']/@content",
            "//meta[@name='publish_date']/@content",
            "//span[contains(@class,'date')]/text()", "//div[contains(@class,'date')]/text()",
            "//span[contains(@class,'time')]/text()", "//div[contains(@class,'time')]/text()",
            "//*[contains(@class,'date')]/text()", "//*[contains(@class,'time')]/text()",
        ]
        for xpath in possible_xpaths:
            result = tree.xpath(xpath)
            if result:
                value = clean_text(str(result[0]))
                if len(value) > 5:
                    return value
    except Exception as exc:
        print("Tarix çıxarma xətası:", exc, flush=True)
    return None


def is_probably_section_url(link):
    path = urlparse(link.lower()).path.strip("/").lower()
    if not path:
        return True
    section_paths = [
        "news", "xeber", "xeberler", "xəbərlər", "media", "media/news", "category",
        "kateqoriya", "archive", "arxiv", "allnews", "all-news", "newsarchive", "latest",
        "lastnews", "son-xeberler", "az/news", "az/xeber", "az/xeberler", "az/xəbərlər",
        "az/metbuat/xeberler", "az/page/media/news", "az/news-and-updates", "p/news",
        "tehsil", "elm", "elm-ve-tehsil",
    ]
    if path in section_paths:
        return True
    bad_section_words = [
        "news", "xeber", "xeberler", "xəbərlər", "category", "kateqoriya", "archive",
        "arxiv", "latest", "lastnews", "allnews", "all-news", "son-xeberler", "media",
    ]
    parts = [part for part in path.split("/") if part]
    if len(parts) <= 1 and any(word in path for word in bad_section_words):
        return True
    if len(parts) <= 2 and any(path.endswith(word) for word in bad_section_words):
        return True
    return False


def is_article_like_link(link):
    link_lower = link.lower()
    return any(pattern in link_lower for pattern in ARTICLE_URL_PATTERNS)


def is_bad_link(title, link):
    title_lower = title.lower()
    link_lower = link.lower()
    bad_words = [
        "ana səhifə", "haqqımızda", "əlaqə", "reklam", "giriş", "qeydiyyat",
        "axtarış", "abunə", "facebook", "instagram", "youtube", "telegram",
        "twitter", "linkedin", "rss", "bütün xəbərlər", "daha çox", "arxiv",
        "kateqoriya", "bütün bölmələr", "menu", "menyu",
    ]
    bad_domains = ["facebook.com", "instagram.com", "youtube.com", "t.me", "twitter.com", "x.com", "linkedin.com"]
    bad_extensions = [".jpg", ".jpeg", ".png", ".gif", ".webp", ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".zip", ".rar", ".mp4", ".mp3"]
    if len(title) < 15:
        return True
    if any(word in title_lower for word in bad_words):
        return True
    if any(domain in link_lower for domain in bad_domains):
        return True
    if any(link_lower.endswith(ext) for ext in bad_extensions):
        return True
    if is_probably_section_url(link):
        return True
    return False


def add_item(results, page_url, title, link, keywords, extra=None):
    title = clean_text(title)
    link = urljoin(page_url, clean_text(link)).split("#")[0]
    if not title or not link.startswith("http"):
        return
    if get_domain(page_url) and get_domain(link) and get_domain(page_url) != get_domain(link):
        # Google News fallback bəzən orijinal linki news.google yönləndiricisi ilə verir, ona görə source domain yoxdursa keçmirik.
        if "news.google.com" not in page_url:
            return
    if is_bad_link(title, link):
        return
    if not is_article_like_link(link):
        return
    title_for_keyword = clean_title_for_message(title)
    matched, matched_keywords = keyword_match(title_for_keyword, keywords)
    matched_keywords = clean_matched_keywords(matched_keywords)
    if not matched_keywords:
        return
    item = {
        "title": title,
        "clean_title": title_for_keyword,
        "link": link,
        "source": get_domain(link) or get_domain(page_url),
        "matched_keywords": matched_keywords,
    }
    if extra:
        item.update(extra)
    results.append(item)


def unique_items(items):
    unique = {}
    for item in items:
        if item.get("link"):
            unique[normalize_link(item["link"])] = item
    return list(unique.values())


def discover_rss_links(page_url, page_html):
    rss_links = []
    try:
        soup = BeautifulSoup(page_html, "html.parser")
        for tag in soup.find_all("link", href=True):
            tag_type = (tag.get("type") or "").lower()
            tag_title = (tag.get("title") or "").lower()
            href = tag.get("href")
            if "rss" in tag_type or "atom" in tag_type or "rss" in tag_title or "feed" in tag_title:
                rss_links.append(urljoin(page_url, href))
        root = get_base_url(page_url)
        for path in COMMON_RSS_PATHS:
            rss_links.append(urljoin(root, path))
    except Exception as exc:
        print(f"RSS link axtarışı xətası: {page_url} | {exc}", flush=True)
    return list(dict.fromkeys([item for item in rss_links if item and item.startswith("http")]))[:8]


def extract_links_from_rss(site, rss_urls):
    results = []
    keywords = site.get("keywords", [])
    page_url = site.get("url") or site.get("base_url") or ""
    for rss_url in rss_urls:
        if not rss_url:
            continue
        try:
            response = requests.get(
                rss_url,
                headers={"User-Agent": "Mozilla/5.0", "Accept-Language": "az-AZ,az;q=0.9,en-US;q=0.8", "Referer": "https://www.google.com/"},
                timeout=REQUEST_TIMEOUT,
                allow_redirects=True,
            )
            if response.status_code != 200:
                continue
            feed = feedparser.parse(response.text)
            if not feed.entries:
                continue
            print(f"RSS tapıldı: {rss_url} | xəbər sayı: {len(feed.entries)}", flush=True)
            before_count = len(results)
            for entry in feed.entries[:MAX_LINKS_PER_SITE * 4]:
                title = clean_text(entry.get("title", ""))
                link = entry.get("link", "")
                published = entry.get("published") or entry.get("updated") or entry.get("created") or ""
                add_item(results, page_url or rss_url, title, link, keywords, {"rss_published": published})
                if len(results) >= MAX_LINKS_PER_SITE:
                    break
            added = len(results) - before_count
            if added > 0:
                print(f"RSS uyğun namizəd verdi: {site.get('name')} | {added}", flush=True)
                break
        except Exception as exc:
            print(f"RSS oxuma xətası: {rss_url} | {exc}", flush=True)
            continue
    return unique_items(results)[:MAX_LINKS_PER_SITE]


def extract_links_by_selector(page_url, page_html, selector, keywords):
    results = []
    if not selector:
        return []
    try:
        soup = BeautifulSoup(page_html, "html.parser")
        blocks = soup.select(selector)
        print(f"Selector blok sayı: {len(blocks)} | {selector}", flush=True)
    except Exception as exc:
        print("Selector xətası:", exc, flush=True)
        return []
    for block in blocks:
        links = block.find_all("a", href=True)
        if getattr(block, "name", None) == "a" and block.get("href"):
            links.append(block)
        for a in links:
            title = clean_text(a.get_text(" ", strip=True))
            link = urljoin(page_url, a.get("href", ""))
            add_item(results, page_url, title, link, keywords)
    return unique_items(results)[:MAX_LINKS_PER_SITE]


def extract_links_from_xpath(page_url, page_html, xpaths, keywords):
    results = []
    if not xpaths:
        return []
    try:
        tree = html.fromstring(page_html)
    except Exception as exc:
        print("HTML parse xətası:", exc, flush=True)
        return []
    for xpath in xpaths:
        try:
            blocks = tree.xpath(xpath)
        except Exception as exc:
            print("XPath xətası:", exc, flush=True)
            continue
        print(f"XPath üzrə blok sayı: {len(blocks)} | {xpath[:80]}", flush=True)
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
    return unique_items(results)[:MAX_LINKS_PER_SITE]


def extract_links_by_patterns(page_url, page_html, keywords, patterns):
    soup = BeautifulSoup(page_html, "html.parser")
    results = []
    for a in soup.find_all("a", href=True):
        title = clean_text(a.get_text(" ", strip=True))
        link = urljoin(page_url, a["href"])
        if not any(pattern.lower() in link.lower() for pattern in patterns):
            continue
        add_item(results, page_url, title, link, keywords)
    return unique_items(results)[:MAX_LINKS_PER_SITE]


def extract_links_fallback(page_url, page_html, keywords):
    soup = BeautifulSoup(page_html, "html.parser")
    results = []
    for a in soup.find_all("a", href=True):
        title = clean_text(a.get_text(" ", strip=True))
        link = urljoin(page_url, a["href"])
        add_item(results, page_url, title, link, keywords)
    return unique_items(results)[:MAX_LINKS_PER_SITE]


def extract_links_from_sitemap(site):
    sitemap_url = site.get("latest_url") or urljoin(site.get("base_url", "").rstrip("/") + "/", "sitemap.xml")
    keywords = site.get("keywords", [])
    results = []
    try:
        response = requests.get(sitemap_url, headers={"User-Agent": "Mozilla/5.0"}, timeout=REQUEST_TIMEOUT)
        if response.status_code != 200:
            return []
        urls = re.findall(r"<loc>(.*?)</loc>", response.text, flags=re.IGNORECASE)
        for url in urls[:300]:
            if not any(pattern in url.lower() for pattern in ARTICLE_URL_PATTERNS):
                continue
            # Sitemap-də başlıq yoxdur; məqaləni açıb title/meta alırıq.
            try:
                article = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=REQUEST_TIMEOUT)
                if article.status_code != 200:
                    continue
                soup = BeautifulSoup(article.text, "html.parser")
                title = ""
                if soup.find("meta", property="og:title"):
                    title = soup.find("meta", property="og:title").get("content", "")
                if not title and soup.find("title"):
                    title = soup.find("title").get_text(" ", strip=True)
                add_item(results, url, title, url, keywords)
                if len(results) >= MAX_LINKS_PER_SITE:
                    break
            except Exception:
                continue
    except Exception as exc:
        print(f"Sitemap oxuma xətası: {sitemap_url} | {exc}", flush=True)
    return unique_items(results)[:MAX_LINKS_PER_SITE]


def fetch_page(url):
    headers = {"User-Agent": "Mozilla/5.0", "Accept-Language": "az-AZ,az;q=0.9,en-US;q=0.8", "Referer": "https://www.google.com/"}
    try:
        print(f"Sayt açılır: {url}", flush=True)
        response = requests.get(url, headers=headers, timeout=REQUEST_TIMEOUT, allow_redirects=True)
        print(f"Status: {response.status_code}", flush=True)
        if response.status_code != 200:
            return None
        response.encoding = response.apparent_encoding
        return response.text
    except Exception as exc:
        print(f"Sayt xətası: {url} | {exc}", flush=True)
        return None


def fetch_site(site, patterns_data):
    page_url = site.get("url") or site.get("latest_url") or site.get("base_url")
    base_url = site.get("base_url") or get_base_url(page_url)
    rss_url = clean_text(site.get("rss_url", ""))
    selector = clean_text(site.get("selector", ""))
    article_pattern = clean_text(site.get("article_pattern", ""))
    method = clean_text(site.get("monitor_method", "")).lower()
    keywords = site.get("keywords", [])

    print(f"Metod: {method or 'auto'} | {site.get('name')} | {page_url}", flush=True)

    # 1. Method-based oxuma
    if method in {"rss", "rss_discovered", "google_news", "google_news_fallback"}:
        items = extract_links_from_rss(site, [rss_url or page_url])
        if items:
            return items

    if method == "sitemap":
        items = extract_links_from_sitemap(site)
        if items:
            return items

    page_html = None
    if page_url:
        page_html = fetch_page(page_url)

    if page_html:
        if method == "selector" and selector:
            items = extract_links_by_selector(page_url, page_html, selector, keywords)
            if items:
                return items

        if method == "xpath_pattern" and article_pattern:
            xpaths = [clean_text(x) for x in re.split(r"[,\n\r]+", article_pattern) if clean_text(x)]
            items = extract_links_from_xpath(page_url, page_html, xpaths, keywords)
            if items:
                return items

        if method in {"latest_page", "homepage", "recoverable", "auto", ""}:
            items = extract_links_fallback(page_url, page_html, keywords)
            if items:
                return items

    # 2. Fallback RSS discovery
    if page_html and not rss_url:
        discovered_rss = discover_rss_links(page_url, page_html)
        if discovered_rss:
            items = extract_links_from_rss(site, discovered_rss)
            if items:
                return items

    # 3. Common latest paths
    if base_url:
        for path in COMMON_LATEST_PATHS:
            candidate_url = urljoin(base_url.rstrip("/") + "/", path.lstrip("/"))
            html_text = fetch_page(candidate_url)
            if not html_text:
                continue
            items = extract_links_fallback(candidate_url, html_text, keywords)
            if items:
                return items

    # 4. Pattern fallback
    if page_html:
        domain = get_domain(page_url)
        site_patterns = patterns_data.get(domain, [])
        if site_patterns:
            print(f"Pattern fallback işləyir: {domain}", flush=True)
            items = extract_links_by_patterns(page_url, page_html, keywords, site_patterns)
            if items:
                return items

    # 5. Last HTML fallback
    if page_html:
        print("HTML fallback işləyir...", flush=True)
        return extract_links_fallback(page_url, page_html, keywords)

    return []


def get_existing_monitor_match_id(monitor_id, item_id):
    if not supabase_ready() or not monitor_id or not item_id:
        return None
    try:
        response = requests.get(
            f"{SUPABASE_URL}/rest/v1/monitor_matches",
            headers=supabase_headers(),
            params={"select": "id", "monitor_id": f"eq.{monitor_id}", "item_id": f"eq.{item_id}", "limit": "1"},
            timeout=REQUEST_TIMEOUT,
        )
        if response.status_code == 200 and response.json():
            return response.json()[0].get("id")
        return None
    except Exception as exc:
        print(f"Monitor match_id oxuma xətası: {exc}", flush=True)
        return None


def get_existing_monitor_alert_id(match_id):
    if not supabase_ready() or not match_id:
        return None
    try:
        response = requests.get(
            f"{SUPABASE_URL}/rest/v1/monitor_alerts",
            headers=supabase_headers(),
            params={"select": "id", "match_id": f"eq.{match_id}", "limit": "1"},
            timeout=REQUEST_TIMEOUT,
        )
        if response.status_code == 200 and response.json():
            return response.json()[0].get("id")
        return None
    except Exception as exc:
        print(f"Bildiriş mövcudluq istisnası: {exc}", flush=True)
        return None


def create_monitor_alert(match_id):
    if not supabase_ready() or not match_id:
        return False
    if get_existing_monitor_alert_id(match_id):
        print(f"⛔ Bildiriş artıq mövcuddur: match={match_id}", flush=True)
        return False
    payload_variants = [
        {"match_id": match_id, "channel": "web", "recipient": "admin", "status": "new", "sent_at": datetime.now(BAKU_TZ).isoformat()},
        {"match_id": match_id, "channel": "web", "recipient": "admin", "status": "new"},
        {"match_id": match_id, "status": "new"},
    ]
    last_error = ""
    for payload in payload_variants:
        try:
            response = requests.post(
                f"{SUPABASE_URL}/rest/v1/monitor_alerts",
                headers=supabase_headers({"Prefer": "return=representation"}),
                json=payload,
                timeout=REQUEST_TIMEOUT,
            )
            if response.status_code in (200, 201):
                data = response.json() or []
                alert_id = data[0].get("id") if data else None
                print(f"🔔 Bildiriş yaradıldı: match={match_id} | alert={alert_id}", flush=True)
                return True
            if response.status_code == 409:
                return False
            last_error = f"{response.status_code} | {response.text[:300]}"
        except Exception as exc:
            last_error = str(exc)
    print(f"Bildiriş yazılmadı: match={match_id} | son xəta: {last_error}", flush=True)
    return False


def save_to_vizual_monitor(site, item, clean_title, published_time):
    if not supabase_ready():
        return None
    link = normalize_link(item.get("link"))
    if not link:
        return None
    source_id = get_or_create_monitor_source(site.get("name"), item.get("source"), site.get("url"))
    if not source_id:
        print("Vizual Monitor: source_id tapılmadı", flush=True)
        return None
    dt = parse_datetime_to_baku(published_time)
    payload = {
        "source_id": source_id,
        "title": clean_title,
        "url": link,
        "published_at": dt.isoformat() if dt else None,
        "detected_at": datetime.now(BAKU_TZ).isoformat(),
        "item_hash": link,
        "status": "new",
    }
    try:
        response = requests.post(
            f"{SUPABASE_URL}/rest/v1/monitored_items",
            headers=supabase_headers({"Prefer": "resolution=ignore-duplicates,return=representation"}),
            json=payload,
            timeout=REQUEST_TIMEOUT,
        )
        if response.status_code in (200, 201) and response.json():
            item_id = response.json()[0].get("id")
            print(f"✅ Vizual Monitor-a yazıldı: {clean_title[:80]}", flush=True)
            return item_id
        if response.status_code in (204, 409):
            existing = requests.get(
                f"{SUPABASE_URL}/rest/v1/monitored_items",
                headers=supabase_headers(),
                params={"select": "id", "url": f"eq.{link}", "limit": "1"},
                timeout=REQUEST_TIMEOUT,
            )
            if existing.status_code == 200 and existing.json():
                return existing.json()[0].get("id")
        print(f"Vizual Monitor yazma xətası: {response.status_code} | {response.text[:300]}", flush=True)
        return None
    except Exception as exc:
        print(f"Vizual Monitor istisnası: {exc}", flush=True)
        return None


def match_user_monitors(item_id, title):
    if not supabase_ready() or not item_id:
        return 0
    title_text = normalize_text(title)
    try:
        response = requests.get(
            f"{SUPABASE_URL}/rest/v1/monitor_keywords",
            headers=supabase_headers(),
            params={"select": "id,keyword,match_type,monitor_id,user_monitors(status)"},
            timeout=REQUEST_TIMEOUT,
        )
        if response.status_code != 200:
            print(f"Monitor keyword oxuma xətası: {response.status_code} | {response.text[:200]}", flush=True)
            return 0
        keywords = response.json() or []
        matched_count = 0
        for row in keywords:
            monitor_status = (row.get("user_monitors") or {}).get("status")
            if monitor_status != "active":
                continue
            keyword_original = row.get("keyword", "")
            keyword = normalize_text(keyword_original)
            if not keyword or keyword not in title_text:
                continue
            payload = {"monitor_id": row.get("monitor_id"), "item_id": item_id, "matched_keyword": keyword_original}
            match_response = requests.post(
                f"{SUPABASE_URL}/rest/v1/monitor_matches",
                headers=supabase_headers({"Prefer": "resolution=ignore-duplicates,return=representation"}),
                json=payload,
                timeout=REQUEST_TIMEOUT,
            )
            if match_response.status_code in (200, 201):
                matched_count += 1
                match_data = match_response.json() or []
                match_id = match_data[0].get("id") if match_data else None
                print(f"✅ Monitor uyğunluğu yazıldı: {keyword_original} | item={item_id}", flush=True)
                if match_id:
                    create_monitor_alert(match_id)
            elif match_response.status_code in (204, 409):
                match_id = get_existing_monitor_match_id(row.get("monitor_id"), item_id)
                if match_id:
                    create_monitor_alert(match_id)
            else:
                print(f"Monitor match yazma xətası: {match_response.status_code} | {match_response.text[:200]}", flush=True)
        return matched_count
    except Exception as exc:
        print(f"Monitor match istisnası: {exc}", flush=True)
        return 0


def extract_keywords_from_rules(site):
    keywords = set()
    for keyword in site.get("keywords", []):
        if str(keyword).strip():
            keywords.add(str(keyword).lower().strip())
    for rule in site.get("condition_rules", []):
        value = rule.get("value", "")
        for part in re.split(r"[|\r\n]+", value):
            word = clean_text(part).replace(".*", "").strip()
            if word and len(word) > 1:
                keywords.add(word.lower())
    return list(keywords)


def load_sites():
    if not supabase_ready():
        print("Supabase bağlantısı yoxdur, sources oxunmadı.", flush=True)
        return []

    all_sites = []
    seen_urls = set()
    try:
        offset = 0
        page_size = 1000
        while True:
            response = requests.get(
                f"{SUPABASE_URL}/rest/v1/sources",
                headers=supabase_headers(),
                params={
                    "select": "id,name,base_url,latest_url,rss_url,status,source_type,trust_level,monitor_method,selector,article_pattern,discovery_status,discovery_score,notes",
                    "status": "eq.active",
                    "order": "name.asc",
                    "limit": str(page_size),
                    "offset": str(offset),
                },
                timeout=REQUEST_TIMEOUT,
            )
            if response.status_code != 200:
                print(f"Supabase sources oxuma xətası: {response.status_code} | {response.text[:300]}", flush=True)
                return []
            rows = response.json() or []
            if not rows:
                break
            for row in rows:
                base_url = clean_text(row.get("base_url", ""))
                latest_url = clean_text(row.get("latest_url", ""))
                rss_url = clean_text(row.get("rss_url", ""))
                method = clean_text(row.get("monitor_method", "")).lower()

                # failed/dead mənbələri əsas monitorinqdə keçirik. blocked üçün Google News fallback varsa oxunacaq.
                if method in {"failed", "dead"}:
                    continue

                url = latest_url or base_url or rss_url
                if not url:
                    continue
                if not url.startswith("http"):
                    url = "https://" + url.lstrip("/")
                normalized_url = normalize_link(url)
                if normalized_url in seen_urls:
                    continue
                seen_urls.add(normalized_url)
                all_sites.append({
                    "id": row.get("id"),
                    "name": row.get("name") or get_domain(url),
                    "url": url,
                    "base_url": base_url,
                    "latest_url": latest_url,
                    "rss_url": rss_url,
                    "selector": row.get("selector") or "",
                    "article_pattern": row.get("article_pattern") or "",
                    "xpaths": [],
                    "keywords": [],
                    "limit": MAX_LINKS_PER_SITE,
                    "source_type": row.get("source_type"),
                    "trust_level": row.get("trust_level"),
                    "monitor_method": method,
                    "discovery_status": row.get("discovery_status"),
                    "discovery_score": row.get("discovery_score"),
                })
            if len(rows) < page_size:
                break
            offset += page_size
        print(f"Supabase active readable sources sayı: {len(all_sites)}", flush=True)
        return all_sites
    except Exception as exc:
        print(f"Supabase sources istisnası: {exc}", flush=True)
        return []


def process_site(index, total, site, patterns_data):
    started = time.time()
    result = {"sent": 0, "site": site.get("name"), "url": site.get("url"), "candidates": 0, "reason": "unknown"}
    print(f"[{index}/{total}] Yoxlanır: {site['name']} | {site['url']}", flush=True)
    try:
        items = fetch_site(site, patterns_data)
    except Exception as exc:
        print(f"❌ [{index}/{total}] {site['name']} | sayt emalı xətası: {exc}", flush=True)
        result["reason"] = "site_error"
        return result

    result["candidates"] = len(items)
    print(f"[{index}/{total}] {site['name']} | uyğun link sayı: {len(items)}", flush=True)

    if not items:
        result["reason"] = "no_candidate"
        print(f"📊 [{index}/{total}] {site['name']} | namizəd=0 | göndərildi=0 | nəticə=uyğun xəbər yoxdur | vaxt={time.time() - started:.1f}s", flush=True)
        return result

    for item in items[:site.get("limit", MAX_LINKS_PER_SITE)]:
        title = item["title"]
        link = item["link"]
        source = item["source"]
        matched_keywords = item.get("matched_keywords", [])

        if exists(link, title):
            result["reason"] = "duplicate"
            continue

        title_time = parse_datetime_to_baku(title)
        rss_time = item.get("rss_published")
        article_time = rss_time or extract_publish_time_from_article(link)
        published_time = choose_publish_time(title, article_time)

        print(f"[{index}/{total}] Xəbər: {title[:80]} | title_tarix: {title_time} | rss_tarix: {rss_time} | article_tarix: {article_time} | seçilən tarix: {published_time} | Link: {link}", flush=True)

        if not published_time:
            result["reason"] = "no_date"
            continue
        if not is_today_news(published_time):
            result["reason"] = "not_today"
            continue
        if not is_recent_news(published_time):
            result["reason"] = "old_news"
            continue

        clean_title = item.get("clean_title") or clean_title_for_message(title)
        matched_keywords = clean_matched_keywords(matched_keywords)
        matched_keywords_text = ", ".join(matched_keywords) if matched_keywords else "Açar söz tapılmadı"

        message = f"""
🆕 Yeni uyğun xəbər

📌 Başlıq:
{clean_title}

🌐 Mənbə:
{source}

🔎 Açar sözlər:
{matched_keywords_text}

🕒 Tarix və saat:
{published_time}

🔗 Link:
{link}
"""
        if not reserve_news(link, clean_title, source):
            result["reason"] = "duplicate"
            continue

        monitor_item_id = save_to_vizual_monitor(site, item, clean_title, published_time)
        if monitor_item_id:
            match_user_monitors(monitor_item_id, clean_title)

        if send_telegram(message):
            print(f"✅ [{index}/{total}] Göndərildi: {source} | {clean_title[:70]} | Açar sözlər: {matched_keywords_text}", flush=True)
            result["sent"] = 1
            result["reason"] = "sent"
            time.sleep(1)
            return result

        release_reserved_news(link)
        result["reason"] = "telegram_error"

    print(f"📊 [{index}/{total}] {site['name']} | namizəd={len(items)} | göndərildi=0 | nəticə={result['reason']} | vaxt={time.time() - started:.1f}s", flush=True)
    return result


def check_sites():
    started = time.time()
    sites = load_sites()
    patterns_data = load_patterns()
    total = len(sites)
    print(f"Yüklənən sayt sayı: {total}", flush=True)
    print(f"Monitorinq başladı | worker={MAX_WORKERS} | son {NEWS_TIME_LIMIT_HOURS} saat | {datetime.now(BAKU_TZ).strftime('%d.%m.%Y %H:%M:%S')} AZT", flush=True)

    sent_count = 0
    stats = {"sent": 0, "no_candidate": 0, "duplicate": 0, "no_date": 0, "not_today": 0, "old_news": 0, "site_error": 0, "telegram_error": 0, "unknown": 0}
    max_workers = max(1, min(MAX_WORKERS, total or 1))

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(process_site, index, total, site, patterns_data): site for index, site in enumerate(sites, start=1)}
        for future in as_completed(futures):
            try:
                result = future.result() or {}
            except Exception as exc:
                print(f"Worker xətası: {exc}", flush=True)
                stats["site_error"] += 1
                continue
            sent = int(result.get("sent", 0) or 0)
            reason = result.get("reason", "unknown")
            sent_count += sent
            stats["sent"] += sent
            if reason != "sent":
                stats[reason] = stats.get(reason, 0) + 1
            if sent_count >= MAX_SEND_PER_RUN:
                print("Bu dövr üçün göndərmə limiti tamamlandı. Qalan başladılmış yoxlamalar tamamlanacaq.", flush=True)
                break

    elapsed = time.time() - started
    print("=" * 60, flush=True)
    print("📈 MONİTORİNQ YEKUNU", flush=True)
    print(f"🌐 Sayt sayı: {total}", flush=True)
    print(f"⚙️ Worker sayı: {max_workers}", flush=True)
    print(f"📤 Göndərilən xəbər: {sent_count}", flush=True)
    print(f"🔎 Uyğun xəbər olmayan sayt: {stats.get('no_candidate', 0)}", flush=True)
    print(f"🔁 Təkrar keçilən: {stats.get('duplicate', 0)}", flush=True)
    print(f"🕒 Tarix tapılmayan: {stats.get('no_date', 0)}", flush=True)
    print(f"📅 Bugünkü olmayan: {stats.get('not_today', 0)}", flush=True)
    print(f"⏩ Köhnə xəbər: {stats.get('old_news', 0)}", flush=True)
    print(f"❌ Sayt/worker xətası: {stats.get('site_error', 0)}", flush=True)
    print(f"📨 Telegram xətası: {stats.get('telegram_error', 0)}", flush=True)
    print(f"⏱️ Ümumi vaxt: {elapsed:.1f} saniyə", flush=True)
    print("=" * 60, flush=True)


def main():
    print("🚀 Sayt monitorinq botu işə düşdü.", flush=True)
    if supabase_ready():
        print("✅ Supabase bağlantı məlumatları yükləndi", flush=True)

    run_once = os.getenv("RUN_ONCE", "1").strip().lower() in {"1", "true", "yes"}
    notify_start = os.getenv("NOTIFY_START", "0").strip().lower() in {"1", "true", "yes"}

    if notify_start:
        send_telegram("✅ Bot işə düşdü və saytları yoxlamağa başladı.")

    if run_once:
        print("🔎 GitHub Actions rejimi: bir dəfə yoxlanılır...", flush=True)
        check_sites()
        print("✅ GitHub Actions monitor yoxlaması tamamlandı.", flush=True)
        return

    while True:
        print("🔎 Yeni xəbərlər yoxlanılır...", flush=True)
        check_sites()
        time.sleep(CHECK_INTERVAL_SECONDS)


if __name__ == "__main__":
    main()
