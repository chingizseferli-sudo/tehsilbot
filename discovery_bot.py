import argparse
import json
import os
import re
import time
from collections import Counter
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from urllib.parse import quote_plus, urljoin, urlparse

import feedparser
import requests
from bs4 import BeautifulSoup

from domain_policy import is_excluded_domain as policy_is_excluded_domain

DISCOVERED_FILE = "discovered_sites.json"
CONFIG_FILE = "courier_config_clean.json"
REVIEW_FILE = "review_sites.json"
REJECTED_FILE = "rejected_sites.json"
PATTERNS_FILE = "patterns.json"

REQUEST_TIMEOUT = 12
DISCOVERY_VERSION = "5.3-general-news-discovery"
DISCOVERY_ACTIVITY_LOOKBACK_HOURS = int(os.getenv("DISCOVERY_ACTIVITY_LOOKBACK_HOURS", "24"))
DISCOVERY_MIN_NEWS_PER_PERIOD = int(os.getenv("DISCOVERY_MIN_NEWS_PER_PERIOD", "15"))
DISCOVERY_REQUIRE_ACTIVITY = os.getenv("DISCOVERY_REQUIRE_ACTIVITY", "true").lower() != "false"

SUPABASE_URL = os.getenv("SUPABASE_URL", "").strip().rstrip("/")
SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "").strip()
BAKU_TZ = ZoneInfo("Asia/Baku")

# Əvvəlki discovery versiyasında .gov.az bloklanırdı. Vizual.az üçün dövlət/qurum saytlarını da
# izləmək lazım ola bilər. İstəsən Railway-də DISCOVERY_BLOCK_GOV=true qoyub yenə bloklaya bilərsən.
DISCOVERY_BLOCK_GOV = os.getenv("DISCOVERY_BLOCK_GOV", "true").lower() != "false"
DISCOVERY_WRITE_JSON = os.getenv("DISCOVERY_WRITE_JSON", "false").lower() == "true"
DISCOVERY_SYNC_SUPABASE = os.getenv("DISCOVERY_SYNC_SUPABASE", "true").lower() != "false"
DISCOVERY_MAX_QUERIES = int(os.getenv("DISCOVERY_MAX_QUERIES", "0") or "0")
DISCOVERY_MAX_ENTRIES_PER_QUERY = int(os.getenv("DISCOVERY_MAX_ENTRIES_PER_QUERY", "0") or "0")
DISCOVERY_BUILD_PATTERNS = os.getenv("DISCOVERY_BUILD_PATTERNS", "true").lower() != "false"
DISCOVERY_INCLUDE_EDU_SPECIALIZED = os.getenv("DISCOVERY_INCLUDE_EDU_SPECIALIZED", "false").lower() == "true"
DISCOVERY_SUBDOMAIN_ALLOWLIST = {
    item.strip().lower().lstrip(".")
    for item in os.getenv("DISCOVERY_SUBDOMAIN_ALLOWLIST", "").split(",")
    if item.strip()
}


HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; TehsilBotDiscovery/4.0)",
    "Accept-Language": "az-AZ,az;q=0.9,tr-TR;q=0.8,en-US;q=0.7,en;q=0.6",
}

DEFAULT_KEYWORDS = [
    "xəbər", "son xəbər", "gündəm", "siyasət", "cəmiyyət", "iqtisadiyyat",
    "dünya", "ölkə", "region", "hadisə", "idman", "mədəniyyət",
    "media", "mətbuat", "reportaj", "açıqlama", "rəsmi", "agentlik",
]

# Məqsəd: konkret mövzu yox, gündəlik aktiv Azərbaycan xəbər mənbələri tapmaq.
GENERAL_NEWS_QUERIES_FAST = [
    "Azərbaycan xəbər saytı",
    "Azərbaycan xəbər portalı",
    "Azərbaycan media portalları",
    "son xəbərlər Azərbaycan",
    "gündəlik xəbərlər Azərbaycan",
    "ölkə xəbərləri Azərbaycan",
    "cəmiyyət xəbərləri Azərbaycan",
    "siyasət xəbərləri Azərbaycan",
    "iqtisadiyyat xəbərləri Azərbaycan",
    "region xəbərləri Azərbaycan",
    "dünya xəbərləri Azərbaycan",
    "idman xəbərləri Azərbaycan",
    "mədəniyyət xəbərləri Azərbaycan",
    "site:.az xəbər",
    "site:.az xəbərlər",
    "site:.az xeber",
    "site:.az xeberler",
    "site:.az son xeberler",
    "site:.az gündəm",
    "site:.az media",
    "site:.az news",
    "site:.az RSS xəbər",
]

GENERAL_NEWS_QUERIES_DEEP = GENERAL_NEWS_QUERIES_FAST + [
    "site:.az xəbər lenti",
    "site:.az son xəbərlər",
    "site:.az bütün xəbərlər",
    "site:.az ölkə xəbərləri",
    "site:.az sosial xəbərlər",
    "site:.az cəmiyyət xəbərləri",
    "site:.az region xəbərləri",
    "site:.az dünya xəbərləri",
    "site:.az iqtisadiyyat xəbərləri",
    "site:.az siyasət xəbərləri",
    "site:.az idman xəbərləri",
    "site:.az kriminal xəbərlər",
    "site:.az mədəniyyət xəbərləri",
    "site:.az press news",
    "site:.az newsroom",
    "site:.az latest news",
]

EDU_CHECK_QUERIES_FAST = [
    "təhsil xəbərləri Azərbaycan",
    "universitet xəbərləri Azərbaycan",
]

EDU_CHECK_QUERIES_DEEP = EDU_CHECK_QUERIES_FAST + [
    "elm xəbərləri Azərbaycan",
]
NEWS_SECTION_WORDS = [
    "xəbərlər", "xeberler", "xəbər", "xeber", "xəbər lenti", "xeber lenti",
    "son xəbərlər", "son xeberler", "bütün xəbərlər", "butun xeberler",
    "yeniliklər", "yenilikler", "yenilik", "elanlar", "elan", "duyurular",
    "duyuru", "bildirişlər", "bildirisler", "bildiriş", "bildiris",
    "media", "mətbuat", "metbuat", "press", "press center", "press-centre",
    "press room", "pressroom", "newsroom", "media center", "media centre",
    "news", "latest", "latest news", "all news", "updates", "announcements",
    "announcement", "events", "event", "notices", "notice", "blog", "posts",
    "post", "articles", "article", "publications", "publication",
    "research", "researches", "projects", "project", "conference",
    "conferences", "seminars", "seminar", "science", "education",
]


AZ_NEWS_LANGUAGE_WORDS = [
    "azərbaycan", "azerbaycan", "xəbər", "xeber", "gündəm", "gundem",
    "cəmiyyət", "cemiyyet", "siyasət", "siyaset", "iqtisadiyyat",
    "ölkə", "olke", "dünya", "dunya", "təhsil", "tehsil",
    "mədəniyyət", "medeniyyet", "hadisə", "hadise", "bugün", "bugun",
]

COMMON_NEWS_PATHS_FAST = [
    "/news",
    "/xeber",
    "/xeberler",
    "/xəbərlər",
    "/latest",
    "/latest-news",
    "/son-xeber",
    "/son-xeberler",
    "/category/son-xeber",
    "/news-of-day",
    "/az/news",
    "/az/xeber",
    "/az/xeberler",
    "/az/xəbərlər",
    "/media",
    "/press",
    "/gundem",
    "/category/gundem",
]

COMMON_NEWS_PATHS_DEEP = [
    "/news", "/news/", "/xeber", "/xeber/", "/xeberler", "/xeberler/",
    "/xəbərlər", "/xəbərlər/", "/az/news", "/az/news/", "/az/xeber",
    "/az/xeber/", "/az/xeberler", "/az/xeberler/", "/az/xəbərlər",
    "/az/xəbərlər/", "/media", "/media/news", "/media/news/", "/az/media",
    "/az/media/news", "/az/media/news/", "/all-news", "/allnews", "/latest",
    "/lastnews", "/son-xeberler", "/son-xeberler/", "/newsarchive",
    "/az/newsarchive", "/p/news", "/tehsil", "/elm", "/elm-ve-tehsil",
    "/press-relizler", "/press-release", "/announcements", "/announcement",
    "/elanlar", "/updates", "/events", "/event", "/blog", "/posts",
    "/articles", "/article", "/publications", "/publication", "/research",
    "/newsroom", "/press", "/press-center", "/press-centre", "/duyurular",
    "/duyuru", "/notices", "/notice", "/yenilikler", "/yeniliklər",
    "/az/elanlar", "/az/duyurular", "/az/events", "/az/announcements",
    "/az/updates", "/az/blog", "/az/publications", "/az/research",
    "/news",
    "/xeber",
    "/xeberler",
    "/xəbərlər",
    "/latest",
    "/latest-news",
    "/son-xeber",
    "/son-xeberler",
    "/category/son-xeber",
    "/news-of-day",
    "/az/news",
    "/az/xeber",
    "/az/xeberler",
    "/az/xəbərlər",
    "/media",
    "/press",
    "/gundem",
    "/category/gundem",
]

RSS_PATHS = [
    "/rss", "/rss.xml", "/feed", "/feed.xml", "/atom.xml",
    "/az/rss", "/az/rss.xml", "/az/feed", "/az/feed.xml",
    "/rss/index.xml", "/feed/index.xml",
    "/?feed=rss2",
    "/index.php?format=feed&type=rss",
    "/az/index.php?format=feed&type=rss",
    "/news?format=feed&type=rss",
    "/xeber?format=feed&type=rss",
    "/xeberler?format=feed&type=rss",
    "/xəbərlər?format=feed&type=rss",
    "/rss/news",
    "/rss/news.xml",
    "/news/rss",
    "/feed/rss",
    "/feeds",
    "/feeds/posts/default",
]

BAD_DOMAINS = [
    "facebook.com", "instagram.com", "youtube.com", "youtu.be", "t.me",
    "twitter.com", "x.com", "linkedin.com", "whatsapp.com", "google.com",
    "news.google.com", "maps.google.com",
]

# gov istənmir. Tam qadağa qoyuruq ki, discovery gov mənbələrini toplamasın.
BLOCKED_DOMAIN_PARTS = [".gov.az"] if DISCOVERY_BLOCK_GOV else []

BAD_URL_WORDS = [
    "facebook", "instagram", "youtube", "telegram", "login", "register",
    "search", "contact", "about", "elaqe", "haqqimizda", "reklam",
    "tag", "author", "wp-content", "uploads", "cdn-cgi", "pdf", "docx",
    "privacy", "terms", "sitemap", "javascript:", "mailto:",
    "localhost", "127.0.0.1", "0.0.0.0",
]

COMMERCIAL_SITE_WORDS = [
    "market", "shop", "store", "shopping", "mall", "satis", "satış", "almaq",
    "qiymet", "qiymət", "discount", "endirim", "kampaniya", "kataloq",
    "catalog", "product", "products", "mehsul", "məhsul", "xidmet", "xidmət",
    "service", "services", "company", "şirkət", "sirket", "agency", "agentlik",
    "restaurant", "hotel", "booking", "estate", "avto", "auto", "cars",
]

ARTICLE_HINTS = [
    "/news/", "/xeber/", "/xeberler/", "/xəbərlər/", "/post/", "/article/",
    "/read/", "/item/", "/son-xeber/", "/sosial/", "/cemiyyet/", "/cəmiyyət/",
    "/hadise/", "/dunya/", "/ölke/", "/olke/", "/iqtisadiyyat/",
    "/education/", "/tehsil/", "/elm/", "/2024/", "/2025/", "/2026/",
]

GOOD_PATTERN_HINTS = [
    "news", "xeber", "xeberler", "xəbər", "xəbərlər", "article", "post",
    "read", "item", "son-xeber", "latest", "media", "tehsil", "elm",
]

BAD_PATTERNS = [
    "/tag/", "/category/", "/kateqoriya/", "/author/", "/page/", "/login/",
    "/register/", "/search/", "/video/", "/photo/", "/contact/", "/about/",
    "/elaqe/", "/haqqimizda/", "/reklam/", "/wp-content/", "/uploads/", "/cdn-cgi/",
]


def get_mode_settings(mode: str) -> dict:
    if mode == "deep":
        settings = {
            "max_queries": 140,
            "max_entries_per_query": 80,
            "max_sections_per_source": 5,
            "sleep": 0.20,
            "paths": COMMON_NEWS_PATHS_DEEP,
            "build_patterns": True,
        }
    else:
        settings = {
            "max_queries": 55,
            "max_entries_per_query": 40,
            "max_sections_per_source": 3,
            "sleep": 0.10,
            "paths": COMMON_NEWS_PATHS_FAST,
            "build_patterns": False,
        }

    if DISCOVERY_MAX_QUERIES > 0:
        settings["max_queries"] = DISCOVERY_MAX_QUERIES
    if DISCOVERY_MAX_ENTRIES_PER_QUERY > 0:
        settings["max_entries_per_query"] = DISCOVERY_MAX_ENTRIES_PER_QUERY
    return settings


def supabase_ready() -> bool:
    if not SUPABASE_URL or not SUPABASE_SERVICE_ROLE_KEY:
        print("Supabase env yoxdur: SUPABASE_URL və ya SUPABASE_SERVICE_ROLE_KEY", flush=True)
        return False
    return True


def supabase_headers(extra=None) -> dict:
    headers = {
        "apikey": SUPABASE_SERVICE_ROLE_KEY,
        "Authorization": f"Bearer {SUPABASE_SERVICE_ROLE_KEY}",
        "Content-Type": "application/json",
    }
    if extra:
        headers.update(extra)
    return headers


def source_monitor_method(site: dict) -> str:
    if site.get("rss_url"):
        return "rss"
    if site.get("selector"):
        return "selector"
    if site.get("xpaths"):
        return "xpath"
    return "html"


def source_trust_level(score: int) -> str:
    if score >= 80:
        return "high"
    if score >= 50:
        return "medium"
    return "low"


def discovery_status_for_site(site: dict) -> str:
    status = site.get("status")
    if status == "approved":
        return "accepted"
    if status == "review":
        return "manual_needed"
    return "rejected"


def is_hard_rejected(site: dict) -> bool:
    reason = str(site.get("reason") or "").lower()
    source_type = str(site.get("source_type") or "").lower()
    hard_markers = (
        "commercial_site_not_news",
        "insufficient_news_activity",
        "subdomain_rejected",
        "bad_url_or_gov_blocked",
        "rejected_commercial",
        "rejected_inactive_news",
    )
    return any(marker in reason or marker in source_type for marker in hard_markers)


def build_source_payload(site: dict) -> dict:
    url = site.get("url", "")
    score = int(site.get("score", 0) or 0)
    method = source_monitor_method(site)
    analysis = site.get("analysis", {}) if isinstance(site.get("analysis"), dict) else {}

    return {
        "name": site.get("name") or clean_domain(url),
        "base_url": base_url(url),
        "latest_url": url,
        "rss_url": site.get("rss_url"),
        "source_type": "news_site",
        "status": "inactive",
        "trust_level": source_trust_level(score),
        "monitor_method": method,
        "selector": site.get("selector"),
        "article_pattern": ",".join(site.get("xpaths", [])[:3]) if site.get("xpaths") else None,
        "discovery_status": discovery_status_for_site(site),
        "discovery_score": score,
        "last_discovered_at": datetime.now(BAKU_TZ).isoformat(),
        "notes": "; ".join(analysis.get("reasons", []))[:1000] if analysis else site.get("reason"),
    }


def save_discovery_log(domain: str, url: str, status: str, reason: str = "", method: str = "", score: int = 0, sample_links=None):
    if not supabase_ready():
        return False

    payload = {
        "domain": domain,
        "url": url,
        "status": status,
        "reason": reason,
        "method": method,
        "score": score,
        "sample_links": sample_links or [],
    }

    try:
        response = requests.post(
            f"{SUPABASE_URL}/rest/v1/discovery_logs",
            headers=supabase_headers({"Prefer": "return=minimal"}),
            json=payload,
            timeout=REQUEST_TIMEOUT,
        )
        if response.status_code not in (200, 201, 204):
            print(f"Discovery log yazılmadı: {response.status_code} | {response.text[:200]}", flush=True)
            return False
        return True
    except Exception as e:
        print(f"Discovery log istisnası: {e}", flush=True)
        return False


def save_rejected_source(site: dict):
    if not supabase_ready():
        return False

    url = site.get("url", "")
    domain = clean_domain(url)
    if not domain:
        return False

    payload = {
        "domain": domain,
        "url": url,
        "reason": site.get("reason") or "; ".join((site.get("analysis") or {}).get("reasons", []))[:1000],
        "checked_at": datetime.now(BAKU_TZ).isoformat(),
    }

    try:
        response = requests.post(
            f"{SUPABASE_URL}/rest/v1/rejected_sources",
            headers=supabase_headers({"Prefer": "resolution=merge-duplicates,return=minimal"}),
            params={"on_conflict": "domain"},
            json=payload,
            timeout=REQUEST_TIMEOUT,
        )
        if response.status_code not in (200, 201, 204):
            print(f"Rejected source yazılmadı: {response.status_code} | {response.text[:200]}", flush=True)
            return False
        return True
    except Exception as e:
        print(f"Rejected source istisnası: {e}", flush=True)
        return False


def upsert_source_to_supabase(site: dict):
    """Sources cədvəlinə təhlükəsiz yazır.

    Köhnə variant on_conflict=base_url istifadə edirdi. Əgər Supabase-də base_url üçün
    unique constraint yoxdursa, bu xəta verə bilər. Ona görə əvvəl base_url üzrə axtarırıq:
    varsa PATCH, yoxdursa POST edirik.
    """
    if not supabase_ready():
        return False

    payload = build_source_payload(site)
    base = payload.get("base_url")
    if not base:
        return False

    try:
        lookup = requests.get(
            f"{SUPABASE_URL}/rest/v1/sources",
            headers=supabase_headers(),
            params={
                "select": "id",
                "base_url": f"eq.{base}",
                "limit": "1",
            },
            timeout=REQUEST_TIMEOUT,
        )

        if lookup.status_code == 200 and lookup.json():
            source_id = lookup.json()[0]["id"]

            response = requests.patch(
                f"{SUPABASE_URL}/rest/v1/sources",
                headers=supabase_headers({"Prefer": "return=minimal"}),
                params={"id": f"eq.{source_id}"},
                json=payload,
                timeout=REQUEST_TIMEOUT,
            )
        else:
            response = requests.post(
                f"{SUPABASE_URL}/rest/v1/sources",
                headers=supabase_headers({"Prefer": "return=minimal"}),
                json=payload,
                timeout=REQUEST_TIMEOUT,
            )

        if response.status_code in (200, 201, 204):
            print(
                f"✅ Supabase sources yazıldı: {payload.get('name')} | {payload.get('monitor_method')} | score={payload.get('discovery_score')}",
                flush=True,
            )
            return True

        print(f"Supabase sources yazma xətası: {response.status_code} | {response.text[:300]}", flush=True)
        return False

    except Exception as e:
        print(f"Supabase sources istisnası: {e}", flush=True)
        return False


def sync_discovery_results_to_supabase(approved_sites: list[dict], review_sites: list[dict], rejected_sites: list[dict]):
    if not supabase_ready():
        print("Supabase sync keçildi: env yoxdur", flush=True)
        return

    accepted = 0
    manual = 0
    rejected = 0

    for site in approved_sites:
        domain = clean_domain(site.get("url", ""))
        method = source_monitor_method(site)
        score = int(site.get("score", 0) or 0)
        if upsert_source_to_supabase(site):
            accepted += 1
        save_discovery_log(domain, site.get("url", ""), "accepted", "approved", method, score)

    for site in review_sites:
        domain = clean_domain(site.get("url", ""))
        method = source_monitor_method(site)
        score = int(site.get("score", 0) or 0)
        if upsert_source_to_supabase(site):
            manual += 1
        save_discovery_log(domain, site.get("url", ""), "manual_needed", "review", method, score)

    for site in rejected_sites:
        domain = clean_domain(site.get("url", ""))
        score = int(site.get("score", 0) or 0)
        reason = site.get("reason") or "; ".join((site.get("analysis") or {}).get("reasons", []))
        save_rejected_source(site)
        save_discovery_log(domain, site.get("url", ""), "rejected", reason, "none", score)
        rejected += 1

    print("📦 Supabase discovery sync", flush=True)
    print(f"✅ accepted: {accepted}", flush=True)
    print(f"🟡 manual_needed: {manual}", flush=True)
    print(f"🔴 rejected: {rejected}", flush=True)


def read_json(filename: str, default):
    try:
        with open(filename, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return default
    except Exception as e:
        print(f"JSON oxunmadı: {filename} | {e}", flush=True)
        return default


def write_json(filename: str, data):
    with open(filename, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def clean_text(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def clean_domain(url: str) -> str:
    try:
        value = clean_text(url).lower()
        if value and "://" not in value:
            value = "https://" + value
        domain = urlparse(value).netloc.lower().strip()
        if domain.startswith("www."):
            domain = domain[4:]
        return domain
    except Exception:
        return ""


def is_excluded_domain(url: str) -> bool:
    domain = clean_domain(url)
    if DISCOVERY_BLOCK_GOV and (domain == "gov.az" or domain.endswith(".gov.az")):
        return True
    return policy_is_excluded_domain(domain)


def base_url(url: str) -> str:
    parsed = urlparse(url)
    if not parsed.scheme or not parsed.netloc:
        return ""
    return f"{parsed.scheme}://{parsed.netloc}"


def is_subdomain_of(domain: str, parent_domain: str) -> bool:
    domain = clean_domain(domain)
    parent_domain = clean_domain(parent_domain)
    return bool(domain and parent_domain and domain != parent_domain and domain.endswith("." + parent_domain))


def find_parent_domain(domain: str, existing_domains: set[str]) -> str | None:
    domain = clean_domain(domain)
    if not domain or domain in DISCOVERY_SUBDOMAIN_ALLOWLIST:
        return None
    for existing in sorted(existing_domains, key=len, reverse=True):
        if is_subdomain_of(domain, existing):
            return existing
    return None


def build_rejected_subdomain_site(name: str | None, url: str, parent_domain: str) -> dict:
    domain = clean_domain(url)
    reason = f"subdomain_rejected: parent_domain_exists={parent_domain}"
    return {
        "name": name or domain,
        "url": url,
        "enabled": False,
        "rss_url": None,
        "selector": None,
        "xpaths": [],
        "keywords": DEFAULT_KEYWORDS,
        "limit": 0,
        "score": 0,
        "status": "rejected",
        "reason": reason,
        "analysis": {
            "rss_count": 0,
            "news_link_count": 0,
            "topic_keyword_count": 0,
            "article_block_count": 0,
            "reasons": [reason],
        },
        "source_type": "subdomain_rejected",
        "monitor_method": "none",
    }


def normalize_url(url: str) -> str:
    return clean_text(url).split("#")[0].rstrip("/").lower()


def is_bad_domain(url: str) -> bool:
    domain = clean_domain(url)
    if not domain:
        return True
    if is_excluded_domain(domain):
        return True
    if any(bad in domain for bad in BAD_DOMAINS):
        return True
    if any(part in domain for part in BLOCKED_DOMAIN_PARTS):
        return True
    return False


def is_bad_url(url: str) -> bool:
    u = str(url or "").lower()
    if not u.startswith("http"):
        return True
    if any(bad in u for bad in BAD_URL_WORDS):
        return True
    return is_bad_domain(url)


def google_news_rss(query: str) -> str:
    return (
        "https://news.google.com/rss/search?"
        f"q={quote_plus(query)}"
        "&hl=az&gl=AZ&ceid=AZ:az"
    )


def looks_like_commercial_site(name: str | None, url: str, html_text: str = "") -> bool:
    domain = clean_domain(url)
    value = f"{name or ''} {domain} {url} {html_text[:3000]}".lower()
    commercial_hits = sum(1 for word in COMMERCIAL_SITE_WORDS if word in value)
    news_hits = sum(1 for word in NEWS_SECTION_WORDS if word in value)
    if commercial_hits >= 3 and news_hits < 3:
        return True
    if commercial_hits >= 2 and not any(hint in domain for hint in ("news", "xeber", "media", "press")):
        return True
    return False



def count_az_news_language_signals(html_text: str) -> int:
    value = clean_text((html_text or "")[:12000]).lower()
    return sum(1 for word in AZ_NEWS_LANGUAGE_WORDS if word in value)

def entry_datetime(entry) -> datetime | None:
    parsed = entry.get("published_parsed") or entry.get("updated_parsed")
    if parsed:
        try:
            return datetime(*parsed[:6], tzinfo=timezone.utc).astimezone(BAKU_TZ)
        except Exception:
            return None
    return None


def count_recent_feed_entries(feed, lookback_hours: int) -> int:
    now = datetime.now(BAKU_TZ)
    cutoff = now - timedelta(hours=max(1, lookback_hours))
    count = 0
    for entry in feed.entries or []:
        dt = entry_datetime(entry)
        if dt and cutoff <= dt <= now + timedelta(minutes=5):
            count += 1
    return count


def count_recent_rss_entries(session: requests.Session, rss_url: str, lookback_hours: int) -> int:
    if not rss_url:
        return 0
    try:
        r = session.get(rss_url, headers=HEADERS, timeout=REQUEST_TIMEOUT, allow_redirects=True)
        if r.status_code != 200:
            return 0
        return count_recent_feed_entries(feedparser.parse(r.text), lookback_hours)
    except Exception:
        return 0


def google_news_activity_count(session: requests.Session, domain: str, lookback_hours: int) -> int:
    if not domain:
        return 0
    try:
        feed = feedparser.parse(google_news_rss(f"site:{domain} when:{max(1, lookback_hours)}h"))
        return count_recent_feed_entries(feed, lookback_hours)
    except Exception:
        return 0


def build_search_queries(mode: str) -> list[str]:
    queries = []
    base_queries = GENERAL_NEWS_QUERIES_DEEP if mode == "deep" else GENERAL_NEWS_QUERIES_FAST
    edu_queries = EDU_CHECK_QUERIES_DEEP if mode == "deep" else EDU_CHECK_QUERIES_FAST

    # 1) Default hədəf: gündəlik Azərbaycan xəbər saytları.
    queries.extend(base_queries)

    # 2) Təhsil/elm xüsusi axtarışı artıq default deyil; yalnız lazım olanda env ilə açılır.
    if DISCOVERY_INCLUDE_EDU_SPECIALIZED:
        queries.extend(edu_queries)

    out = []
    seen = set()
    for q in queries:
        q = clean_text(q)
        if not q:
            continue
        if "gov" in q.lower() and DISCOVERY_BLOCK_GOV:
            continue
        if q.lower() in seen:
            continue
        seen.add(q.lower())
        out.append(q)
    return out
def looks_like_news_url(url: str) -> bool:
    u = url.lower()

    if any(bad in u for bad in BAD_URL_WORDS):
        return False

    news_hints = [
        "news", "xeber", "xeberler", "xəbər", "xəbərlər",
        "latest", "lastnews", "son-xeber", "all-news", "allnews",
        "press", "media", "announcements", "announcement", "updates", "update",
        "events", "event", "blog", "posts", "articles", "article",
        "publications", "publication", "research", "newsroom",
        "duyurular", "duyuru", "elanlar", "notice", "notices",
        "yenilikler", "yeniliklər", "gundem", "gündəm",
        "world", "politics", "economy", "society",
        "sport", "sports", "football", "basketball",
        "dunya", "ölke", "olke", "cemiyyet", "siyaset", "iqtisadiyyat",
        "medeniyyet", "kriminal", "hadise", "region",
    ]

    return any(hint in u for hint in news_hints)


def is_article_like_url(url: str) -> bool:
    u = url.lower()

    if any(bad in u for bad in BAD_URL_WORDS):
        return False

    # Tarixli URL.
    if re.search(r"(20[2-9][0-9])", u):
        return True

    # ID əsaslı xəbər.
    if re.search(r"/\d{4,}", u):
        return True

    # Uzun slug: /bu-bir-xeber-basligidir
    if re.search(
        r"/(?:[a-z0-9əöğüşıç-]+-){2,}[a-z0-9əöğüşıç-]+/?$",
        u,
    ):
        return True

    article_hints = [
        "news", "xeber", "article", "story", "post", "read", "content",
        "football", "sport", "sports", "world", "economy", "politics",
        "society", "dunya", "cemiyyet", "siyaset", "hadise",
    ]

    return any(hint in u for hint in article_hints)


def discover_rss_links(page_url: str, page_html: str | None = None) -> list[str]:
    rss_links = []
    root = base_url(page_url)

    if page_html:
        try:
            soup = BeautifulSoup(page_html, "html.parser")
            for tag in soup.find_all("link", href=True):
                tag_type = (tag.get("type") or "").lower()
                title = (tag.get("title") or "").lower()
                href = tag.get("href")
                if "rss" in tag_type or "atom" in tag_type or "rss" in title or "feed" in title:
                    rss_links.append(urljoin(page_url, href))
        except Exception:
            pass

    for path in RSS_PATHS:
        rss_links.append(urljoin(root, path))

    cleaned = []
    for rss in rss_links:
        if rss and rss.startswith("http") and rss not in cleaned and not is_bad_url(rss):
            cleaned.append(rss)
    return cleaned[:10]


def test_rss(session: requests.Session, rss_url: str) -> tuple[bool, int]:
    try:
        r = session.get(rss_url, headers=HEADERS, timeout=REQUEST_TIMEOUT, allow_redirects=True)
        if r.status_code != 200 or not r.text:
            return False, 0
        feed = feedparser.parse(r.text)
        count = len(feed.entries or [])
        return count >= 3, count
    except Exception:
        return False, 0


def find_working_rss(session: requests.Session, page_url: str, page_html: str | None = None) -> tuple[str | None, int]:
    for rss_url in discover_rss_links(page_url, page_html):
        ok, count = test_rss(session, rss_url)
        if ok:
            return rss_url, count
    return None, 0


def discover_sitemap_urls(session: requests.Session, root_url: str, limit: int = 60) -> list[str]:
    """Sitemap içindən xəbər bölməsi və xəbər linklərinə oxşayan URL-ləri çıxarır."""
    root = base_url(root_url)
    if not root:
        return []

    sitemap_candidates = [
        urljoin(root, "/sitemap.xml"),
        urljoin(root, "/sitemap_index.xml"),
        urljoin(root, "/sitemap-index.xml"),
        urljoin(root, "/sitemap1.xml"),
        urljoin(root, "/post-sitemap.xml"),
        urljoin(root, "/page-sitemap.xml"),
        urljoin(root, "/news-sitemap.xml"),
    ]

    found = []
    checked_sitemaps = set()

    def parse_sitemap(sitemap_url: str, depth: int = 0):
        if depth > 1:
            return
        if sitemap_url in checked_sitemaps:
            return
        checked_sitemaps.add(sitemap_url)

        try:
            r = session.get(sitemap_url, headers=HEADERS, timeout=REQUEST_TIMEOUT, allow_redirects=True)
            if r.status_code != 200 or not r.text:
                return

            urls = re.findall(r"<loc>\s*(.*?)\s*</loc>", r.text, flags=re.I)
            for item_url in urls:
                item_url = clean_text(item_url)
                if not item_url.startswith("http"):
                    continue
                if is_bad_url(item_url):
                    continue
                if clean_domain(item_url) != clean_domain(root):
                    continue

                if item_url.lower().endswith(".xml") and "sitemap" in item_url.lower():
                    parse_sitemap(item_url, depth + 1)
                    continue

                if looks_like_news_url(item_url) or is_article_like_url(item_url):
                    if item_url not in found:
                        found.append(item_url.rstrip("/"))

                if len(found) >= limit:
                    return
        except Exception:
            return

    for sitemap in sitemap_candidates:
        parse_sitemap(sitemap, 0)
        if len(found) >= limit:
            break

    return found[:limit]


def extract_home_news_links(session: requests.Session, root: str, limit: int = 40) -> list[str]:
    """Ana səhifədə xəbər/media/elan/yenilik mətnli linkləri daha ağıllı toplayır."""
    found = []
    try:
        r = session.get(root, headers=HEADERS, timeout=REQUEST_TIMEOUT, allow_redirects=True)
        if r.status_code != 200:
            return []

        soup = BeautifulSoup(r.text, "html.parser")
        for a in soup.find_all("a", href=True):
            text = clean_text(a.get_text(" ", strip=True)).lower()
            href = urljoin(root, a["href"]).split("#")[0].rstrip("/")

            if not href or href in found or is_bad_url(href):
                continue
            if clean_domain(href) != clean_domain(root):
                continue

            combined = f"{text} {href.lower()}"

            if any(word in combined for word in NEWS_SECTION_WORDS) or looks_like_news_url(href):
                found.append(href)

            if len(found) >= limit:
                break
    except Exception:
        pass

    return found


def crawl_second_level_news_sections(session: requests.Session, first_level_urls: list[str], settings: dict) -> list[str]:
    """Tapılan birinci səviyyə bölmələrin içindən əlavə xəbər bölmələri tapır."""
    found = []

    for first_url in first_level_urls[:15]:
        try:
            r = session.get(first_url, headers=HEADERS, timeout=REQUEST_TIMEOUT, allow_redirects=True)
            if r.status_code != 200:
                continue

            soup = BeautifulSoup(r.text, "html.parser")
            for a in soup.find_all("a", href=True):
                text = clean_text(a.get_text(" ", strip=True)).lower()
                href = urljoin(first_url, a["href"]).split("#")[0].rstrip("/")

                if not href or href in found or is_bad_url(href):
                    continue
                if clean_domain(href) != clean_domain(first_url):
                    continue

                combined = f"{text} {href.lower()}"
                if any(word in combined for word in NEWS_SECTION_WORDS) or looks_like_news_url(href) or is_article_like_url(href):
                    ok, _news_count, _edu_count, _html = page_news_stats(session, href)
                    if ok:
                        found.append(href)

                if len(found) >= settings["max_sections_per_source"]:
                    return found
        except Exception:
            continue

    return found


def page_news_stats(session: requests.Session, url: str) -> tuple[bool, int, int, str]:
    """Return: has_news, news_link_count, edu_keyword_count, html_text.

    Məqsəd saytın izlənə bilib-bilməyəcəyini yumşaq yoxlamaqdır.
    Əsas xəbər saytları bəzən /news və /xeber pattern-i işlətmir.
    Ona görə başlıq, slug, tarix, id və xəbər sözləri birlikdə qiymətləndirilir.
    """
    try:
        r = session.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT, allow_redirects=True)
        if r.status_code != 200:
            return False, 0, 0, ""

        html_text = r.text or ""
        soup = BeautifulSoup(html_text, "html.parser")

        news_links = set()
        edu_links = set()

        title_bad_words = [
            "ana səhifə", "haqqımızda", "əlaqə", "reklam", "giriş",
            "qeydiyyat", "axtarış", "abunə", "facebook", "instagram",
            "youtube", "telegram", "twitter", "linkedin", "rss", "menu", "menyu",
            "privacy", "terms", "cookie",
        ]

        news_words = [
            "xəbər", "xeber", "son xəbər", "son xeber", "gündəm", "gundem",
            "siyasət", "cəmiyyət", "cemiyyet", "dünya", "iqtisadiyyat",
            "hadisə", "ölkə", "olke", "region", "təhsil", "elm", "idman",
            "mədəniyyət", "medeniyyet", "şou", "show", "kriminal",
        ]

        for a in soup.find_all("a", href=True):
            text = clean_text(a.get_text(" ", strip=True))
            href = urljoin(url, a["href"]).split("#")[0].rstrip("/")

            if not href or clean_domain(href) != clean_domain(url):
                continue
            if is_bad_url(href):
                continue

            text_lower = text.lower()
            href_lower = href.lower()
            combined = f"{text_lower} {href_lower}"

            if any(w in combined for w in title_bad_words):
                continue

            has_real_title = len(text) >= 12

            pattern_signal = looks_like_news_url(href) or is_article_like_url(href)
            date_signal = bool(re.search(r"(20[2-9][0-9])", href_lower))
            id_signal = bool(re.search(r"/\d{4,}($|[-_/])", href_lower))
            long_slug_signal = bool(
                re.search(r"/(?:[a-z0-9əöğüşıç-]+-){2,}[a-z0-9əöğüşıç-]+(?:/|$)", href_lower)
            )
            word_signal = any(word in combined for word in news_words)

            if has_real_title and (
                pattern_signal
                or date_signal
                or id_signal
                or long_slug_signal
                or word_signal
            ):
                news_links.add(normalize_url(href))

            if any(k in combined for k in DEFAULT_KEYWORDS):
                edu_links.add(normalize_url(href))

            if len(news_links) >= 25 and len(edu_links) >= 3:
                return True, len(news_links), len(edu_links), html_text

        # Çox vacib: əsas xəbər saytını itirməmək üçün 1 real link belə kifayətdir.
        return len(news_links) >= 1 or len(edu_links) >= 1, len(news_links), len(edu_links), html_text

    except Exception as e:
        print(f"page_news_stats xətası: {url} | {e}", flush=True)
        return False, 0, 0, ""


def find_news_sections(session: requests.Session, source_url: str, settings: dict) -> list[str]:
    root = base_url(source_url)
    if not root or is_bad_url(root):
        return []

    found = []

    def add_candidate(candidate_url: str):
        candidate_url = candidate_url.rstrip("/")
        if not candidate_url or candidate_url in found or is_bad_url(candidate_url):
            return False
        if clean_domain(candidate_url) != clean_domain(root):
            return False

        ok, news_count, _edu_count, _html = page_news_stats(session, candidate_url)

        # Section üçün 1 link də kifayətdir, amma varsa daha çox linkli səhifələr üstündür.
        if ok or news_count >= 1:
            found.append(candidate_url)
            return True

        return False

    # 1) Əvvəl ana səhifəni də namizəd kimi yoxla.
    # Ajans, Yenicag, Musavat kimi saytların son xəbərləri ana səhifədən götürülə bilər.
    add_candidate(root)

    if len(found) >= settings["max_sections_per_source"]:
        return found

    # 2) Gələn URL özü xəbər bölməsinə oxşayırsa yoxla.
    ok, _news_count, _edu_count, _html = page_news_stats(session, source_url)
    if looks_like_news_url(source_url) and ok:
        candidate = source_url.rstrip("/")
        if candidate not in found:
            found.append(candidate)

    if len(found) >= settings["max_sections_per_source"]:
        return found

    # 3) Ən çox işlənən xəbər path-ləri yoxla.
    for path in settings["paths"]:
        candidate = urljoin(root, path).rstrip("/")
        add_candidate(candidate)
        if len(found) >= settings["max_sections_per_source"]:
            return found

    # 4) Ana səhifədən xəbər/media/elan/yenilik linklərini çıxar.
    home_links = extract_home_news_links(session, root, limit=80)
    for href in home_links:
        add_candidate(href)
        if len(found) >= settings["max_sections_per_source"]:
            return found

    # 5) İkinci səviyyə crawl.
    second_level = crawl_second_level_news_sections(session, home_links, settings)
    for href in second_level:
        add_candidate(href)
        if len(found) >= settings["max_sections_per_source"]:
            return found

    # 6) Sitemap-dan xəbər linklərinə/bölmələrə oxşayan URL-ləri yoxla.
    sitemap_links = discover_sitemap_urls(session, root, limit=100)
    for href in sitemap_links:
        add_candidate(href)
        if len(found) >= settings["max_sections_per_source"]:
            return found

    return found

def guess_selector_and_xpath(session: requests.Session, url: str) -> tuple[str | None, list[str], int]:
    try:
        r = session.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT, allow_redirects=True)
        if r.status_code != 200:
            return None, [], 0

        soup = BeautifulSoup(r.text, "html.parser")
        class_counter = Counter()
        xpath_candidates = []
        article_count = 0

        for tag in soup.find_all(["article", "div", "li", "section"]):
            links = tag.find_all("a", href=True)
            if not links:
                continue

            has_article_link = False
            for a in links[:5]:
                href = urljoin(url, a.get("href"))
                title = clean_text(a.get_text(" ", strip=True))
                if len(title) >= 12 and clean_domain(href) == clean_domain(url) and not is_bad_url(href):
                    if is_article_like_url(href) or looks_like_news_url(href):
                        has_article_link = True
                        break

            if not has_article_link:
                continue

            article_count += 1
            classes = tag.get("class") or []
            if classes:
                simple_classes = [c for c in classes if len(c) >= 3 and not re.search(r"\d{4,}", c)]
                if simple_classes:
                    selector = "." + ".".join(simple_classes[:2])
                    class_counter[selector] += 1

        selector = None
        for candidate, count in class_counter.most_common(10):
            if count >= 3:
                selector = candidate
                break

        if selector:
            class_name = selector.split(".")[1]
            xpath_candidates.append(f"//*[contains(@class,'{class_name}')]//a[@href]")

        generic_xpaths = [
            "//article//a[@href]",
            "//div[contains(@class,'news')]//a[@href]",
            "//div[contains(@class,'xeber')]//a[@href]",
            "//div[contains(@class,'post')]//a[@href]",
            "//li[contains(@class,'news')]//a[@href]",
            "//li[contains(@class,'xeber')]//a[@href]",
        ]
        for xp in generic_xpaths:
            if xp not in xpath_candidates:
                xpath_candidates.append(xp)

        return selector, xpath_candidates[:5], article_count
    except Exception:
        return None, [], 0


def analyze_section(session: requests.Session, name: str, section_url: str) -> dict:
    score = 0
    reasons = []
    selector = None
    xpaths = []
    rss_url = None
    rss_count = 0
    news_count = 0
    edu_keyword_count = 0
    html_text = ""
    article_count = 0

    domain = clean_domain(section_url)

    if is_bad_url(section_url):
        return {
            "name": name or domain,
            "url": section_url,
            "enabled": True,
            "score": 0,
            "status": "rejected",
            "reason": "bad_url_or_gov_blocked",
            "source_type": "rejected",
        }

    ok, news_count, edu_keyword_count, html_text = page_news_stats(
        session,
        section_url,
    )

    # Əvvəl RSS yoxla. Çünki bəzi saytların HTML-i çətin oxunur, amma RSS işləyir.
    try:
        rss_url, rss_count = find_working_rss(session, section_url, html_text)
    except Exception as e:
        print(f"RSS yoxlama xətası: {section_url} | {e}", flush=True)
        rss_url = None
        rss_count = 0

    if rss_url:
        score += 45
        reasons.append(f"RSS tapıldı ({rss_count})")

    az_language_signals = count_az_news_language_signals(html_text)
    if az_language_signals >= 8:
        score += 10
        reasons.append(f"Azərbaycan xəbər dili siqnalı güclüdür ({az_language_signals})")
    elif az_language_signals >= 3:
        score += 5
        reasons.append(f"Azərbaycan xəbər dili siqnalı var ({az_language_signals})")
    if looks_like_commercial_site(name, section_url, html_text):
        return {
            "name": name or domain,
            "url": section_url.rstrip("/"),
            "enabled": True,
            "rss_url": rss_url,
            "selector": None,
            "xpaths": [],
            "keywords": DEFAULT_KEYWORDS,
            "limit": 10,
            "score": 0,
            "status": "rejected",
            "reason": "commercial_site_not_news",
            "analysis": {
                "rss_count": rss_count,
                "news_link_count": news_count,
                "topic_keyword_count": edu_keyword_count,
                "article_block_count": 0,
                "activity_count": 0,
                "activity_source": "commercial_filter",
                "reasons": ["commercial site signals, not a news source"],
            },
            "source_type": "rejected_commercial",
        }

    rss_recent_count = count_recent_rss_entries(
        session,
        rss_url,
        DISCOVERY_ACTIVITY_LOOKBACK_HOURS,
    ) if rss_url else 0
    google_recent_count = google_news_activity_count(
        session,
        domain,
        DISCOVERY_ACTIVITY_LOOKBACK_HOURS,
    )
    activity_count = max(rss_recent_count, google_recent_count)
    activity_source = "rss" if rss_recent_count >= google_recent_count else "google_news"

    if DISCOVERY_REQUIRE_ACTIVITY and activity_count < DISCOVERY_MIN_NEWS_PER_PERIOD:
        return {
            "name": name or domain,
            "url": section_url.rstrip("/"),
            "enabled": True,
            "rss_url": rss_url,
            "selector": None,
            "xpaths": [],
            "keywords": DEFAULT_KEYWORDS,
            "limit": 10,
            "score": min(score, 20),
            "status": "rejected",
            "reason": f"insufficient_news_activity: {activity_count}/{DISCOVERY_MIN_NEWS_PER_PERIOD} in last {DISCOVERY_ACTIVITY_LOOKBACK_HOURS}h",
            "analysis": {
                "rss_count": rss_count,
                "news_link_count": news_count,
                "topic_keyword_count": edu_keyword_count,
                "article_block_count": 0,
                "activity_count": activity_count,
                "activity_source": activity_source,
                "rss_recent_count": rss_recent_count,
                "google_recent_count": google_recent_count,
                "reasons": reasons + [f"activity too low ({activity_count}/{DISCOVERY_MIN_NEWS_PER_PERIOD})"],
            },
            "source_type": "rejected_inactive_news",
        }

    if activity_count >= DISCOVERY_MIN_NEWS_PER_PERIOD:
        score += 25
        reasons.append(
            f"aktiv xəbər axını var ({activity_count}/{DISCOVERY_ACTIVITY_LOOKBACK_HOURS}h, {activity_source})"
        )

    # page_news_stats uğursuzdursa, saytı dərhal reject etmə.
    # Ana səhifə və müasir xəbər saytları üçün yumşaq fallback.
    if not ok:
        fallback_score = 0
        fallback_reasons = []

        try:
            html_lower = (html_text or "").lower()

            article_signals = [
                "/news/",
                "/xeber/",
                "/xeberler/",
                "/xəbər/",
                "/xəbərlər/",
                "/article/",
                "/story/",
                "/post/",
                "/read/",
                "/item/",
                "/son-xeber/",
                "/latest/",
                "/2024/",
                "/2025/",
                "/2026/",
                "news/",
                "xeber/",
                "article/",
                "story/",
            ]

            signal_count = sum(
                html_lower.count(signal)
                for signal in article_signals
            )

            if signal_count >= 10:
                fallback_score += 45
                fallback_reasons.append(
                    f"homepage article signals ({signal_count})"
                )
            elif signal_count >= 5:
                fallback_score += 35
                fallback_reasons.append(
                    f"homepage article signals ({signal_count})"
                )
            elif signal_count >= 2:
                fallback_score += 25
                fallback_reasons.append(
                    f"some article signals ({signal_count})"
                )

            news_words = [
                "son xəbər",
                "son xəbərlər",
                "xəbərlər",
                "xeberler",
                "gündəm",
                "gundem",
                "siyasət",
                "cəmiyyət",
                "dünya",
                "iqtisadiyyat",
                "hadisə",
            ]

            word_count = sum(1 for word in news_words if word in html_lower)

            if word_count >= 3:
                fallback_score += 15
                fallback_reasons.append(f"news words found ({word_count})")
            elif word_count >= 1:
                fallback_score += 7
                fallback_reasons.append(f"some news words found ({word_count})")

        except Exception as e:
            print(f"Homepage fallback analiz xətası: {section_url} | {e}", flush=True)

        # RSS varsa, HTML zəif olsa belə review kimi saxla.
        total_fallback_score = score + fallback_score

        if total_fallback_score >= 40:
            status = "review"
        elif total_fallback_score >= 30:
            status = "review"
        elif domain.endswith(".az") and name and name != domain:
            # Google News bu domeni mənbə kimi veribsə, onu tam itirmirik.
            # Belə saytlar review/manual_needed kimi adminə düşsün, sonra metod seçilər.
            total_fallback_score = max(total_fallback_score, 35)
            fallback_reasons.append("google news source fallback")
            status = "review"
        else:
            status = "rejected"

        return {
            "name": name or domain,
            "url": section_url.rstrip("/"),
            "enabled": True,
            "rss_url": rss_url,
            "selector": None,
            "xpaths": [],
            "keywords": DEFAULT_KEYWORDS,
            "limit": 10,
            "score": total_fallback_score,
            "status": status,
            "reason": "homepage_fallback" if status != "rejected" else f"news links insufficient: {news_count}",
            "analysis": {
                "rss_count": rss_count,
                "news_link_count": news_count,
                "topic_keyword_count": edu_keyword_count,
                "article_block_count": 0,
                "activity_count": activity_count,
                "activity_source": activity_source,
                "rss_recent_count": rss_recent_count,
                "google_recent_count": google_recent_count,
                "reasons": reasons + fallback_reasons,
            },
            "source_type": "homepage_fallback",
        }

    # Xəbər saytı olması əsasdır.
    if news_count >= 10:
        score += 30
        reasons.append(f"çox xəbər linki var ({news_count})")
    elif news_count >= 5:
        score += 22
        reasons.append(f"xəbər linkləri var ({news_count})")
    else:
        score += 15
        reasons.append(f"minimum xəbər linkləri var ({news_count})")

    try:
        selector, xpaths, article_count = guess_selector_and_xpath(
            session,
            section_url,
        )
    except Exception as e:
        print(f"Selector analiz xətası: {section_url} | {e}", flush=True)
        selector = None
        xpaths = []
        article_count = 0

    if selector:
        score += 15
        reasons.append(f"selector tapıldı: {selector}")
    elif xpaths:
        score += 7
        reasons.append("generic xpath əlavə edildi")

    # Mövzu açar sözləri yalnız zəif bonusdur; discovery üçün əsas meyar gündəlik xəbər axınıdır.
    if edu_keyword_count >= 5:
        score += 5
        reasons.append(f"mövzu siqnalları var ({edu_keyword_count})")
    if score >= 70:
        status = "approved"
    elif score >= 40:
        status = "review"
    else:
        status = "rejected"

    monitor_method = "html"
    if rss_url:
        monitor_method = "rss"
    elif selector:
        monitor_method = "selector"
    elif xpaths:
        monitor_method = "xpath"

    return {
        "name": name or domain,
        "url": section_url.rstrip("/"),
        "enabled": True,
        "rss_url": rss_url,
        "selector": selector,
        "xpaths": xpaths,
        "keywords": DEFAULT_KEYWORDS,
        "limit": 10,
        "score": score,
        "status": status,
        "reason": "; ".join(reasons),
        "analysis": {
            "rss_count": rss_count,
            "news_link_count": news_count,
            "topic_keyword_count": edu_keyword_count,
            "article_block_count": article_count,
            "activity_count": activity_count,
            "activity_source": activity_source,
            "rss_recent_count": rss_recent_count,
            "google_recent_count": google_recent_count,
            "reasons": reasons,
        },
        "source_type": "discovered_news_site_first",
        "monitor_method": monitor_method,
    }


def collect_existing_domains() -> set[str]:
    domains = set()

    # VACİB: REJECTED_FILE burada oxunmur.
    # Əvvəl reject edilən saytlar sonradan düzəlmiş discovery məntiqi ilə yenidən yoxlanmalıdır.
    # Əks halda Ajans.az, Yenicag.az, Musavat.com kimi əsas saytlar bir dəfə reject edildisə,
    # həmişəlik atlanır.
    for filename in [DISCOVERED_FILE, CONFIG_FILE, REVIEW_FILE]:
        data = read_json(filename, {"sites": []})
        if not isinstance(data, dict):
            continue
        for site in data.get("sites", []):
            url = site.get("url", "")
            domain = clean_domain(url)
            if domain:
                domains.add(domain)

    if supabase_ready():
        try:
            offset = 0
            page_size = 1000
            while True:
                response = requests.get(
                    f"{SUPABASE_URL}/rest/v1/sources",
                    headers=supabase_headers(),
                    params={
                        "select": "base_url,latest_url",
                        "limit": str(page_size),
                        "offset": str(offset),
                    },
                    timeout=REQUEST_TIMEOUT,
                )
                if response.status_code != 200:
                    print(f"Supabase sources domain oxunmadı: {response.status_code} | {response.text[:200]}", flush=True)
                    break

                rows = response.json() or []
                if not rows:
                    break

                for row in rows:
                    for key in ("base_url", "latest_url"):
                        domain = clean_domain(row.get(key, ""))
                        if domain:
                            domains.add(domain)

                if len(rows) < page_size:
                    break
                offset += page_size
        except Exception as exc:
            print(f"Supabase sources domain istisnası: {exc}", flush=True)

    return domains


def append_unique(filename: str, new_sites: list[dict]) -> int:
    data = read_json(filename, {"sites": []})
    if not isinstance(data, dict):
        data = {"sites": []}
    if "sites" not in data or not isinstance(data["sites"], list):
        data["sites"] = []

    # Eyni domen bir dəfə saxlanır. Hədəf sayt bazası qurmaqdır.
    existing_domains = {clean_domain(site.get("url", "")) for site in data["sites"] if site.get("url")}
    added = 0

    for site in new_sites:
        d = clean_domain(site.get("url", ""))
        if not d:
            continue
        if d in existing_domains:
            continue
        data["sites"].append(site)
        existing_domains.add(d)
        added += 1

    write_json(filename, data)
    return added


def discover_sites(mode: str = "fast", add_to_config: bool = False):
    settings = get_mode_settings(mode)

    print("🔍 Discovery 2.0 başladı", flush=True)
    print("Versiya:", DISCOVERY_VERSION, flush=True)
    print("Rejim:", mode, flush=True)
    print(f"Discovery siqnal sayı: {len(DEFAULT_KEYWORDS)}", flush=True)

    known_domains = collect_existing_domains()
    processed_domains = set()
    queries = build_search_queries(mode)[:settings["max_queries"]]
    print(f"Axtarış sorğusu sayı: {len(queries)}", flush=True)

    # Domain üzrə ən yaxşı nəticəni saxlayırıq.
    best_by_domain = {}
    rejected_sites = []

    session = requests.Session()
    session.headers.update(HEADERS)

    for query in queries:
        if "gov" in query.lower() and DISCOVERY_BLOCK_GOV:
            continue

        print("Axtarılır:", query, flush=True)

        try:
            feed = feedparser.parse(google_news_rss(query))
        except Exception as e:
            print("Google News RSS xətası:", e, flush=True)
            continue

        print("Nəticə sayı:", len(feed.entries), flush=True)

        for entry in feed.entries[:settings["max_entries_per_query"]]:
            source = entry.get("source", {})
            source_name = None
            source_url = None

            if isinstance(source, dict):
                source_name = source.get("title")
                source_url = source.get("href")

            if not source_url or not str(source_url).startswith("http"):
                continue

            if is_bad_url(source_url):
                continue

            domain = clean_domain(source_url)
            if not domain:
                continue

            if domain in known_domains or domain in processed_domains:
                continue

            parent_domain = find_parent_domain(domain, known_domains | set(best_by_domain.keys()))
            if parent_domain:
                rejected_sites.append(build_rejected_subdomain_site(source_name, source_url, parent_domain))
                processed_domains.add(domain)
                print(f"SUBDOMAIN REJECT: {domain} | parent={parent_domain}", flush=True)
                continue

            processed_domains.add(domain)

            sections = find_news_sections(session, source_url, settings)

            # Əsas prinsip: Google News domeni mənbə kimi veribsə, onu itirmirik.
            # Bölmə tapılmasa belə manual review-ə salırıq.
            if not sections:
                print(
                    f"⚠️ Xəbər bölməsi tapılmadı, Google News manual review: {source_name or domain}",
                    flush=True,
                )

                analyzed = analyze_section(
                    session,
                    source_name or domain,
                    source_url,
                )

                score = int(analyzed.get("score", 0) or 0)

                if is_hard_rejected(analyzed):
                    rejected_sites.append(analyzed)
                    print(
                        f"🔴 HARD REJECT {analyzed.get('score')}: {analyzed.get('name')} | {source_url} | {analyzed.get('reason')}",
                        flush=True,
                    )
                    time.sleep(settings["sleep"])
                    continue

                # Xüsusi təhlükəsiz fallback: əsas xəbər saytları sıfır balla itməsin.
                analyzed["status"] = "review"
                analyzed["score"] = max(score, 35)
                analyzed["reason"] = analyzed.get("reason") or "google_news_source_manual_review"
                analyzed["source_type"] = analyzed.get("source_type") or "google_news_source"
                analyzed.setdefault("keywords", DEFAULT_KEYWORDS)
                analyzed.setdefault("limit", 10)
                analyzed.setdefault("rss_url", None)
                analyzed.setdefault("selector", None)
                analyzed.setdefault("xpaths", [])

                analysis = analyzed.get("analysis")
                if not isinstance(analysis, dict):
                    analysis = {
                        "rss_count": 0,
                        "news_link_count": 0,
                        "topic_keyword_count": 0,
                        "article_block_count": 0,
                        "reasons": [],
                    }
                reasons = analysis.get("reasons") or []
                reasons.append("google news source manual review")
                analysis["reasons"] = reasons
                analyzed["analysis"] = analysis

                old = best_by_domain.get(domain)
                if not old or int(analyzed.get("score", 0) or 0) > int(old.get("score", 0) or 0):
                    best_by_domain[domain] = analyzed

                print(
                    f"🟡 GOOGLE NEWS REVIEW {analyzed.get('score')}: {analyzed.get('name')} | {source_url}",
                    flush=True,
                )

                time.sleep(settings["sleep"])
                continue

            for section_url in sections:
                section_domain = clean_domain(section_url)

                if not section_domain or section_domain in known_domains:
                    continue

                parent_domain = find_parent_domain(section_domain, known_domains | set(best_by_domain.keys()))
                if parent_domain:
                    rejected_sites.append(build_rejected_subdomain_site(source_name, section_url, parent_domain))
                    processed_domains.add(section_domain)
                    print(f"SUBDOMAIN REJECT: {section_domain} | parent={parent_domain}", flush=True)
                    continue

                analyzed = analyze_section(
                    session,
                    source_name or section_domain,
                    section_url,
                )

                status = analyzed.get("status")
                score = int(analyzed.get("score", 0) or 0)

                # Əgər Google News mənbə veribsə və analiz az bal veribsə belə, onu manual review-də saxlayırıq.
                if status not in ("approved", "review") and source_name and section_domain.endswith(".az") and not is_hard_rejected(analyzed):
                    analyzed["status"] = "review"
                    analyzed["score"] = max(score, 35)
                    analyzed["reason"] = analyzed.get("reason") or "google_news_section_manual_review"
                    status = "review"
                    score = int(analyzed.get("score", 0) or 0)

                if status in ("approved", "review"):
                    old = best_by_domain.get(section_domain)
                    if not old or score > int(old.get("score", 0) or 0):
                        best_by_domain[section_domain] = analyzed

                    print(
                        f"✅ NAMİZƏD {status.upper()} {score}: {analyzed.get('name')} | {section_url}",
                        flush=True,
                    )
                else:
                    rejected_sites.append(analyzed)

                    print(
                        f"🔴 REJECTED {score}: {analyzed.get('name')} | {section_url}",
                        flush=True,
                    )

            time.sleep(settings["sleep"])

    approved_sites = []
    review_sites = []

    for site in best_by_domain.values():
        if site.get("status") == "approved":
            approved_sites.append(site)
        else:
            review_sites.append(site)

    # Əgər sayt review/approved siyahısındadırsa, rejected siyahısında saxlamırıq.
    accepted_domains = {
        clean_domain(site.get("url", ""))
        for site in approved_sites + review_sites
        if site.get("url")
    }
    rejected_sites = [
        site for site in rejected_sites
        if clean_domain(site.get("url", "")) not in accepted_domains
    ]

    discovered_added = 0
    review_added = 0
    rejected_added = 0
    config_added = 0

    if DISCOVERY_WRITE_JSON:
        discovered_added = append_unique(DISCOVERED_FILE, approved_sites + review_sites)
        review_added = append_unique(REVIEW_FILE, review_sites)
        rejected_added = append_unique(REJECTED_FILE, rejected_sites)

        if add_to_config:
            config_added = append_unique(CONFIG_FILE, approved_sites)
    else:
        print("JSON cache yazilmir: DISCOVERY_WRITE_JSON=false", flush=True)

    if DISCOVERY_SYNC_SUPABASE:
        sync_discovery_results_to_supabase(approved_sites, review_sites, rejected_sites)
    else:
        print("Supabase sync ötürüldü: DISCOVERY_SYNC_SUPABASE=false", flush=True)

    print("\n===== DISCOVERY 2.0 YEKUNU =====", flush=True)
    print(f"✅ Approved: {len(approved_sites)} | config-ə əlavə: {config_added}", flush=True)
    print(f"🟡 Review: {len(review_sites)} | review faylına əlavə: {review_added}", flush=True)
    print(f"🔴 Rejected: {len(rejected_sites)} | rejected faylına əlavə: {rejected_added}", flush=True)
    print(f"📌 discovered_sites əlavə: {discovered_added}", flush=True)
    print("================================\n", flush=True)

    return approved_sites + review_sites


def is_bad_pattern(pattern: str) -> bool:
    return any(bad in pattern.lower() for bad in BAD_PATTERNS)


def is_good_pattern(pattern: str) -> bool:
    return any(hint in pattern.lower() for hint in GOOD_PATTERN_HINTS)


def analyze_site_patterns(session: requests.Session, site: dict) -> list[str]:
    url = site.get("url")
    if not url:
        return []

    try:
        print(f"Pattern yoxlanır: {url}", flush=True)
        r = session.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT, allow_redirects=True)
        print("Status:", r.status_code, flush=True)
        if r.status_code != 200:
            return []

        soup = BeautifulSoup(r.text, "html.parser")
        links = []

        for a in soup.find_all("a", href=True):
            full = urljoin(url, a["href"])
            if clean_domain(full) != clean_domain(url):
                continue
            path = urlparse(full).path
            parts = [p for p in path.split("/") if p]

            if len(parts) >= 1:
                p1 = "/" + parts[0] + "/"
                if not is_bad_pattern(p1) and is_good_pattern(p1):
                    links.append(p1)
            if len(parts) >= 2:
                p2 = "/" + parts[0] + "/" + parts[1] + "/"
                if not is_bad_pattern(p2) and is_good_pattern(p2):
                    links.append(p2)

        counter = Counter(links)
        selected = [pattern for pattern, count in counter.most_common(15) if count >= 1]
        print("Tapılan patternlər:", selected, flush=True)
        return selected
    except Exception as e:
        print("Pattern xətası:", e, flush=True)
        return []


def build_patterns():
    print("🧩 Pattern builder başladı", flush=True)
    patterns = read_json(PATTERNS_FILE, {})
    checked = 0
    updated = 0

    all_sources = []
    for filename in [DISCOVERED_FILE, CONFIG_FILE, REVIEW_FILE]:
        data = read_json(filename, {"sites": []})
        if isinstance(data, dict):
            all_sources.extend(data.get("sites", []))

    session = requests.Session()
    session.headers.update(HEADERS)

    for site in all_sources:
        url = site.get("url", "")
        domain = clean_domain(url)
        if not url or not domain:
            continue
        checked += 1
        new_patterns = analyze_site_patterns(session, site)
        if not new_patterns:
            continue
        old_patterns = patterns.get(domain, [])
        merged = []
        for p in old_patterns + new_patterns:
            if p not in merged:
                merged.append(p)
        patterns[domain] = merged[:20]
        updated += 1

    write_json(PATTERNS_FILE, patterns)
    print("Yoxlanılan sayt sayı:", checked, flush=True)
    print("Pattern yenilənən sayt sayı:", updated, flush=True)


def main():
    parser = argparse.ArgumentParser(description="Vizual.az Discovery Engine - sources, logs, rejected_sources")
    parser.add_argument("--mode", choices=["fast", "deep"], default="fast")
    parser.add_argument("--add-to-config", action="store_true", help="Yalnız approved saytları courier_config_clean.json faylına əlavə edir")
    parser.add_argument("--patterns", action="store_true", help="Pattern builder-i məcburi işə salır")
    args = parser.parse_args()

    new_sites = discover_sites(mode=args.mode, add_to_config=args.add_to_config)
    settings = get_mode_settings(args.mode)

    if args.patterns or (settings["build_patterns"] and DISCOVERY_BUILD_PATTERNS):
        build_patterns()

    print("✅ Discovery tamamlandı", flush=True)
    print("Rejim:", args.mode, flush=True)
    print("Yeni namizəd sayı:", len(new_sites), flush=True)


if __name__ == "__main__":
    main()
