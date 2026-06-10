print("PYTHON STARTED", flush=True)
HEALTH_FILE = "site_health.json"
import json
import os
import re
import time
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
import feedparser
import requests
from bs4 import BeautifulSoup
from lxml import html
from urllib.parse import urljoin, urlparse
from datetime import datetime, timedelta
from dateutil import parser
from email.utils import parsedate_to_datetime
from zoneinfo import ZoneInfo

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
CHAT_ID = os.getenv("CHAT_ID", "").strip()
SUPABASE_URL = os.getenv("SUPABASE_URL", "").strip().rstrip("/")
SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "").strip()

CHECK_INTERVAL_SECONDS = 60
MAX_SEND_PER_RUN = 10
MAX_LINKS_PER_SITE = 10
NEWS_TIME_LIMIT_HOURS = 1
MAX_WORKERS = int(os.getenv("MAX_WORKERS", "10"))
REQUEST_TIMEOUT = int(os.getenv("REQUEST_TIMEOUT", "10"))

CONFIG_FILES = [
    "courier_config_clean.json",
    "discovered_sites.json"
]

PATTERNS_FILE = "patterns.json"
KEYWORDS_FILE = "keywords.json"

BAKU_TZ = ZoneInfo("Asia/Baku")

STRICT_WORDS = {
    "dim", "tkta", "arti", "pisa", "timss", "pirls", "bağça", "magistr", "peşə", "elm", "miq", "diplom"
}

NEWS_CATEGORIES = {
    "sosial", "siyasət", "hadisə", "cəmiyyət", "iqtisadiyyat", "dünya",
    "ölkə", "təhsil", "elm", "mədəniyyət", "idman", "kriminal",
    "region", "bölgə", "maraqlı", "şou", "sağlamlıq", "texnologiya",
}

DB_LOCK = threading.Lock()
TELEGRAM_LOCK = threading.Lock()

def load_health():
    try:
        with open(HEALTH_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def save_health(data):
    try:
        with open(HEALTH_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print("Health save xətası:", e, flush=True)

def update_site_health(domain, status):
    health = load_health()

    if domain not in health:
        health[domain] = {
            "checked": 0,
            "success": 0,
            "no_candidate": 0,
            "error": 0,
            "last_check": None
        }

    health[domain]["checked"] += 1
    health[domain]["last_check"] = datetime.now(BAKU_TZ).isoformat()

    if status in health[domain]:
        health[domain][status] += 1

    save_health(health)

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
        print("SUPABASE_URL və ya SUPABASE_SERVICE_ROLE_KEY yoxdur. Təkrar xəbər bazası işləməyəcək.", flush=True)
        return False
    return True


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
        return False

    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"

    try:
        with TELEGRAM_LOCK:
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
            return False

        if r.status_code == 400 and "migrate_to_chat_id" in r.text:
            print("Telegram qrupu supergroup-a keçib. CHAT_ID-ni migrate_to_chat_id ilə yenilə.", flush=True)

        return r.status_code == 200

    except Exception as e:
        print("Telegram xətası:", e, flush=True)
        return False


def clean_text(text):
    return re.sub(r"\s+", " ", str(text or "")).strip()


def clean_title_for_message(title):
    title = clean_text(title)

    # Başlıq əvvəlinə düşən kateqoriyanı silir:
    # "SOSİAL 09:41 ..." -> "09:41 ..."
    category_pattern = r"^(" + "|".join(re.escape(c) for c in NEWS_CATEGORIES) + r")\s+"
    title = re.sub(category_pattern, "", title, flags=re.IGNORECASE)

    # Başlıq əvvəlinə düşən saatı silir:
    # "09:41 Müəllimlərin..." -> "Müəllimlərin..."
    title = re.sub(r"^\d{1,2}[:.]\d{2}\s+", "", title)

    # Tire/ayırıcı qalıqları silir.
    title = re.sub(r"^[-–—|]+\s*", "", title)
    title = re.sub(r"^\d{1,2}[:.]\d{2}\s*[-–—|]?\s*", "", title)

    # Başlığın sonunda qalan tarix+saatı silir:
    # "... 3 iyn 2026, 11:53"
    title = re.sub(
        r"\s+\d{1,2}\s+[a-zəöğıçşü]+\s+\d{4}\s*,?\s*\d{1,2}[:.]\d{2}$",
        "",
        title,
        flags=re.IGNORECASE
    )

    # Başlığın sonunda qalan rəqəmli tarix+saatı silir:
    # "... 03.06.2026 11:53"
    title = re.sub(
        r"\s+\d{1,2}[./-]\d{1,2}[./-]\d{4}\s+\d{1,2}[:.]\d{2}$",
        "",
        title
    )

    # Başlığın sonunda tək tarix qalıbsa silir:
    # "... 03.06.2026"
    title = re.sub(
        r"\s+\d{1,2}[./-]\d{1,2}[./-]\d{4}$",
        "",
        title
    )

    return clean_text(title)


def clean_matched_keywords(keywords):
    cleaned = []
    seen = set()

    for keyword in keywords or []:
        normalized = normalize_text(keyword)
        if not normalized:
            continue

        # Kateqoriya adları açar söz kimi göstərilməsin.
        if normalized in NEWS_CATEGORIES:
            continue

        if normalized in seen:
            continue

        seen.add(normalized)
        cleaned.append(keyword)

    return cleaned


def normalize_text(text):
    text = str(text or "").lower()
    text = text.replace("i̇", "i")
    return text


def get_domain(url):
    return urlparse(url).netloc.replace("www.", "").lower()


def normalize_link(link):
    link = clean_text(link)
    if not link:
        return ""

    # Query və anchor hissələrini silirik: ?utm=... və #...
    link = link.split("?")[0].split("#")[0]

    # www fərqini eyni sayırıq
    link = link.replace("://www.", "://")

    # Son slash fərqini eyni sayırıq
    link = link.rstrip("/")

    return link.lower()


def normalize_title_key(title):
    title = clean_title_for_message(title)
    title = normalize_text(title)

    # Başlıq sonundakı durğu və artıq boşluqları yumşaldırıq
    title = re.sub(r"[^a-zA-Z0-9əöğüçıƏÖĞÜÇŞşİı\s]", " ", title)
    title = re.sub(r"\s+", " ", title).strip()

    return title


def exists(link, title=None):
    if not supabase_ready():
        return False

    normalized_link = normalize_link(link)
    title_key = normalize_title_key(title) if title else ""

    if not normalized_link:
        return False

    try:
        # Əvvəl link üzrə yoxlayırıq
        with DB_LOCK:
            response = requests.get(
                f"{SUPABASE_URL}/rest/v1/sent_news",
                headers=supabase_headers(),
                params={
                    "select": "link,title",
                    "link": f"eq.{normalized_link}",
                    "limit": "1",
                },
                timeout=REQUEST_TIMEOUT,
            )

        if response.status_code == 200 and response.json():
            print(f"⛔ Təkrar xəbər link üzrə bazada var: {normalized_link}", flush=True)
            return True

        if response.status_code != 200:
            print(f"Supabase exists link xətası: {response.status_code} | {response.text[:200]}", flush=True)
            return False

        # Sonra başlıq üzrə yoxlayırıq. Bu, eyni xəbərin fərqli URL-lərlə gəlməsini azaldır.
        if title_key:
            with DB_LOCK:
                title_response = requests.get(
                    f"{SUPABASE_URL}/rest/v1/sent_news",
                    headers=supabase_headers(),
                    params={
                        "select": "link,title",
                        "title": f"eq.{title_key}",
                        "limit": "1",
                    },
                    timeout=REQUEST_TIMEOUT,
                )

            if title_response.status_code == 200 and title_response.json():
                print(f"⛔ Təkrar xəbər başlıq üzrə bazada var: {title_key[:80]}", flush=True)
                return True

            if title_response.status_code != 200:
                print(f"Supabase exists title xətası: {title_response.status_code} | {title_response.text[:200]}", flush=True)

        return False

    except Exception as e:
        print(f"Supabase exists istisnası: {e}", flush=True)
        return False



def reserve_news(link, title, source):
    """
    Ən vacib hissə budur:
    Xəbəri Telegram-a göndərməzdən ƏVVƏL Supabase-də rezerv edirik.
    Paralel worker-lər eyni xəbəri eyni anda görsə belə, unique link səbəbindən yalnız biri rezerv edə bilir.
    """
    if not supabase_ready():
        # Supabase yoxdursa, bot dayanmasın. Amma bu halda təkrar qoruması zəif olacaq.
        return True

    normalized_link = normalize_link(link)
    title_key = normalize_title_key(title)

    if not normalized_link:
        print("Supabase reserve: link boşdur", flush=True)
        return False

    # Eyni başlıq fərqli URL-lə gəlibsə əvvəl yoxlayırıq.
    if exists(normalized_link, title_key):
        return False

    payload = {
        "link": normalized_link,
        "title": title_key or clean_text(title),
        "source": source,
    }

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

    except Exception as e:
        print(f"Supabase reserve istisnası: {e}", flush=True)
        return False


def release_reserved_news(link):
    """Telegram göndərilməsə, rezervi silirik ki, növbəti dövrdə yenidən yoxlana bilsin."""
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

        if response.status_code in (200, 204):
            print(f"Rezerv silindi: {normalized_link}", flush=True)
            return True

        print(f"Rezerv silinmədi: {response.status_code} | {response.text[:200]}", flush=True)
        return False

    except Exception as e:
        print(f"Rezerv silmə istisnası: {e}", flush=True)
        return False

def get_or_create_monitor_source(site_name, source_domain, page_url):
    if not supabase_ready():
        return None

    base_url = f"{urlparse(page_url).scheme}://{urlparse(page_url).netloc}"

    try:
        response = requests.get(
            f"{SUPABASE_URL}/rest/v1/sources",
            headers=supabase_headers(),
            params={
                "select": "id",
                "base_url": f"eq.{base_url}",
                "limit": "1",
            },
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

        if create_response.status_code in (200, 201):
            data = create_response.json()
            if data:
                return data[0]["id"]

        print(
            f"Monitor source yaradılmadı: {create_response.status_code} | {create_response.text[:200]}",
            flush=True,
        )
        return None

    except Exception as e:
        print(f"Monitor source xətası: {e}", flush=True)
        return None


def save_to_vizual_monitor(site, item, clean_title, published_time):
    if not supabase_ready():
        return None

    link = normalize_link(item.get("link"))
    if not link:
        return None

    source_id = get_or_create_monitor_source(
        site.get("name"),
        item.get("source"),
        site.get("url"),
    )

    if not source_id:
        print("Vizual Monitor: source_id tapılmadı", flush=True)
        return None

    published_at = None
    dt = parse_datetime_to_baku(published_time)
    if dt:
        published_at = dt.isoformat()

    payload = {
        "source_id": source_id,
        "title": clean_title,
        "url": link,
        "published_at": published_at,
        "detected_at": datetime.now(BAKU_TZ).isoformat(),
        "item_hash": link,
        "status": "new",
    }

    try:
        response = requests.post(
            f"{SUPABASE_URL}/rest/v1/monitored_items",
            headers=supabase_headers({
                "Prefer": "resolution=ignore-duplicates,return=representation"
            }),
            json=payload,
            timeout=REQUEST_TIMEOUT,
        )

        if response.status_code in (200, 201):
            data = response.json()
            if data:
                item_id = data[0].get("id")
                print(f"✅ Vizual Monitor-a yazıldı: {clean_title[:80]}", flush=True)
                return item_id

        if response.status_code in (204, 409):
            existing = requests.get(
                f"{SUPABASE_URL}/rest/v1/monitored_items",
                headers=supabase_headers(),
                params={
                    "select": "id",
                    "url": f"eq.{link}",
                    "limit": "1",
                },
                timeout=REQUEST_TIMEOUT,
            )

            if existing.status_code == 200 and existing.json():
                item_id = existing.json()[0].get("id")
                print(f"⛔ Vizual Monitor duplicate, mövcud item istifadə olunur: {link}", flush=True)
                return item_id

            print(f"⛔ Vizual Monitor duplicate amma item_id tapılmadı: {link}", flush=True)
            return None

        print(
            f"Vizual Monitor yazma xətası: {response.status_code} | {response.text[:300]}",
            flush=True,
        )
        return None

    except Exception as e:
        print(f"Vizual Monitor istisnası: {e}", flush=True)
        return None

def match_user_monitors(item_id, title):
    if not supabase_ready() or not item_id:
        return 0

    title_text = normalize_text(title)

    try:
        response = requests.get(
            f"{SUPABASE_URL}/rest/v1/monitor_keywords",
            headers=supabase_headers(),
            params={
                "select": "id,keyword,match_type,monitor_id,user_monitors(status)",
            },
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

            keyword = normalize_text(row.get("keyword", ""))
            if not keyword:
                continue

            if keyword not in title_text:
                continue

            payload = {
                "monitor_id": row.get("monitor_id"),
                "item_id": item_id,
                "matched_keyword": row.get("keyword"),
            }

            match_response = requests.post(
                f"{SUPABASE_URL}/rest/v1/monitor_matches",
                headers=supabase_headers({
                    "Prefer": "resolution=ignore-duplicates,return=minimal"
                }),
                json=payload,
                timeout=REQUEST_TIMEOUT,
            )

        if match_response.status_code in (200, 201, 204):
    matched_count += 1
    print(
        f"✅ Monitor uyğunluğu yazıldı: {row.get('keyword')} | item={item_id}",
        flush=True,
    )

    match_id = None

    try:
        match_lookup = requests.get(
            f"{SUPABASE_URL}/rest/v1/monitor_matches",
            headers=supabase_headers(),
            params={
                "select": "id",
                "monitor_id": f"eq.{row.get('monitor_id')}",
                "item_id": f"eq.{item_id}",
                "limit": "1",
            },
            timeout=REQUEST_TIMEOUT,
        )

           if match_lookup.status_code == 200 and match_lookup.json():
            match_id = match_lookup.json()[0].get("id")
         except Exception as e:
                print(f"Monitor match_id oxuma xətası: {e}", flush=True)

           if match_id:
        try:
            alert_response = requests.post(
                f"{SUPABASE_URL}/rest/v1/monitor_alerts",
                headers=supabase_headers({
                    "Prefer": "resolution=ignore-duplicates,return=minimal"
                }),
                json={
                    "match_id": match_id,
                    "channel": "web",
                    "recipient": "admin",
                    "status": "new",
                    "sent_at": datetime.now(BAKU_TZ).isoformat(),
                },
                timeout=REQUEST_TIMEOUT,
            )

            if alert_response.status_code in (200, 201, 204):
                print(f"🔔 Bildiriş yaradıldı: match={match_id}", flush=True)
            else:
                print(
                    f"Bildiriş yazma xətası: {alert_response.status_code} | {alert_response.text[:200]}",
                    flush=True,
                )
        except Exception as e:
            print(f"Bildiriş istisnası: {e}", flush=True)
            elif match_response.status_code == 409:
                print(
                    f"⛔ Monitor uyğunluğu duplicate: {row.get('keyword')} | item={item_id}",
                    flush=True,
                )
            else:
                print(
                    f"Monitor match yazma xətası: {match_response.status_code} | {match_response.text[:200]}",
                    flush=True,
                )

        return matched_count

    except Exception as e:
        print(f"Monitor match istisnası: {e}", flush=True)
        return 0

def save(link, title, source):
    # Köhnə ad qalır ki, başqa yerdə çağırılsa işləsin.
    return reserve_news(link, title, source)



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
                    "rss_url": clean_text(site.get("rss_url", "")),
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
        "news", "xeber", "xeberler", "xəbərlər", "media", "media/news", "category",
        "kateqoriya", "archive", "arxiv", "allnews", "all-news", "newsarchive", "latest",
        "lastnews", "son-xeberler", "az/news", "az/xeber", "az/xeberler", "az/xəbərlər",
        "az/metbuat/xeberler", "az/page/media/news", "az/news-and-updates", "p/news",
        "tehsil", "elm", "elm-ve-tehsil"
    ]

    if path in section_paths:
        return True

    bad_section_words = [
        "news", "xeber", "xeberler", "xəbərlər", "category", "kateqoriya", "archive",
        "arxiv", "latest", "lastnews", "allnews", "all-news", "son-xeberler", "media"
    ]

    parts = [p for p in path.split("/") if p]

    if len(parts) <= 1 and any(word in path for word in bad_section_words):
        return True

    if len(parts) <= 2 and any(path.endswith(word) for word in [
        "news", "xeber", "xeberler", "xəbərlər", "media/news", "allnews", "all-news",
        "latest", "lastnews", "son-xeberler", "category", "kateqoriya", "archive", "arxiv"
    ]):
        return True

    return False


def is_bad_link(title, link):
    title_lower = title.lower()
    link_lower = link.lower()

    bad_words = [
        "ana səhifə", "haqqımızda", "əlaqə", "reklam", "giriş", "qeydiyyat",
        "axtarış", "abunə", "facebook", "instagram", "youtube", "telegram",
        "twitter", "linkedin", "rss", "bütün xəbərlər", "daha çox", "arxiv",
        "kateqoriya", "bütün bölmələr", "menu", "menyu"
    ]

    bad_domains = [
        "facebook.com", "instagram.com", "youtube.com", "t.me", "twitter.com", "x.com", "linkedin.com"
    ]

    bad_extensions = [
        ".jpg", ".jpeg", ".png", ".gif", ".webp", ".pdf", ".doc", ".docx",
        ".xls", ".xlsx", ".zip", ".rar", ".mp4", ".mp3"
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


AZ_MONTHS = {
    "yanvar": 1, "fevral": 2, "mart": 3, "aprel": 4, "may": 5, "iyun": 6,
    "iyul": 7, "avqust": 8, "sentyabr": 9, "oktyabr": 10, "noyabr": 11, "dekabr": 12,
    "yan": 1, "fev": 2, "mar": 3, "apr": 4, "iyn": 6, "iyl": 7, "avq": 8,
    "sen": 9, "okt": 10, "noy": 11, "dek": 12,
}


def parse_az_datetime(value):
    text = clean_text(str(value or "")).lower()
    if not text:
        return None

    text = text.replace("—", "-").replace("–", "-")

    patterns = [
        r"(\d{1,2})\s+([a-zəöğıçşü]+)\s+(\d{4})\s*[,\-]?\s*(\d{1,2})[:.](\d{2})",
        r"(\d{1,2})[.\-/](\d{1,2})[.\-/](\d{4})\s*[,\-]?\s*(\d{1,2})[:.](\d{2})",
        r"(\d{1,2})[:.](\d{2})\s*[,\-]?\s*(\d{1,2})\s+([a-zəöğıçşü]+)\s*,?\s*(\d{4})",
        r"(\d{1,2})[:.](\d{2})\s*[,\-]?\s*(\d{1,2})[.\-/](\d{1,2})[.\-/](\d{4})",
    ]

    for idx, pattern in enumerate(patterns):
        m = re.search(pattern, text, re.IGNORECASE)
        if not m:
            continue

        try:
            groups = m.groups()

            if idx == 0:
                day, month_name, year, hour, minute = groups
                month = AZ_MONTHS.get(month_name)
            elif idx == 1:
                day, month, year, hour, minute = groups
                month = int(month)
            elif idx == 2:
                hour, minute, day, month_name, year = groups
                month = AZ_MONTHS.get(month_name)
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

    # Başlığın əvvəlində yalnız saat varsa: "12:02 Günəş aktivliyi..."
    time_only = re.search(r"^\s*(\d{1,2})[:.](\d{2})(?:\s|$)", text)
    if time_only:
        try:
            hour = int(time_only.group(1))
            minute = int(time_only.group(2))
            if 0 <= hour <= 23 and 0 <= minute <= 59:
                today = datetime.now(BAKU_TZ).date()
                return datetime(today.year, today.month, today.day, hour, minute, tzinfo=BAKU_TZ)
        except Exception:
            pass

    date_only_patterns = [
        r"(\d{1,2})\s+([a-zəöğıçşü]+)\s+(\d{4})",
        r"(\d{1,2})[.\-/](\d{1,2})[.\-/](\d{4})",
    ]

    for idx, pattern in enumerate(date_only_patterns):
        m = re.search(pattern, text, re.IGNORECASE)
        if not m:
            continue

        try:
            groups = m.groups()

            if idx == 0:
                day, month_name, year = groups
                month = AZ_MONTHS.get(month_name)
            else:
                day, month, year = groups
                month = int(month)

            if not month:
                continue

            return datetime(int(year), int(month), int(day), 0, 0, tzinfo=BAKU_TZ)
        except Exception:
            continue

    return None


def parse_datetime_to_baku(published_time):
    text = clean_text(str(published_time or ""))

    if not text or "tarix tapılmadı" in text.lower():
        return None

    az_dt = parse_az_datetime(text)
    if az_dt:
        return az_dt

    try:
        try:
            dt = parsedate_to_datetime(text)
        except Exception:
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

    if diff <= timedelta(hours=NEWS_TIME_LIMIT_HOURS):
        hours = diff.total_seconds() / 3600
        print(f"Tarix uyğundur: {published_time} | fərq: {hours:.2f} saat", flush=True)
        return True

    hours = diff.total_seconds() / 3600
    print(f"Köhnə xəbər keçildi: {published_time} | fərq: {hours:.2f} saat", flush=True)
    return False


def choose_publish_time(title, article_time):
    title_dt = parse_datetime_to_baku(title)
    article_dt = parse_datetime_to_baku(article_time)

    # Başlıqda tarix/saat varsa, onu əsas götürürük.
    # Çünki səhifədəki date/time çox vaxt yenilənmə və ya cari saat olur.
    if title_dt:
        return title_dt.strftime("%d.%m.%Y %H:%M")

    if article_dt:
        return article_dt.strftime("%d.%m.%Y %H:%M")

    return None


def extract_publish_time_from_article(article_url):
    headers = {
        "User-Agent": "Mozilla/5.0",
        "Accept-Language": "az-AZ,az;q=0.9,en-US;q=0.8"
    }

    try:
        r = requests.get(article_url, headers=headers, timeout=REQUEST_TIMEOUT)
        r.encoding = r.apparent_encoding
        tree = html.fromstring(r.text)

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
        "/news/", "/xeber/", "/xeberler/", "/xəbərlər/", "/az/news/", "/az/xeber/",
        "/az/xeberler/", "/az/xəbərlər/", "/post/", "/article/", "/read/", "/item/",
        "/son-xeber/", "/sosial/", "/resmi-xeber/", "/hadise/", "/politic/",
        "/world/", "/economy/", "/education/", "/elm/", "/tehsil/", "/2024/", "/2025/", "/2026/"
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

    # Açar söz yoxlamasında başlıq əvvəlinə düşən kateqoriya və saat nəzərə alınmır.
    # Məsələn: "SOSİAL 09:41 Müəllimlərin..." -> "Müəllimlərin..."
    title_for_keyword = clean_title_for_message(title)
    matched, matched_keywords = keyword_match(title_for_keyword, keywords)
    matched_keywords = clean_matched_keywords(matched_keywords)

    if not matched_keywords:
        return

    results.append({
        "title": title,
        "clean_title": title_for_keyword,
        "link": link,
        "source": get_domain(page_url),
        "matched_keywords": matched_keywords
    })



def discover_rss_links(page_url, page_html):
    rss_links = []

    try:
        soup = BeautifulSoup(page_html, "html.parser")

        for tag in soup.find_all("link", href=True):
            tag_type = (tag.get("type") or "").lower()
            tag_title = (tag.get("title") or "").lower()
            href = tag.get("href")

            if not href:
                continue

            if "rss" in tag_type or "atom" in tag_type or "rss" in tag_title or "feed" in tag_title:
                rss_links.append(urljoin(page_url, href))

        root = f"{urlparse(page_url).scheme}://{urlparse(page_url).netloc}"

        common_paths = [
            "/rss",
            "/rss.xml",
            "/feed",
            "/feed.xml",
            "/atom.xml",
            "/az/rss",
            "/az/rss.xml",
            "/az/feed",
            "/az/feed.xml",
            "/xeberler/rss",
            "/news/rss",
        ]

        for path in common_paths:
            rss_links.append(urljoin(root, path))

    except Exception as e:
        print(f"RSS link axtarışı xətası: {page_url} | {e}", flush=True)

    return list(dict.fromkeys([x for x in rss_links if x and x.startswith("http")]))[:8]


def extract_links_from_rss(site, rss_urls):
    results = []
    keywords = site.get("keywords", [])
    page_url = site["url"]

    for rss_url in rss_urls:
        if not rss_url:
            continue

        try:
            response = requests.get(
                rss_url,
                headers={
                    "User-Agent": "Mozilla/5.0",
                    "Accept-Language": "az-AZ,az;q=0.9,en-US;q=0.8",
                    "Referer": "https://www.google.com/",
                },
                timeout=REQUEST_TIMEOUT,
                allow_redirects=True,
            )

            if response.status_code != 200:
                continue

            feed = feedparser.parse(response.text)

            if not feed.entries:
                continue

            print(
                f"RSS tapıldı: {rss_url} | xəbər sayı: {len(feed.entries)}",
                flush=True
            )

            before_count = len(results)

            for entry in feed.entries[:MAX_LINKS_PER_SITE * 3]:
                title = clean_text(entry.get("title", ""))
                link = entry.get("link", "")

                add_item(results, page_url, title, link, keywords)

                if results:
                    last = results[-1]
                    if last.get("link") == link or last.get("title") == title:
                        last["rss_published"] = (
                            entry.get("published")
                            or entry.get("updated")
                            or entry.get("created")
                            or ""
                        )

                if len(results) >= MAX_LINKS_PER_SITE:
                    break

            added = len(results) - before_count
            if added > 0:
                print(f"RSS uyğun namizəd verdi: {site.get('name')} | {added}", flush=True)
                break

        except Exception as e:
            print(f"RSS oxuma xətası: {rss_url} | {e}", flush=True)
            continue

    return unique_items(results)[:MAX_LINKS_PER_SITE]

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
    rss_url = clean_text(site.get("rss_url", ""))
    selector = site.get("selector")
    xpaths = site.get("xpaths", [])
    keywords = site.get("keywords", [])

    headers = {
        "User-Agent": "Mozilla/5.0",
        "Accept-Language": "az-AZ,az;q=0.9,en-US;q=0.8",
        "Referer": "https://www.google.com/"
    }

    # 1) Əgər config-də rss_url varsa, əvvəl RSS yoxlanılır.
    if rss_url:
        print(f"RSS-first yoxlanır: {rss_url}", flush=True)
        items = extract_links_from_rss(site, [rss_url])
        if items:
            return unique_items(items)

    try:
        print(f"Sayt açılır: {page_url}", flush=True)

        r = requests.get(page_url, headers=headers, timeout=REQUEST_TIMEOUT)
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

    # 2) Config-də rss_url yoxdursa, səhifənin içindən RSS tapmağa çalışırıq.
    if not rss_url:
        discovered_rss = discover_rss_links(page_url, page_html)
        if discovered_rss:
            items = extract_links_from_rss(site, discovered_rss)
            if items:
                return unique_items(items)

    # 3) RSS nəticə vermirsə, əvvəlki mexanizmlər eyni qaydada işləyir.
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


def process_site(index, total, site, patterns_data):
    started = time.time()
    result = {
        "sent": 0,
        "site": site.get("name"),
        "url": site.get("url"),
        "candidates": 0,
        "reason": "unknown",
    }

    print(f"[{index}/{total}] Yoxlanır: {site['name']} | {site['url']}", flush=True)

    try:
        items = fetch_site(site, patterns_data)
    except Exception as e:
        print(f"❌ [{index}/{total}] {site['name']} | sayt emalı xətası: {e}", flush=True)
        result["reason"] = "site_error"
        return result

    result["candidates"] = len(items)
    print(f"[{index}/{total}] {site['name']} | uyğun link sayı: {len(items)}", flush=True)

    if not items:
        result["reason"] = "no_candidate"
        elapsed = time.time() - started
        print(
            f"📊 [{index}/{total}] {site['name']} | namizəd=0 | göndərildi=0 | nəticə=uyğun xəbər yoxdur | vaxt={elapsed:.1f}s",
            flush=True
        )
        return result

    limit = site.get("limit", MAX_LINKS_PER_SITE)

    for item in items[:limit]:
        title = item["title"]
        link = item["link"]
        source = item["source"]
        matched_keywords = item.get("matched_keywords", [])

        if exists(link, title):
            print(f"[{index}/{total}] Təkrar xəbər keçildi: {link}", flush=True)
            result["reason"] = "duplicate"
            continue

        title_time = parse_datetime_to_baku(title)
        rss_time = item.get("rss_published")
        article_time = rss_time or extract_publish_time_from_article(link)
        published_time = choose_publish_time(title, article_time)

        print(
            f"[{index}/{total}] Xəbər: {title[:80]} | title_tarix: {title_time} | rss_tarix: {rss_time} | article_tarix: {article_time} | seçilən tarix: {published_time} | Link: {link}",
            flush=True
        )

        if not published_time:
            print(f"[{index}/{total}] Tarix tapılmadı, xəbər keçildi: {title[:70]}", flush=True)
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

        # ƏVVƏL Supabase-də rezerv edirik, sonra Telegram-a göndəririk.
        # Bu, paralel worker-lərdə eyni xəbərin 3-4 dəfə getməsinin qarşısını alır.
        if not reserve_news(link, clean_title, source):
            print(f"[{index}/{total}] Təkrar/rezerv olunmuş xəbər keçildi: {link}", flush=True)
            result["reason"] = "duplicate"
            continue

        monitor_item_id = save_to_vizual_monitor(site, item, clean_title, published_time)

        if monitor_item_id:
            match_user_monitors(monitor_item_id, clean_title)

        if send_telegram(message):
            print(
                f"✅ [{index}/{total}] Göndərildi: {source} | {clean_title[:70]} | Açar sözlər: {matched_keywords_text}",
                flush=True
            )

            result["sent"] = 1
            result["reason"] = "sent"
            elapsed = time.time() - started
            print(
                f"📊 [{index}/{total}] {site['name']} | namizəd={len(items)} | göndərildi=1 | nəticə=telegram | vaxt={elapsed:.1f}s",
                flush=True
            )
            time.sleep(1)
            return result

        release_reserved_news(link)
        print(f"[{index}/{total}] Telegram göndərilmədi, rezerv geri silindi.", flush=True)
        result["reason"] = "telegram_error"

    elapsed = time.time() - started
    print(
        f"📊 [{index}/{total}] {site['name']} | namizəd={len(items)} | göndərildi=0 | nəticə={result['reason']} | vaxt={elapsed:.1f}s",
        flush=True
    )
    return result


def check_sites():
    started = time.time()
    sites = load_sites()
    patterns_data = load_patterns()
    total = len(sites)

    print(f"Yüklənən sayt sayı: {total}", flush=True)
    print(
        f"Monitorinq başladı | worker={MAX_WORKERS} | son {NEWS_TIME_LIMIT_HOURS} saat | {datetime.now(BAKU_TZ).strftime('%d.%m.%Y %H:%M:%S')} AZT",
        flush=True
    )

    sent_count = 0
    stats = {
        "sent": 0,
        "no_candidate": 0,
        "duplicate": 0,
        "no_date": 0,
        "not_today": 0,
        "old_news": 0,
        "site_error": 0,
        "telegram_error": 0,
        "unknown": 0,
    }

    max_workers = max(1, min(MAX_WORKERS, total or 1))

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(process_site, index, total, site, patterns_data): site
            for index, site in enumerate(sites, start=1)
        }

        for future in as_completed(futures):
            try:
                result = future.result() or {}
            except Exception as e:
                print(f"Worker xətası: {e}", flush=True)
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


print("🚀 Sayt monitorinq botu işə düşdü.", flush=True)
if supabase_ready():
    print("✅ Supabase bağlantı məlumatları yükləndi", flush=True)
send_telegram("✅ Bot işə düşdü və saytları yoxlamağa başladı.")

while True:
    print("🔎 Yeni xəbərlər yoxlanılır...", flush=True)
    check_sites()
    time.sleep(CHECK_INTERVAL_SECONDS)
