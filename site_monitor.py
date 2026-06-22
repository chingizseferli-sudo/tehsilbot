import json
import os
import re
import time
import threading
from html import unescape
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from email.utils import parsedate_to_datetime
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse
from zoneinfo import ZoneInfo

import feedparser
import requests
from bs4 import BeautifulSoup
from dateutil import parser
from lxml import html

from domain_policy import is_excluded_domain

HEALTH_FILE = "site_health.json"
TELEGRAM_OFFSET_FILE = "telegram_offset.json"

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
MONITOR_DATA_RETENTION_DAYS = int(os.getenv("MONITOR_DATA_RETENTION_DAYS", "30"))
SOURCE_HEALTH_ENABLED = os.getenv("SOURCE_HEALTH_ENABLED", "false").strip().lower() in {"1", "true", "yes"}
SOURCE_MAX_CONSECUTIVE_FAILS = int(os.getenv("SOURCE_MAX_CONSECUTIVE_FAILS", "5"))
GOOGLE_NEWS_FALLBACK_HOURS = max(1, int(os.getenv("GOOGLE_NEWS_FALLBACK_HOURS", str(NEWS_TIME_LIMIT_HOURS))))

PATTERNS_FILE = "patterns.json"
BAKU_TZ = ZoneInfo("Asia/Baku")
USER_TELEGRAM_CACHE = {}
LAST_MONITOR_CLEANUP = None

STRICT_WORDS = {
    "dim", "tkta", "arti", "pisa", "timss", "pirls", "bağça", "magistr",
    "peşə", "elm", "miq", "diplom"
}

NEWS_CATEGORIES = {
    "sosial", "siyasət", "hadisə", "cəmiyyət", "iqtisadiyyat", "dünya",
    "ölkə", "təhsil", "elm", "mədəniyyət", "idman", "kriminal",
    "region", "bölgə", "maraqlı", "şou", "sağlamlıq", "texnologiya",
}

TITLE_NOISE_LABELS = {
    "video", "foto", "yenilənib", "yeniləndi", "canlı", "son dəqiqə",
    "açıqlama", "rəsmi", "eksklüziv", "müsahibə", "reportaj",
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

LOCAL_ONLY_DOMAINS = {"localhost", "127.0.0.1", "0.0.0.0", "::1"}
REQUEST_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,application/rss+xml;q=0.8,application/atom+xml;q=0.8,*/*;q=0.7",
    "Accept-Language": "az-AZ,az;q=0.9,en-US;q=0.8",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
    "Referer": "https://www.google.com/",
}

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
    text = unescape(str(text or ""))
    return re.sub(r"\s+", " ", repair_mojibake(text)).strip()


MOJIBAKE_MARKERS = ("\u00c3", "\u00c2", "\u00e2", "\u00ce", "\u0413", "\u0414", "\u0415", "\u0419", "\ufffd")


def mojibake_score(text, original_length=None):
    value = str(text or "")
    score = value.count("\ufffd") * 8
    for marker in MOJIBAKE_MARKERS:
        score += value.count(marker) * 3
    score += len(re.findall(r"[\u00c0-\u00ff]{2,}", value))
    if original_length is not None:
        score += abs(len(value) - original_length) * 2
    return score


def repair_mojibake(text):
    value = str(text or "")
    if not value or not any(marker in value for marker in MOJIBAKE_MARKERS):
        return value

    candidates = [value]
    for encoding in ("latin1", "cp1252", "cp1251"):
        try:
            candidates.append(value.encode(encoding).decode("utf-8"))
        except Exception:
            pass

    original_length = len(value)
    return min(candidates, key=lambda item: mojibake_score(item, original_length))


def decode_response_text(response):
    raw = response.content or b""
    candidates = []

    for encoding in (
        response.encoding,
        response.apparent_encoding,
        "utf-8",
        "windows-1254",
        "cp1254",
        "iso-8859-9",
        "windows-1251",
        "cp1251",
    ):
        if not encoding:
            continue
        try:
            decoded = raw.decode(encoding, errors="replace")
            candidates.append(repair_mojibake(decoded))
        except Exception:
            continue

    if not candidates:
        return repair_mojibake(response.text)

    return min(candidates, key=mojibake_score)


def normalize_text(text):
    text = str(text or "").lower()
    text = text.replace("i̇", "i")
    return text


def get_domain(url):
    return urlparse(url or "").netloc.replace("www.", "").lower()


def is_local_only_url(url):
    domain = get_domain(url).split(":")[0]
    return domain in LOCAL_ONLY_DOMAINS


def get_base_url(url):
    parsed = urlparse(url or "")
    if not parsed.scheme or not parsed.netloc:
        return ""
    return f"{parsed.scheme}://{parsed.netloc}"


def normalize_link(link):
    link = clean_text(link)
    if not link:
        return ""
    parsed = urlparse(link)
    if not parsed.scheme or not parsed.netloc:
        return link.split("#")[0].rstrip("/").lower()

    tracking_prefixes = ("utm_",)
    tracking_params = {
        "fbclid", "gclid", "dclid", "yclid", "mc_cid", "mc_eid",
        "igshid", "ref", "ref_src", "spm", "ved", "usg",
    }
    kept_query = []
    for key, value in parse_qsl(parsed.query, keep_blank_values=True):
        lower_key = key.lower()
        if lower_key in tracking_params or lower_key.startswith(tracking_prefixes):
            continue
        kept_query.append((key, value))

    netloc = parsed.netloc.lower()
    if netloc.startswith("www."):
        netloc = netloc[4:]
    path = parsed.path.rstrip("/")
    query = urlencode(kept_query, doseq=True)
    return urlunparse((parsed.scheme.lower(), netloc, path, "", query, "")).lower()


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


def load_patterns():
    return load_json_file(PATTERNS_FILE, {})


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


def update_source_health(site, result):
    if not SOURCE_HEALTH_ENABLED or not supabase_ready():
        return

    source_id = site.get("id")
    if not source_id:
        return

    reason = clean_text(result.get("reason") or "unknown")
    candidates = int(result.get("candidates", 0) or 0)
    sent = int(result.get("sent", 0) or 0)
    now = datetime.now(BAKU_TZ).isoformat()
    payload = {
        "last_checked_at": now,
        "last_result": reason,
    }

    hard_fail_reasons = {"site_error", "blocked", "dead", "failed"}
    readable_reasons = {
        "sent", "duplicate", "old_news", "no_date", "no_monitor_match",
        "no_telegram_recipient", "no_keyword_match",
    }

    if reason in hard_fail_reasons:
        try:
            response = requests.post(
                f"{SUPABASE_URL}/rest/v1/rpc/increment_source_fail",
                headers=supabase_headers(),
                json={"p_source_id": source_id, "p_reason": reason},
                timeout=REQUEST_TIMEOUT,
            )
            if response.status_code in {200, 204}:
                return
            print(f"Source fail saygac xetasi: {response.status_code} | {response.text[:200]}", flush=True)
        except Exception as exc:
            print(f"Source fail saygac istisnasi: {exc}", flush=True)
        payload["last_error"] = reason
    elif candidates > 0 or reason in readable_reasons:
        payload["last_success_at"] = now
        payload["last_error"] = None
        payload["consecutive_fail_count"] = 0
    else:
        payload["last_error"] = reason

    if sent > 0 or candidates > 0:
        payload["last_article_found_at"] = now

    try:
        response = requests.patch(
            f"{SUPABASE_URL}/rest/v1/sources",
            headers=supabase_headers({"Prefer": "return=minimal"}),
            params={"id": f"eq.{source_id}"},
            json=payload,
            timeout=REQUEST_TIMEOUT,
        )
        if response.status_code not in {200, 204}:
            print(f"Source health yazma xetasi: {response.status_code} | {response.text[:200]}", flush=True)
    except Exception as exc:
        print(f"Source health istisnasi: {exc}", flush=True)


def send_telegram(message, chat_id=None):
    target_chat_id = clean_text(chat_id or CHAT_ID)
    if not BOT_TOKEN or not target_chat_id:
        print("BOT_TOKEN və ya CHAT_ID yoxdur.", flush=True)
        return False

    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    try:
        with TELEGRAM_LOCK:
            response = requests.post(
                url,
                data={
                    "chat_id": target_chat_id,
                    "text": message,
                    "disable_web_page_preview": False,
                },
                timeout=15,
            )
        print("Telegram:", response.status_code, flush=True)
        if response.status_code != 200:
            print(f"Telegram cavabi: {response.text[:500]}", flush=True)

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


def load_telegram_offset():
    try:
        if not os.path.exists(TELEGRAM_OFFSET_FILE):
            return 0
        with open(TELEGRAM_OFFSET_FILE, "r", encoding="utf-8") as fh:
            return int((json.load(fh) or {}).get("offset", 0))
    except Exception:
        return 0


def save_telegram_offset(offset):
    try:
        with open(TELEGRAM_OFFSET_FILE, "w", encoding="utf-8") as fh:
            json.dump({"offset": offset}, fh)
    except Exception as exc:
        print(f"Telegram offset yazilmadi: {exc}", flush=True)


def connect_telegram_users_from_updates():
    if not BOT_TOKEN or not supabase_ready():
        return
    try:
        response = requests.get(
            f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates",
            params={"offset": load_telegram_offset(), "timeout": 0, "allowed_updates": json.dumps(["message"])},
            timeout=REQUEST_TIMEOUT,
        )
        if response.status_code != 200:
            print(f"Telegram getUpdates xetasi: {response.status_code} | {response.text[:200]}", flush=True)
            return
        updates = response.json().get("result", []) or []
        next_offset = None
        for update in updates:
            update_id = update.get("update_id")
            if update_id is not None:
                next_offset = max(next_offset or 0, update_id + 1)
            message = update.get("message") or {}
            text = clean_text(message.get("text"))
            if not text.startswith("/start"):
                continue
            parts = text.split(maxsplit=1)
            if len(parts) < 2:
                continue
            user_id = parts[1].strip()
            if not re.fullmatch(r"[0-9a-fA-F-]{36}", user_id):
                continue
            chat = message.get("chat") or {}
            chat_id = clean_text(chat.get("id"))
            if not chat_id:
                continue
            payload = {
                "user_id": user_id,
                "telegram_chat_id": chat_id,
                "updated_at": datetime.now(BAKU_TZ).isoformat(),
            }
            upsert = requests.post(
                f"{SUPABASE_URL}/rest/v1/user_profiles",
                headers=supabase_headers({"Prefer": "resolution=merge-duplicates"}),
                json=payload,
                timeout=REQUEST_TIMEOUT,
            )
            if upsert.status_code in (200, 201, 204):
                USER_TELEGRAM_CACHE[user_id] = chat_id
                print(f"Telegram profil baglandi: user={user_id} | chat={chat_id}", flush=True)
                send_telegram("Telegram bildirisleri aktiv edildi.", chat_id=chat_id)
            else:
                print(f"Telegram profil yazma xetasi: {upsert.status_code} | {upsert.text[:200]}", flush=True)
        if next_offset is not None:
            save_telegram_offset(next_offset)
    except Exception as exc:
        print(f"Telegram connect istisnasi: {exc}", flush=True)


def clean_title_for_message(title):
    title = clean_text(title)
    if not title:
        return ""

    separators = r"[-\u2013\u2014|:\u2022\u00bb]+"
    edge_chars = " \t\n\r-\u2013\u2014|:\u2022\u00bb"
    category_group = r"(?:" + "|".join(re.escape(c) for c in sorted(NEWS_CATEGORIES, key=len, reverse=True)) + r")"
    noise_group = r"(?:" + "|".join(re.escape(c) for c in sorted(TITLE_NOISE_LABELS, key=len, reverse=True)) + r")"
    az_months = r"yanvar|fevral|mart|aprel|may|iyun|iyul|avqust|sentyabr|oktyabr|noyabr|dekabr"

    title = re.sub(r"\s+", " ", title).strip(edge_chars)

    for _ in range(3):
        before = title
        title = re.sub(rf"^\s*{category_group}\s*(?:{separators}|/)\s*", "", title, flags=re.IGNORECASE)
        title = re.sub(rf"^\s*{noise_group}\s*(?:{separators})\s*", "", title, flags=re.IGNORECASE)
        title = re.sub(r"^\s*\[?\d{1,2}[./-]\d{1,2}[./-]\d{2,4}\]?\s*(?:[-\u2013\u2014|:])?\s*", "", title)
        title = re.sub(r"^\s*\[?\d{1,2}[:.]\d{2}\]?\s*(?:[-\u2013\u2014|:])?\s*", "", title)
        title = re.sub(r"^\.\d{2,4}\s*(?:[-\u2013\u2014|:])?\s*", "", title)
        title = re.sub(r"^\s*\[?\d{4}-\d{2}-\d{2}(?:[T\s]\d{2}:\d{2}(?::\d{2})?(?:[+-]\d{2}:?\d{2})?)?\]?\s*(?:[-\u2013\u2014|:])?\s*", "", title)
        if title == before:
            break

    title = re.sub(rf"\s*(?:{separators})\s*{category_group}\s*$", "", title, flags=re.IGNORECASE)
    title = re.sub(rf"\s*(?:{separators})\s*{noise_group}\s*$", "", title, flags=re.IGNORECASE)
    title = re.sub(r"\s+\[?(?:VIDEO|FOTO|YEN?L?N?B|CANLI)\]?\s*$", "", title, flags=re.IGNORECASE)
    title = re.sub(r"\s+\d{1,2}\s+(?:" + az_months + r")\s+\d{4}\s*,?\s*\d{1,2}[:.]\d{2}$", "", title, flags=re.IGNORECASE)
    title = re.sub(r"\s+\d{1,2}\s+(?:" + az_months + r")\s+\d{4}$", "", title, flags=re.IGNORECASE)
    title = re.sub(r"\s+\d{1,2}[./-]\d{1,2}[./-]\d{2,4}\s*,?\s*\d{1,2}[:.]\d{2}$", "", title)
    title = re.sub(r"\s+\d{1,2}[./-]\d{1,2}[./-]\d{2,4}$", "", title)
    title = re.sub(r"\s+\d{4}-\d{2}-\d{2}(?:[T\s]\d{2}:\d{2}(?::\d{2})?(?:[+-]\d{2}:?\d{2})?)?$", "", title)
    title = re.sub(r"\s*(?:[-\u2013\u2014|])\s*(?:[A-Za-z0-9_-]+\.)?az\s*$", "", title, flags=re.IGNORECASE)
    title = re.sub(r"\s*(?:[-\u2013\u2014|])\s*(?:Report|Oxu|Qafqazinfo|Trend|APA|Azertac|Unikal|Modern|Publika|Yeni ?a?|Yeni Cag)\s*$", "", title, flags=re.IGNORECASE)
    title = re.sub(r"\s*[-\u2013\u2014|:\u2022\u00bb]+\s*$", "", title)
    title = re.sub(r"^[-\u2013\u2014|:\u2022\u00bb]+\s*", "", title)
    title = re.sub(r"\s+", " ", title).strip()

    return clean_text(title)


def is_non_news_title(title):
    cleaned = clean_title_for_message(title)
    normalized = normalize_text(cleaned)
    category_values = {normalize_text(item) for item in NEWS_CATEGORIES}
    noise_values = {normalize_text(item) for item in TITLE_NOISE_LABELS}
    if not cleaned or normalized in category_values or normalized in noise_values:
        return True
    if len(cleaned) < 12:
        return True
    if parse_datetime_to_baku(cleaned) and len(cleaned.split()) <= 5:
        return True
    if re.fullmatch(r"[\d\s:./,\-|\u2013\u2014]+", cleaned):
        return True
    if re.fullmatch(r"(?:" + "|".join(re.escape(c) for c in NEWS_CATEGORIES) + r")\s+[\d\s:./,\-|\u2013\u2014]+", cleaned, flags=re.IGNORECASE):
        return True
    return False


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


def keyword_matches_title(keyword, title_text):
    keyword = normalize_text(keyword).strip()
    title_text = normalize_text(title_text)
    if not keyword:
        return False

    word_chars = r"a-zA-Z0-9É™Ã¶ÄŸÃ¼Ã§Ä±ÆÃ–ÄžÃœÃ‡ÅžÅŸÄ°Ä±"
    strict_match = keyword in STRICT_WORDS or len(keyword) <= 4

    if strict_match or " " in keyword:
        pattern = rf"(?<![{word_chars}])" + re.escape(keyword) + rf"(?![{word_chars}])"
        return bool(re.search(pattern, title_text, flags=re.IGNORECASE))

    return keyword in title_text


def keyword_match(title, keywords):
    title_lower = normalize_text(title)
    all_keywords = set()

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


def google_news_when_window():
    if GOOGLE_NEWS_FALLBACK_HOURS < 24:
        return f"{GOOGLE_NEWS_FALLBACK_HOURS}h"
    days = max(1, (GOOGLE_NEWS_FALLBACK_HOURS + 23) // 24)
    return f"{days}d"


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


def contains_known_az_month(value):
    text = normalize_date_text(clean_text(value)).lower()
    for token in re.findall(r"[a-z]+", text):
        if month_number(token):
            return True
    return False


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

    numeric_time_first = re.search(
        r"(?<!\d)(\d{1,2})\s*[:.]\s*(\d{2})\D{0,20}(\d{1,2})\s*[./-]\s*(\d{1,2})\s*[./-]\s*(\d{4})",
        original,
    )
    if numeric_time_first:
        hour, minute, day, month, year = numeric_time_first.groups()
        return safe_datetime(year, month, day, hour, minute)

    numeric_date = re.search(
        r"(?<!\d)(\d{1,2})\s*[./-]\s*(\d{1,2})\s*[./-]\s*(\d{4})(?:\s+(\d{1,2})\s*[:.]\s*(\d{2}))?",
        original,
    )
    if numeric_date:
        day, month, year, hour, minute = numeric_date.groups()
        return safe_datetime(year, month, day, hour or 0, minute or 0)

    text = normalize_date_text(original)

        # Mətn içində Azərbaycan tarix+saat formatı:
    # "Yerləşdirilmə tarixi : 09 İyun 2026 14:20"
    # "article_tarix: 10 İyun 2026, 12:35 / Konfranslar"
    pattern = r"(\d{1,2})\s+([a-z]+)\s+(\d{4})\D{0,20}(\d{1,2})[:.](\d{2})"
    m = re.search(pattern, text, re.IGNORECASE)
    if m:
        day, month_name, year, hour, minute = m.groups()
        month = month_number(month_name)

        if month:
            return safe_datetime(year, month, day, hour, minute)

    # Mətn içində qısa ay + tarix+saat:
    # "İyn 08, 2026 | 20:00"
    # "May 25, 2026 | 12:00"
    pattern = r"([a-z]+)\s+(\d{1,2})\s*,?\s+(\d{4})\D{0,20}(\d{1,2})[:.](\d{2})"
    m = re.search(pattern, text, re.IGNORECASE)
    if m:
        month_name, day, year, hour, minute = m.groups()
        month = month_number(month_name)

        if month:
            return safe_datetime(year, month, day, hour, minute)

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


def has_strong_date_signal(text):
    value = clean_text(text)
    normalized = normalize_date_text(value)
    if re.search(r"\d{4}-\d{2}-\d{2}", value):
        return True
    if re.search(r"(?<!\d)\d{1,2}[./-]\d{1,2}[./-]\d{4}", value):
        return True
    if re.search(r"\d{1,2}\s+[a-z]+\s+\d{4}", normalized, re.IGNORECASE):
        return True
    if re.search(r"[a-z]+\s+\d{1,2}\s*,?\s+\d{4}", normalized, re.IGNORECASE):
        return True
    if re.search(r"\b(mon|tue|wed|thu|fri|sat|sun),?\s+\d{1,2}\s+\w+\s+\d{4}", value, re.IGNORECASE):
        return True
    return False


def is_realistic_publish_datetime(dt):
    if not dt:
        return False
    now_baku = datetime.now(BAKU_TZ)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=BAKU_TZ)
    else:
        dt = dt.astimezone(BAKU_TZ)
    return 2020 <= dt.year <= now_baku.year + 1


def parse_datetime_to_baku(published_time):
    text = clean_text(str(published_time or ""))

    if not text or "tarix tapılmadı" in text.lower():
        return None

    # 1) ISO formatı birinci oxuyuruq:
    # 2026-06-11T15:50:00+04:00
    # 2026-06-11 15:50:00
    iso_match = re.search(
        r"\d{4}-\d{2}-\d{2}[T\s]\d{2}:\d{2}(?::\d{2})?(?:[+-]\d{2}:?\d{2}|Z)?",
        text,
    )

    if iso_match:
        iso_text = iso_match.group(0)

        try:
            if iso_text.endswith("Z"):
                iso_text = iso_text.replace("Z", "+00:00")

            dt = datetime.fromisoformat(iso_text)

            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=BAKU_TZ)
            else:
                dt = dt.astimezone(BAKU_TZ)

            return dt if is_realistic_publish_datetime(dt) else None

        except Exception:
            pass

    # 2) RFC/RSS formatı:
    # Tue, 09 Jun 2026 08:42:36 +0000
    # Sun, 11 Aug 2024 20:00:00 GMT
    try:
        dt = parsedate_to_datetime(text)

        if dt:
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=BAKU_TZ)
            else:
                dt = dt.astimezone(BAKU_TZ)

            return dt if is_realistic_publish_datetime(dt) else None
    except Exception:
        pass

    # 3) Azərbaycan formatları:
    az_dt = parse_az_datetime(text)

    if az_dt:
        return az_dt if is_realistic_publish_datetime(az_dt) else None
    if contains_known_az_month(text):
        return None

    # 4) Sonda ümumi parser.
    # Burada dayfirst=True saxlayırıq, amma ISO artıq yuxarıda tutulduğu üçün qarışmayacaq.
    if not has_strong_date_signal(text):
        return None

    try:
        dt = parser.parse(text, fuzzy=True, dayfirst=True)

        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=BAKU_TZ)
        else:
            dt = dt.astimezone(BAKU_TZ)

        return dt if is_realistic_publish_datetime(dt) else None

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

    if title_dt and article_dt and has_strong_date_signal(title):
        title_baku = title_dt.astimezone(BAKU_TZ) if title_dt.tzinfo else title_dt.replace(tzinfo=BAKU_TZ)
        article_baku = article_dt.astimezone(BAKU_TZ) if article_dt.tzinfo else article_dt.replace(tzinfo=BAKU_TZ)
        if abs(article_baku - title_baku) > timedelta(hours=NEWS_TIME_LIMIT_HOURS):
            return title_baku.strftime("%d.%m.%Y %H:%M")

    if article_dt:
        return article_dt.strftime("%d.%m.%Y %H:%M")
    if title_dt:
        return title_dt.strftime("%d.%m.%Y %H:%M")
    return None


def extract_publish_time_from_html(page_html):
    soup = BeautifulSoup(page_html, "html.parser")

    meta_selectors = [
        ("meta", {"property": "article:published_time"}),
        ("meta", {"property": "article:modified_time"}),
        ("meta", {"property": "og:updated_time"}),
        ("meta", {"name": "article:published_time"}),
        ("meta", {"name": "pubdate"}),
        ("meta", {"name": "publishdate"}),
        ("meta", {"name": "publish_date"}),
        ("meta", {"name": "date"}),
        ("meta", {"name": "DC.date.issued"}),
        ("meta", {"itemprop": "datePublished"}),
        ("meta", {"itemprop": "dateModified"}),
    ]
    for tag_name, attrs in meta_selectors:
        tag = soup.find(tag_name, attrs=attrs)
        value = clean_text(tag.get("content", "")) if tag else ""
        if value and parse_datetime_to_baku(value):
            return value

    for script in soup.find_all("script", type=lambda value: value and "ld+json" in value.lower()):
        try:
            data = json.loads(script.string or script.get_text(" ", strip=True) or "{}")
        except Exception:
            continue

        stack = data if isinstance(data, list) else [data]
        while stack:
            item = stack.pop()
            if isinstance(item, list):
                stack.extend(item)
                continue
            if not isinstance(item, dict):
                continue

            for key in ("datePublished", "dateModified", "dateCreated", "uploadDate"):
                value = clean_text(item.get(key))
                if value and parse_datetime_to_baku(value):
                    return value

            graph = item.get("@graph")
            if isinstance(graph, list):
                stack.extend(graph)

    regex_patterns = [
        r'"(?:datePublished|dateModified|dateCreated|published_at|created_at|updated_at)"\s*:\s*"([^"]+)"',
        r"'(?:datePublished|dateModified|dateCreated|published_at|created_at|updated_at)'\s*:\s*'([^']+)'",
        r"(\d{4}-\d{2}-\d{2}[T\s]\d{2}:\d{2}(?::\d{2})?(?:[+-]\d{2}:?\d{2}|Z)?)",
        r"(\d{1,2}\s+[A-Za-zƏəÖöĞğÜüÇçŞşİı]+\s+\d{4}\D{0,20}\d{1,2}[:.]\d{2})",
        r"(\d{1,2}[./-]\d{1,2}[./-]\d{4}\D{0,20}\d{1,2}[:.]\d{2})",
        r"(\d{1,2}\s+[A-Za-zƏəÖöĞğÜüÇçŞşİı]+\s+\d{4})",
    ]
    for pattern in regex_patterns:
        for match in re.finditer(pattern, page_html, flags=re.IGNORECASE):
            value = clean_text(match.group(1))
            if value and parse_datetime_to_baku(value):
                return value

    return None


def extract_publish_time_from_article(article_url):
    headers = REQUEST_HEADERS
    try:
        response = requests.get(article_url, headers=headers, timeout=REQUEST_TIMEOUT)
        if response.status_code != 200:
            return None
        page_html = decode_response_text(response)
        metadata_time = extract_publish_time_from_html(page_html)
        if metadata_time:
            return metadata_time
        tree = html.fromstring(page_html)
        possible_xpaths = [
            "//time/@datetime", "//time/text()",
            "//meta[@property='article:published_time']/@content",
            "//meta[@property='article:modified_time']/@content",
            "//meta[@property='og:updated_time']/@content",
            "//meta[@name='article:published_time']/@content",
            "//meta[@itemprop='datePublished']/@content",
            "//meta[@itemprop='dateModified']/@content",
            "//meta[@name='pubdate']/@content", "//meta[@name='date']/@content",
            "//meta[@name='DC.date.issued']/@content", "//meta[@name='publishdate']/@content",
            "//meta[@name='publish_date']/@content",
            "//*[@datetime]/@datetime",
            "//*[contains(translate(@class,'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'publish')]/text()",
            "//span[contains(@class,'date')]/text()", "//div[contains(@class,'date')]/text()",
            "//span[contains(@class,'time')]/text()", "//div[contains(@class,'time')]/text()",
            "//*[contains(@class,'date')]/text()", "//*[contains(@class,'time')]/text()",
        ]
        for xpath in possible_xpaths:
            result = tree.xpath(xpath)
            if result:
                value = clean_text(str(result[0]))
                if len(value) > 5 and parse_datetime_to_baku(value):
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
    if "news.google.com/" in link_lower:
        return True
    return any(pattern in link_lower for pattern in ARTICLE_URL_PATTERNS)


def is_bad_link(title, link):
    title_lower = title.lower()
    link_lower = link.lower()
    is_google_news_link = "news.google.com/" in link_lower
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
    if not is_google_news_link and any(word in link_lower for word in bad_words):
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
    raw_title = title
    title_for_keyword = clean_title_for_message(title)
    if is_non_news_title(title_for_keyword):
        return
    page_domain = get_domain(page_url)
    link_domain = get_domain(link)
    is_google_news_link = "news.google.com" in {page_domain, link_domain}
    if page_domain and link_domain and page_domain != link_domain:
        # Google News fallback bəzən orijinal linki news.google yönləndiricisi ilə verir, ona görə source domain yoxdursa keçmirik.
        if not is_google_news_link:
            return
    if is_bad_link(title, link):
        return
    if not is_article_like_link(link):
        return
    item = {
        "title": title_for_keyword,
        "raw_title": raw_title,
        "clean_title": title_for_keyword,
        "link": link,
        "source": link_domain or page_domain,
        "matched_keywords": [],
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
    return list(dict.fromkeys([
        item for item in rss_links
        if item and item.startswith("http") and not is_local_only_url(item)
    ]))[:8]


def extract_links_from_rss(site, rss_urls):
    results = []
    keywords = site.get("keywords", [])
    page_url = site.get("url") or site.get("base_url") or ""
    for rss_url in rss_urls:
        if not rss_url:
            continue
        if is_local_only_url(rss_url):
            print(f"RSS local URL keçildi: {rss_url}", flush=True)
            continue
        try:
            response = requests.get(
                rss_url,
                headers=REQUEST_HEADERS,
                timeout=REQUEST_TIMEOUT,
                allow_redirects=True,
            )
            if response.status_code != 200:
                continue
            feed = feedparser.parse(response.content)
            if not feed.entries:
                continue
            site["_rss_feed_had_entries"] = True
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
    soup = BeautifulSoup(page_html, "html.parser")
    results = []

    try:
        blocks = soup.select(selector)
    except Exception as e:
        print("Selector xətası:", e, flush=True)
        return []

    print(f"Selector blok sayı: {len(blocks)} | {selector}", flush=True)

    # Çox böyük selector nəticələrində yalnız ilk blokları yoxlayırıq.
    # Məqsəd köhnə arxivlərə ilişib botun donmasının qarşısını almaqdır.
    blocks = blocks[:MAX_LINKS_PER_SITE * 3]

    for block in blocks:
        links = block.find_all("a", href=True)

        if getattr(block, "name", None) == "a" and block.get("href"):
            links.append(block)

        for a in links:
            title = clean_text(a.get_text(" ", strip=True))
            link = urljoin(page_url, a["href"])

            add_item(results, page_url, title, link, keywords)

            if len(results) >= MAX_LINKS_PER_SITE:
                return unique_items(results)

    return unique_items(results)


def extract_links_from_xpath(page_url, page_html, xpaths, keywords):
    results = []
    if not xpaths:
        return []
    try:
        tree = html.fromstring(page_html)
    except Exception as exc:
        print("HTML parse xətası:", exc, flush=True)
        return []
    invalid_count = 0
    for xpath in xpaths:
        xpath = clean_text(xpath)
        if not xpath:
            continue
        try:
            blocks = tree.xpath(xpath)
        except Exception:
            invalid_count += 1
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
    if invalid_count:
        print(f"XPath invalid ifade kecildi: {invalid_count}", flush=True)
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

    links = soup.find_all("a", href=True)

    # Bütün arxivi yoxlamırıq. İlk 150 link kifayətdir.
    for a in links[:150]:
        title = clean_text(a.get_text(" ", strip=True))
        link = urljoin(page_url, a["href"])

        add_item(results, page_url, title, link, keywords)

        if len(results) >= MAX_LINKS_PER_SITE:
            break

    return unique_items(results)


def extract_links_from_sitemap(site):
    sitemap_url = site.get("latest_url") or urljoin(site.get("base_url", "").rstrip("/") + "/", "sitemap.xml")
    keywords = site.get("keywords", [])
    results = []
    try:
        response = requests.get(sitemap_url, headers=REQUEST_HEADERS, timeout=REQUEST_TIMEOUT)
        if response.status_code != 200:
            return []
        urls = re.findall(r"<loc>(.*?)</loc>", response.text, flags=re.IGNORECASE)
        for url in urls[:300]:
            if not any(pattern in url.lower() for pattern in ARTICLE_URL_PATTERNS):
                continue
            # Sitemap-də başlıq yoxdur; məqaləni açıb title/meta alırıq.
            try:
                article = requests.get(url, headers=REQUEST_HEADERS, timeout=REQUEST_TIMEOUT)
                if article.status_code != 200:
                    continue
                soup = BeautifulSoup(decode_response_text(article), "html.parser")
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
    headers = REQUEST_HEADERS
    try:
        print(f"Sayt açılır: {url}", flush=True)
        response = requests.get(url, headers=headers, timeout=REQUEST_TIMEOUT, allow_redirects=True)
        print(f"Status: {response.status_code}", flush=True)
        if response.status_code != 200:
            return None
        return decode_response_text(response)
    except Exception as exc:
        print(f"Sayt xətası: {url} | {exc}", flush=True)
        return None


def fetch_site(site, patterns_data):
    page_url = site["url"]
    base_url = clean_text(site.get("base_url", "")) or page_url
    rss_url = clean_text(site.get("rss_url", ""))
    selector = site.get("selector")
    xpaths = site.get("xpaths", []) or parse_article_patterns(site.get("article_pattern"))
    keywords = site.get("keywords", [])
    monitor_method = clean_text(site.get("monitor_method", "")).lower()
    if monitor_method == "xpath":
        monitor_method = "xpath_pattern"

    headers = REQUEST_HEADERS

    print(
        f"Metod: {monitor_method or 'auto'} | {site.get('name')} | {page_url}",
        flush=True,
    )

    # 1) Google News fallback:
    # Bu metodda əsas sayt açılmır. Yalnız Google News RSS oxunur.
    # Report.az kimi 403 verən saytlar üçün əsas məqsəd də budur.
    if monitor_method == "google_news_fallback":
        google_rss_urls = []

        if rss_url and "news.google.com/rss" in rss_url:
            google_rss_urls.append(rss_url)
        else:
            domain = get_domain(base_url or page_url)

            if domain:
                when_window = google_news_when_window()
                google_rss_urls.append(
                    f"https://news.google.com/rss/search?q=site%3A{domain}%20when%3A{when_window}&hl=az&gl=AZ&ceid=AZ:az"
                )

        print(
            f"Google News fallback yalnız RSS oxuyur: {google_rss_urls[0] if google_rss_urls else 'RSS yoxdur'}",
            flush=True,
        )

        if google_rss_urls:
            return extract_links_from_rss(site, google_rss_urls)

        return []

    # 2) Blocked/dead/failed:
    # Bu metodlarda əsas sayta girmirik ki, 403/404 spam və vaxt itkisi olmasın.
    if monitor_method in {"blocked", "dead", "failed"}:
        print(f"Metod {monitor_method}: əsas sayt əlavə gəzilmir.", flush=True)
        return []

    # 3) RSS metodları:
    # RSS varsa əvvəl RSS oxunur. Uyğun nəticə çıxsa, sayt əlavə gəzilmir.
    if monitor_method in {"rss", "rss_discovered"}:
        rss_candidates = []

        if rss_url:
            rss_candidates.append(rss_url)

        if not rss_candidates:
            rss_candidates.extend([
                urljoin(base_url.rstrip("/") + "/", "rss"),
                urljoin(base_url.rstrip("/") + "/", "rss.xml"),
                urljoin(base_url.rstrip("/") + "/", "feed"),
                urljoin(base_url.rstrip("/") + "/", "feed.xml"),
            ])

        print(f"RSS-only yoxlanır: {rss_candidates[:3]}", flush=True)

        items = extract_links_from_rss(site, rss_candidates)


        if items:
            return unique_items(items)

        if site.pop("_rss_feed_had_entries", False):
            print("RSS feed oxundu, amma keyword uygun namized tapilmadi. HTML fallback edilmir.", flush=True)
            return []

        print("RSS nəticə vermədi, HTML fallback yoxlanacaq.", flush=True)

    # 4) Sitemap:
    # Səndə olan extract_links_from_sitemap(site) funksiyasından istifadə edir.
    if monitor_method == "sitemap":
        items = extract_links_from_sitemap(site)

        if items:
            return unique_items(items)

        return []

    # 5) HTML əsaslı metodlar
    try:
        print(f"Sayt açılır: {page_url}", flush=True)

        r = requests.get(
            page_url,
            headers=headers,
            timeout=REQUEST_TIMEOUT,
            allow_redirects=True,
        )

        print(f"Status: {r.status_code}", flush=True)

        if r.status_code != 200:
            return []

        page_html = decode_response_text(r)

    except Exception as e:
        print(f"Sayt xətası: {page_url} | {e}", flush=True)
        return []

    domain = get_domain(page_url)
    site_patterns = patterns_data.get(domain, [])

    # 6) Selector metodu
    if monitor_method == "selector" and selector:
        items = extract_links_by_selector(page_url, page_html, selector, keywords)

        if items:
            return unique_items(items)

        print("Selector nəticə vermədi, fallback yoxlanacaq.", flush=True)

    # 7) XPath metodu
    if monitor_method == "xpath_pattern" and xpaths:
        items = extract_links_from_xpath(page_url, page_html, xpaths, keywords)

        if items:
            return unique_items(items)

        print("XPath nəticə vermədi, fallback yoxlanacaq.", flush=True)

    # 8) Latest/Homepage/Recoverable/Auto metodları
    if monitor_method in {
        "latest_page",
        "homepage",
        "recoverable",
        "selector",
        "xpath_pattern",
        "rss",
        "rss_discovered",
        "",
    }:
        if not rss_url and monitor_method not in {"rss", "rss_discovered"}:
            discovered_rss = discover_rss_links(page_url, page_html)

            if discovered_rss:
                items = extract_links_from_rss(site, discovered_rss)

                if items:
                    return unique_items(items)

        items = []

        if selector and monitor_method != "selector":
            items = extract_links_by_selector(page_url, page_html, selector, keywords)

        if not items and xpaths and monitor_method != "xpath_pattern":
            items = extract_links_from_xpath(page_url, page_html, xpaths, keywords)

        if not items and site_patterns:
            print(f"Pattern fallback işləyir: {domain}", flush=True)
            items = extract_links_by_patterns(
                page_url,
                page_html,
                keywords,
                site_patterns,
            )

        if not items:
            print("HTML fallback işləyir...", flush=True)
            items = extract_links_fallback(page_url, page_html, keywords)

        return unique_items(items)

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


def get_user_telegram_chat_id(user_id):
    if not supabase_ready() or not user_id:
        return ""
    if user_id in USER_TELEGRAM_CACHE:
        return USER_TELEGRAM_CACHE[user_id]
    try:
        response = requests.get(
            f"{SUPABASE_URL}/rest/v1/user_profiles",
            headers=supabase_headers(),
            params={"select": "telegram_chat_id", "user_id": f"eq.{user_id}", "limit": "1"},
            timeout=REQUEST_TIMEOUT,
        )
        if response.status_code == 200 and response.json():
            telegram_chat_id = clean_text(response.json()[0].get("telegram_chat_id"))
        else:
            telegram_chat_id = ""
        USER_TELEGRAM_CACHE[user_id] = telegram_chat_id
        return telegram_chat_id
    except Exception as exc:
        print(f"User profile Telegram oxuma istisnasi: {exc}", flush=True)
        USER_TELEGRAM_CACHE[user_id] = ""
        return ""


def cleanup_old_monitor_data_if_needed():
    global LAST_MONITOR_CLEANUP
    if not supabase_ready():
        return
    now = datetime.now(BAKU_TZ)
    if LAST_MONITOR_CLEANUP and now - LAST_MONITOR_CLEANUP < timedelta(hours=24):
        return
    try:
        response = requests.post(
            f"{SUPABASE_URL}/rest/v1/rpc/cleanup_old_monitor_data",
            headers=supabase_headers(),
            json={"days_to_keep": MONITOR_DATA_RETENTION_DAYS},
            timeout=REQUEST_TIMEOUT,
        )
        if response.status_code in (200, 204):
            LAST_MONITOR_CLEANUP = now
            print(f"Monitor cleanup tamamlandi: {MONITOR_DATA_RETENTION_DAYS} gun saxlanildi", flush=True)
        else:
            print(f"Monitor cleanup xetasi: {response.status_code} | {response.text[:200]}", flush=True)
    except Exception as exc:
        print(f"Monitor cleanup istisnasi: {exc}", flush=True)


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


def load_active_monitor_keywords():
    if not supabase_ready():
        return None
    try:
        response = requests.get(
            f"{SUPABASE_URL}/rest/v1/monitor_keywords",
            headers=supabase_headers(),
            params={
                "select": "id,keyword,match_type,monitor_id,user_monitors(id,name,user_id,status,notify_telegram,telegram_chat_id)"
            },
            timeout=REQUEST_TIMEOUT,
        )
        if response.status_code != 200:
            print(f"Monitor keyword cache oxuma xetasi: {response.status_code} | {response.text[:200]}", flush=True)
            return None
        rows = response.json() or []
        active_rows = [row for row in rows if (row.get("user_monitors") or {}).get("status") == "active"]
        print(f"Aktiv monitor keyword cache: {len(active_rows)}", flush=True)
        return active_rows
    except Exception as exc:
        print(f"Monitor keyword cache istisnasi: {exc}", flush=True)
        return None

def find_matching_user_monitors(title, monitor_keywords_cache=None):
    if not supabase_ready():
        return []
    title_text = normalize_text(title)
    try:
        if monitor_keywords_cache is None:
            response = requests.get(
                f"{SUPABASE_URL}/rest/v1/monitor_keywords",
                headers=supabase_headers(),
                params={
                    "select": "id,keyword,match_type,monitor_id,user_monitors(id,name,user_id,status,notify_telegram,telegram_chat_id)"
                },
                timeout=REQUEST_TIMEOUT,
            )
            if response.status_code != 200:
                print(f"Monitor keyword oxuma xetasi: {response.status_code} | {response.text[:200]}", flush=True)
                return []
            keywords = response.json() or []
        else:
            keywords = monitor_keywords_cache
        matched_monitors = []
        seen_matches = set()
        for row in keywords:
            monitor = row.get("user_monitors") or {}
            if monitor.get("status") != "active":
                continue
            keyword_original = row.get("keyword", "")
            keyword = normalize_text(keyword_original)
            if not keyword_matches_title(keyword, title_text):
                continue
            match_key = (row.get("monitor_id"), keyword)
            if match_key in seen_matches:
                continue
            seen_matches.add(match_key)
            matched_monitors.append(
                {
                    "monitor_id": row.get("monitor_id"),
                    "monitor_name": monitor.get("name") or "Monitor",
                    "keyword": keyword_original,
                    "telegram_chat_id": monitor.get("telegram_chat_id") or get_user_telegram_chat_id(monitor.get("user_id")),
                    "notify_telegram": monitor.get("notify_telegram") is not False,
                }
            )
        return matched_monitors
    except Exception as exc:
        print(f"Monitor keyword yoxlama istisnasi: {exc}", flush=True)
        return []


def match_user_monitors(item_id, title, monitor_keywords_cache=None):
    if not supabase_ready() or not item_id:
        return []
    title_text = normalize_text(title)
    try:
        if monitor_keywords_cache is None:
            response = requests.get(
                f"{SUPABASE_URL}/rest/v1/monitor_keywords",
                headers=supabase_headers(),
                params={
                    "select": "id,keyword,match_type,monitor_id,user_monitors(id,name,user_id,status,notify_telegram,telegram_chat_id)"
                },
                timeout=REQUEST_TIMEOUT,
            )
            if response.status_code != 200:
                print(f"Monitor keyword oxuma xətası: {response.status_code} | {response.text[:200]}", flush=True)
                return []
            keywords = response.json() or []
        else:
            keywords = monitor_keywords_cache
        matched_monitors = []
        seen_matches = set()
        for row in keywords:
            monitor = row.get("user_monitors") or {}
            monitor_status = monitor.get("status")
            if monitor_status != "active":
                continue
            keyword_original = row.get("keyword", "")
            keyword = normalize_text(keyword_original)
            if not keyword_matches_title(keyword, title_text):
                continue
            match_key = (row.get("monitor_id"), keyword)
            if match_key not in seen_matches:
                seen_matches.add(match_key)
                matched_monitors.append(
                    {
                        "monitor_id": row.get("monitor_id"),
                        "monitor_name": monitor.get("name") or "Monitor",
                        "keyword": keyword_original,
                        "telegram_chat_id": monitor.get("telegram_chat_id") or get_user_telegram_chat_id(monitor.get("user_id")),
                        "notify_telegram": monitor.get("notify_telegram") is not False,
                    }
                )
            payload = {"monitor_id": row.get("monitor_id"), "item_id": item_id, "matched_keyword": keyword_original}
            match_response = requests.post(
                f"{SUPABASE_URL}/rest/v1/monitor_matches",
                headers=supabase_headers({"Prefer": "resolution=ignore-duplicates,return=representation"}),
                json=payload,
                timeout=REQUEST_TIMEOUT,
            )
            if match_response.status_code in (200, 201):
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
        return matched_monitors
    except Exception as exc:
        print(f"Monitor match istisnası: {exc}", flush=True)
        return []


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


def parse_article_patterns(value):
    if isinstance(value, list):
        return [clean_text(item) for item in value if clean_text(item)]
    text = clean_text(value)
    if not text:
        return []
    return [clean_text(item) for item in text.split(",") if clean_text(item)]


def load_sites():
    if not supabase_ready():
        print("Supabase bağlantısı yoxdur, sources oxunmadı.", flush=True)
        return []

    all_sites = []
    seen_urls = set()
    source_select = "id,name,base_url,latest_url,rss_url,status,source_type,trust_level,monitor_method,selector,article_pattern,discovery_status,discovery_score,notes"
    if SOURCE_HEALTH_ENABLED:
        source_select += ",last_checked_at,last_success_at,last_article_found_at,last_error,consecutive_fail_count,last_result"
    try:
        offset = 0
        page_size = 1000
        while True:
            response = requests.get(
                f"{SUPABASE_URL}/rest/v1/sources",
                headers=supabase_headers(),
                params={
                    "select": source_select,
                    "status": "eq.active",
                    "order": "name.asc",
                    "limit": str(page_size),
                    "offset": str(offset),
                },
                timeout=REQUEST_TIMEOUT,
            )
            if response.status_code != 200 and SOURCE_HEALTH_ENABLED:
                print("Source health sütunları oxunmadı, köhnə sources select ilə davam edilir.", flush=True)
                source_select = "id,name,base_url,latest_url,rss_url,status,source_type,trust_level,monitor_method,selector,article_pattern,discovery_status,discovery_score,notes"
                response = requests.get(
                    f"{SUPABASE_URL}/rest/v1/sources",
                    headers=supabase_headers(),
                    params={
                        "select": source_select,
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
                article_pattern = row.get("article_pattern") or ""
                xpaths = parse_article_patterns(article_pattern)
                if method == "xpath":
                    method = "xpath_pattern"

                # failed/dead mənbələri əsas monitorinqdə keçirik. blocked üçün Google News fallback varsa oxunacaq.
                if method in {"failed", "dead"}:
                    continue
                fail_count = int(row.get("consecutive_fail_count") or 0)
                if SOURCE_HEALTH_ENABLED and SOURCE_MAX_CONSECUTIVE_FAILS > 0 and fail_count >= SOURCE_MAX_CONSECUTIVE_FAILS:
                    print(f"Fail limiti keçildi, mənbə müvəqqəti skip: {row.get('name') or row.get('base_url')} | fail={fail_count}", flush=True)
                    continue

                url = latest_url or base_url or rss_url
                if not url:
                    continue
                if not url.startswith("http"):
                    url = "https://" + url.lstrip("/")
                if is_excluded_domain(url) or is_excluded_domain(base_url) or is_excluded_domain(rss_url):
                    print(f"Excluded domain skipped: {row.get('name') or url} | {url}", flush=True)
                    continue
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
                    "article_pattern": article_pattern,
                    "xpaths": xpaths,
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


def process_site(index, total, site, patterns_data, monitor_keywords_cache=None):
    started = time.time()
    result = {"sent": 0, "site": site.get("name"), "url": site.get("url"), "candidates": 0, "reason": "unknown"}
    print(f"[{index}/{total}] Yoxlanır: {site['name']} | {site['url']}", flush=True)
    try:
        items = fetch_site(site, patterns_data)
    except Exception as exc:
        print(f"❌ [{index}/{total}] {site['name']} | sayt emalı xətası: {exc}", flush=True)
        result["reason"] = "site_error"
        update_source_health(site, result)
        return result

    result["candidates"] = len(items)
    print(f"[{index}/{total}] {site['name']} | uyğun link sayı: {len(items)}", flush=True)

    if not items:
        result["reason"] = "no_candidate"
        print(f"📊 [{index}/{total}] {site['name']} | namizəd=0 | göndərildi=0 | nəticə=uyğun xəbər yoxdur | vaxt={time.time() - started:.1f}s", flush=True)
        update_source_health(site, result)
        return result

    for item in items[:site.get("limit", MAX_LINKS_PER_SITE)]:
        title = item["title"]
        link = item["link"]
        source = item["source"]
        matched_keywords = item.get("matched_keywords", [])

        if exists(link, title):
            result["reason"] = "duplicate"
            continue

        raw_title = item.get("raw_title") or title
        title_time = parse_datetime_to_baku(raw_title)
        rss_time = item.get("rss_published")
        article_time = extract_publish_time_from_article(link) or rss_time
        published_time = choose_publish_time(title, article_time)

        print(f"[{index}/{total}] Xəbər: {title[:80]} | title_tarix: {title_time} | rss_tarix: {rss_time} | article_tarix: {article_time} | seçilən tarix: {published_time} | Link: {link}", flush=True)

        if not published_time:
            result["reason"] = "no_date"
            continue
        if not is_recent_news(published_time):
            result["reason"] = "old_news"
            continue

        clean_title = item.get("clean_title") or clean_title_for_message(title)
        pre_matches = find_matching_user_monitors(clean_title, monitor_keywords_cache)
        if not pre_matches:
            result["reason"] = "no_monitor_match"
            continue

        monitor_item_id = save_to_vizual_monitor(site, item, clean_title, published_time)
        monitor_matches = match_user_monitors(monitor_item_id, clean_title, monitor_keywords_cache) if monitor_item_id else []
        matched_keywords = clean_matched_keywords([match.get("keyword") for match in monitor_matches])

        if not monitor_matches or not matched_keywords:
            result["reason"] = "no_monitor_match"
            continue

        matched_keywords_text = ", ".join(matched_keywords)
        target_chat_ids = []
        seen_target_chats = set()
        for monitor_match in monitor_matches:
            if not monitor_match.get("notify_telegram", True):
                continue
            chat_id = clean_text(monitor_match.get("telegram_chat_id"))
            if not chat_id or chat_id in seen_target_chats:
                continue
            seen_target_chats.add(chat_id)
            target_chat_ids.append(chat_id)

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

        if not target_chat_ids:
            result["reason"] = "no_telegram_recipient"
            print(f"Telegram alıcısı yoxdur, xəbər panel üçün saxlandı: {source} | {clean_title[:70]}", flush=True)
            update_source_health(site, result)
            return result

        sent_chats = set()
        sent_any = False
        for monitor_match in monitor_matches:
            if not monitor_match.get("notify_telegram", True):
                continue
            chat_id = clean_text(monitor_match.get("telegram_chat_id"))
            if not chat_id or chat_id not in seen_target_chats or chat_id in sent_chats:
                continue
            chat_matches = [match for match in monitor_matches if clean_text(match.get("telegram_chat_id")) == chat_id]
            chat_keywords = clean_matched_keywords([match.get("keyword") for match in chat_matches])
            chat_monitors = clean_matched_keywords([match.get("monitor_name") for match in chat_matches])
            chat_keywords_text = ", ".join(chat_keywords) or matched_keywords_text
            chat_monitors_text = ", ".join(chat_monitors) or "Monitor"
            chat_message = f"""
Yeni uygun xeber

Basliq:
{clean_title}

Menbe:
{source}

Monitor:
{chat_monitors_text}

Acar sozler:
{chat_keywords_text}

Tarix ve saat:
{published_time}

Link:
{link}
"""
            if send_telegram(chat_message, chat_id=chat_id):
                sent_chats.add(chat_id)
                sent_any = True

        if sent_any:
            print(f"✅ [{index}/{total}] Göndərildi: {source} | {clean_title[:70]} | Açar sözlər: {matched_keywords_text}", flush=True)
            result["sent"] = 1
            result["reason"] = "sent"
            time.sleep(1)
            update_source_health(site, result)
            return result

        release_reserved_news(link)
        result["reason"] = "telegram_error"

    print(f"📊 [{index}/{total}] {site['name']} | namizəd={len(items)} | göndərildi=0 | nəticə={result['reason']} | vaxt={time.time() - started:.1f}s", flush=True)
    update_source_health(site, result)
    return result


def check_sites():
    started = time.time()
    connect_telegram_users_from_updates()
    cleanup_old_monitor_data_if_needed()
    sites = load_sites()
    patterns_data = load_patterns()
    monitor_keywords_cache = load_active_monitor_keywords()
    total = len(sites)
    print(f"Yüklənən sayt sayı: {total}", flush=True)
    print(f"Monitorinq başladı | worker={MAX_WORKERS} | son {NEWS_TIME_LIMIT_HOURS} saat | {datetime.now(BAKU_TZ).strftime('%d.%m.%Y %H:%M:%S')} AZT", flush=True)

    sent_count = 0
    stats = {"sent": 0, "no_candidate": 0, "duplicate": 0, "no_date": 0, "old_news": 0, "no_monitor_match": 0, "no_telegram_recipient": 0, "site_error": 0, "telegram_error": 0, "unknown": 0}
    max_workers = max(1, min(MAX_WORKERS, total or 1))

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(process_site, index, total, site, patterns_data, monitor_keywords_cache): site for index, site in enumerate(sites, start=1)}
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
    print(f"⏩ Köhnə xəbər: {stats.get('old_news', 0)}", flush=True)
    print(f"🔎 Monitor açar sözünə uyğun olmayan: {stats.get('no_monitor_match', 0)}", flush=True)
    print(f"📭 Telegram alıcısı olmayan: {stats.get('no_telegram_recipient', 0)}", flush=True)
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
