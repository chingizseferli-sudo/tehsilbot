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
SCHEDULER_DRY_RUN = os.getenv("SCHEDULER_DRY_RUN", "false").strip().lower() in {"1", "true", "yes"}
DISABLE_TELEGRAM_SEND = os.getenv("DISABLE_TELEGRAM_SEND", "false").strip().lower() in {"1", "true", "yes"}
SOURCE_DEFAULT_INTERVAL_MINUTES = max(1, int(os.getenv("SOURCE_DEFAULT_INTERVAL_MINUTES", "60")))

PATTERNS_FILE = "patterns.json"
BAKU_TZ = ZoneInfo("Asia/Baku")
USER_TELEGRAM_CACHE = {}
LAST_MONITOR_CLEANUP = None
TELEGRAM_LAST_ERROR = ""

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
def looks_like_xml_content(text):
    sample = str(text or "").lstrip()[:500].lower()
    if not sample:
        return False
    return (
        sample.startswith("<?xml")
        or sample.startswith("<rss")
        or sample.startswith("<feed")
        or sample.startswith("<urlset")
        or sample.startswith("<sitemapindex")
        or "<urlset" in sample[:200]
        or "<sitemapindex" in sample[:200]
    )


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



def parse_scheduler_datetime(value):
    value = clean_text(value)
    if not value:
        return None
    try:
        normalized = value.replace("Z", "+00:00")
        dt = datetime.fromisoformat(normalized)
    except Exception:
        try:
            dt = parser.parse(value)
        except Exception:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=BAKU_TZ)
    return dt.astimezone(BAKU_TZ)


def normalize_scheduler_method(method):
    method = clean_text(method).lower()
    if method == "xpath":
        return "xpath_pattern"
    known_methods = {
        "rss",
        "rss_discovered",
        "google_news_fallback",
        "latest_page",
        "selector",
        "xpath_pattern",
        "homepage",
        "sitemap",
        "recoverable",
    }
    return method if method in known_methods else "default"


def get_scheduler_base_interval_minutes(site):
    method = normalize_scheduler_method(site.get("monitor_method", ""))
    method_intervals = {
        "rss": 10,
        "rss_discovered": 10,
        "google_news_fallback": 15,
        "latest_page": 15,
        "selector": 20,
        "xpath_pattern": 20,
        "homepage": 30,
        "sitemap": 45,
        "recoverable": 45,
        "default": 30,
    }
    return method_intervals.get(method, method_intervals["default"])


def get_scheduler_fail_count(site):
    try:
        return int(site.get("consecutive_fail_count") or 0)
    except Exception:
        return 0


def get_scheduler_fail_bucket(fail_count):
    if fail_count >= 5:
        return "fail_5_plus"
    if fail_count >= 3:
        return "fail_3_4"
    if fail_count >= 1:
        return "fail_1_2"
    return "fail_0"


def get_scheduler_fail_multiplier(fail_count):
    if fail_count >= 5:
        return 8
    if fail_count >= 3:
        return 4
    if fail_count >= 1:
        return 2
    return 1


def get_scheduler_interval_minutes(site):
    fail_count = get_scheduler_fail_count(site)
    return max(1, get_scheduler_base_interval_minutes(site) * get_scheduler_fail_multiplier(fail_count))


def classify_scheduler_error(last_error):
    value = clean_text(last_error).lower()
    if not value:
        return "none"
    if "403" in value or "429" in value:
        return "rate_or_block"
    if "timeout" in value or "timed out" in value:
        return "timeout"
    if any(marker in value for marker in ("dns", "name", "connect", "connection")):
        return "network"
    return "error"


def evaluate_source_schedule(site, now=None):
    now = now or datetime.now(BAKU_TZ)
    method = normalize_scheduler_method(site.get("monitor_method", ""))
    base_interval = get_scheduler_base_interval_minutes(site)
    fail_count = get_scheduler_fail_count(site)
    fail_bucket = get_scheduler_fail_bucket(fail_count)
    fail_multiplier = get_scheduler_fail_multiplier(fail_count)
    interval_minutes = max(1, base_interval * fail_multiplier)
    last_checked = parse_scheduler_datetime(site.get("last_checked_at"))
    error_type = classify_scheduler_error(site.get("last_error"))
    decision = {
        "site": site,
        "monitor_method": method,
        "base_interval_minutes": base_interval,
        "fail_multiplier": fail_multiplier,
        "fail_bucket": fail_bucket,
        "interval_minutes": interval_minutes,
        "final_interval_minutes": interval_minutes,
        "fail_count": fail_count,
        "error_type": error_type,
    }
    if not last_checked:
        decision.update({
            "due": True,
            "reason": "never_checked",
            "remaining_minutes": 0,
        })
        return decision
    elapsed_minutes = max(0, int((now - last_checked).total_seconds() // 60))
    remaining_minutes = max(0, interval_minutes - elapsed_minutes)
    due = elapsed_minutes >= interval_minutes
    reason = "interval_elapsed" if due else "recently_checked"
    if not due and fail_count > 0:
        reason = "fail_backoff"
    decision.update({
        "due": due,
        "reason": reason,
        "remaining_minutes": remaining_minutes,
        "last_checked_at": last_checked.isoformat(),
    })
    return decision


def summarize_scheduler_group(decisions, key):
    summary = {}
    for decision in decisions:
        group = decision.get(key) or "unknown"
        bucket = summary.setdefault(group, {"total": 0, "due": 0, "skip": 0})
        bucket["total"] += 1
        if decision.get("due"):
            bucket["due"] += 1
        else:
            bucket["skip"] += 1
    return summary


def format_scheduler_summary(summary):
    return " | ".join(
        f"{key} total={value['total']} due={value['due']} skip={value['skip']}"
        for key, value in sorted(summary.items())
    ) or "none"


def log_scheduler_dry_run_summary(sites, decisions):
    if not SCHEDULER_DRY_RUN:
        return
    total = len(sites)
    due_count = sum(1 for decision in decisions if decision.get("due"))
    skip_count = total - due_count
    reason_counts = {}
    for decision in decisions:
        reason = decision.get("reason") or "unknown"
        reason_counts[reason] = reason_counts.get(reason, 0) + 1
    reasons_text = " | ".join(f"{key}={value}" for key, value in sorted(reason_counts.items())) or "none"
    interval_text = (
        "rss=10m | rss_discovered=10m | google_news_fallback=15m | latest_page=15m | "
        "selector=20m | xpath_pattern=20m | homepage=30m | sitemap=45m | recoverable=45m | default=30m"
    )
    method_text = format_scheduler_summary(summarize_scheduler_group(decisions, "monitor_method"))
    fail_bucket_text = format_scheduler_summary(summarize_scheduler_group(decisions, "fail_bucket"))
    print("[DRY-RUN] Scheduler aktivdir. Menbeler skip edilmeyecek.", flush=True)
    print(f"[DRY-RUN] Scheduler | total={total} | due={due_count} | would_skip={skip_count}", flush=True)
    print(f"[DRY-RUN] Scheduler intervals | {interval_text}", flush=True)
    print(f"[DRY-RUN] Scheduler skip reasons | {reasons_text}", flush=True)
    print(f"[DRY-RUN] Scheduler methods | {method_text}", flush=True)
    print(f"[DRY-RUN] Scheduler fail buckets | {fail_bucket_text}", flush=True)
    shown = 0
    for decision in decisions:
        if decision.get("due"):
            continue
        site = decision.get("site") or {}
        print(
            "[DRY-RUN] Would skip: "
            f"{site.get('name') or site.get('url')} | method={decision.get('monitor_method') or 'default'} "
            f"| interval={decision.get('interval_minutes')}m | remaining={decision.get('remaining_minutes')}m "
            f"| reason={decision.get('reason')} | fail={decision.get('fail_count')} | error={decision.get('error_type')}",
            flush=True,
        )
        shown += 1
        if shown >= 10:
            break


def normalize_text(text):
    text = str(text or "").lower()
    text = text.replace("i̇", "i")
    return text


def get_domain(url):
    return urlparse(url or "").netloc.replace("www.", "").lower()


def is_local_only_url(url):
    domain = get_domain(url).split(":")[0]
    return domain in LOCAL_ONLY_DOMAINS



def http_status_reason(status_code):
    try:
        status = int(status_code)
    except Exception:
        return "site_error"
    if status == 403:
        return "http_403"
    if status == 404:
        return "http_404"
    if status == 429:
        return "http_429"
    if status >= 400:
        return f"http_{status}"
    return ""


def classify_fetch_exception(exc):
    if isinstance(exc, requests.Timeout):
        return "timeout"
    if isinstance(exc, requests.exceptions.SSLError):
        return "ssl_failure"
    if isinstance(exc, requests.exceptions.ConnectionError):
        value = str(exc).lower()
        if any(marker in value for marker in ("dns", "name resolution", "getaddrinfo", "nodename", "name or service not known")):
            return "dns_failure"
        return "dns_failure"
    return "site_error"


def normalize_read_method(method):
    method = clean_text(method).lower()
    if method == "xpath":
        return "xpath"
    if method == "rss_discovered":
        return "rss"
    return method


def empty_reason_for_method(method):
    method = normalize_read_method(method)
    if method == "rss":
        return "rss_empty"
    if method == "sitemap":
        return "sitemap_empty"
    if method == "selector":
        return "selector_empty"
    if method in {"xpath", "xpath_pattern"}:
        return "xpath_empty"
    if method == "homepage":
        return "homepage_empty"
    if method == "latest_page":
        return "latest_page_empty"
    if method in {"fallback", "recoverable"}:
        return "fallback_empty"
    if method == "google_news":
        return "rss_empty"
    return "no_article"


def record_method_attempt(site, method):
    method = normalize_read_method(method)
    if not method:
        return
    attempts = site.setdefault("_method_attempted", [])
    if method not in attempts:
        attempts.append(method)


def set_read_diagnostic(site, reason=None, method=None, fallback_used=None):
    if method:
        record_method_attempt(site, method)
        site["_read_method"] = normalize_read_method(method)
    if reason:
        site["_read_failure_reason"] = clean_text(reason)
    if fallback_used is not None:
        site["_fallback_used"] = bool(fallback_used)


def mark_read_success(site, method, fallback_used=False):
    method = normalize_read_method(method)
    site.pop("_read_failure_reason", None)
    site["_method_succeeded"] = method
    set_read_diagnostic(site, None, method, fallback_used)


def get_read_failure_reason(site, default="no_article"):
    return clean_text(site.get("_read_failure_reason") or default)


def merge_bot_diagnostic_notes(existing_notes, reason, method, fallback_used, attempted_methods=None, succeeded_method=None):
    raw_notes = str(existing_notes or "").strip()
    lines = [
        line for line in raw_notes.splitlines()
        if not line.strip().startswith("[bot_diagnostic]")
    ]
    attempted = ",".join(attempted_methods or []) or clean_text(method) or "unknown"
    succeeded = clean_text(succeeded_method) or (clean_text(method) if clean_text(reason) == "sent" else "")
    diagnostic = (
        f"[bot_diagnostic] result={clean_text(reason) or 'unknown'}; "
        f"method_attempted={attempted}; "
        f"method_succeeded={succeeded or 'none'}; "
        f"method={clean_text(method) or 'unknown'}; "
        f"fallback_used={'true' if fallback_used else 'false'}"
    )
    lines.append(diagnostic)
    return "\n".join(line for line in lines if line.strip())

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
        return link.split("#")[0].rstrip("/")

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

    scheme = parsed.scheme.lower()
    hostname = (parsed.hostname or "").lower()
    if hostname.startswith("www."):
        hostname = hostname[4:]
    try:
        port = parsed.port
    except ValueError:
        port = None
    include_port = port and not ((scheme == "http" and port == 80) or (scheme == "https" and port == 443))
    netloc = f"{hostname}:{port}" if include_port else hostname

    # Title is not a safe duplicate key, and URL paths/query values can be
    # case-sensitive. Normalize only safe URL parts; keep meaningful casing.
    path = parsed.path.rstrip("/") if parsed.path != "/" else ""
    query = urlencode(kept_query, doseq=True)
    return urlunparse((scheme, netloc, path, "", query, ""))


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
    read_method = clean_text(result.get("read_method") or site.get("_read_method") or "")
    attempted_methods = site.get("_method_attempted") or []
    succeeded_method = clean_text(site.get("_method_succeeded") or "")
    fallback_used = bool(result.get("fallback_used") or site.get("_fallback_used"))
    now = datetime.now(BAKU_TZ).isoformat()
    payload = {
        "last_checked_at": now,
        "last_result": reason,
        "notes": merge_bot_diagnostic_notes(
            site.get("notes"),
            reason,
            read_method,
            fallback_used,
            attempted_methods=attempted_methods,
            succeeded_method=succeeded_method,
        ),
    }

    hard_fail_reasons = {
        "site_error", "blocked", "dead", "failed",
        "http_403", "http_404", "http_429", "timeout",
        "dns_failure", "ssl_failure", "rss_empty", "invalid_xml",
        "selector_empty", "xpath_empty", "sitemap_empty", "unsafe_url",
    }
    readable_reasons = {
        "sent", "duplicate", "old_news", "future_date", "no_date",
        "date_parse_failed", "no_monitor_match", "no_telegram_recipient",
        "no_keyword_match", "no_article", "latest_page_empty",
        "homepage_empty", "fallback_empty", "forbidden", "chat_not_found",
        "bot_blocked", "bad_request", "network_error", "telegram_429",
        "chat_migrated", "telegram_disabled",
    }

    if reason in hard_fail_reasons:
        try:
            response = requests.post(
                f"{SUPABASE_URL}/rest/v1/rpc/increment_source_fail",
                headers=supabase_headers(),
                json={"p_source_id": source_id, "p_reason": reason},
                timeout=REQUEST_TIMEOUT,
            )
            if response.status_code not in {200, 204}:
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
            print(f"Source health update xetasi: {response.status_code} | {response.text[:200]}", flush=True)
    except Exception as exc:
        print(f"Source health update istisnasi: {exc}", flush=True)

def parse_telegram_response(response):
    status_code = getattr(response, "status_code", 0) or 0
    data = {}
    try:
        data = response.json() or {}
    except Exception:
        data = {}

    parameters = data.get("parameters") or {}
    description = clean_text(data.get("description") or getattr(response, "text", ""))
    description_lower = description.lower()
    try:
        retry_after = int(parameters.get("retry_after") or 0)
    except (TypeError, ValueError):
        retry_after = 0
    result = {
        "ok": status_code == 200,
        "status_code": status_code,
        "reason": "",
        "retry_after": retry_after,
        "migrate_to_chat_id": clean_text(parameters.get("migrate_to_chat_id")),
        "description": description,
    }

    if status_code == 200:
        return result
    if status_code == 429:
        result["reason"] = "telegram_429"
        if result["retry_after"] <= 0:
            result["retry_after"] = 30
        return result
    if result["migrate_to_chat_id"] or "migrate_to_chat_id" in description_lower:
        result["reason"] = "chat_migrated"
        return result
    if status_code == 403:
        if "bot was blocked" in description_lower or "blocked by the user" in description_lower:
            result["reason"] = "bot_blocked"
        else:
            result["reason"] = "forbidden"
        return result
    if status_code == 400:
        if "chat not found" in description_lower:
            result["reason"] = "chat_not_found"
        else:
            result["reason"] = "bad_request"
        return result

    result["reason"] = "bad_request" if 400 <= status_code < 500 else "network_error"
    return result


def send_telegram(message, chat_id=None):
    global TELEGRAM_LAST_ERROR
    TELEGRAM_LAST_ERROR = ""
    target_chat_id = clean_text(chat_id or CHAT_ID)
    if DISABLE_TELEGRAM_SEND:
        TELEGRAM_LAST_ERROR = "telegram_disabled"
        print("Telegram send disabled by DISABLE_TELEGRAM_SEND; message skipped safely.", flush=True)
        return False
    if not BOT_TOKEN or not target_chat_id:
        TELEGRAM_LAST_ERROR = "bad_request"
        print("BOT_TOKEN və ya CHAT_ID yoxdur.", flush=True)
        return False

    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": target_chat_id,
        "text": message,
        "disable_web_page_preview": False,
    }

    for attempt in range(2):
        try:
            with TELEGRAM_LOCK:
                response = requests.post(url, data=payload, timeout=15)
        except Exception as exc:
            TELEGRAM_LAST_ERROR = "network_error"
            print(f"Telegram network_error: chat={target_chat_id} | {exc}", flush=True)
            return False

        parsed = parse_telegram_response(response)
        print(f"Telegram: {parsed['status_code']} | reason={parsed['reason'] or 'sent'}", flush=True)
        if parsed["ok"]:
            TELEGRAM_LAST_ERROR = ""
            return True

        TELEGRAM_LAST_ERROR = parsed["reason"] or "network_error"
        print(f"Telegram cavabi: {parsed['description'][:500]}", flush=True)

        if TELEGRAM_LAST_ERROR == "chat_migrated":
            print(
                f"Telegram chat_migrated: old_chat={target_chat_id} | "
                f"new_chat={parsed.get('migrate_to_chat_id') or 'unknown'}",
                flush=True,
            )

        if TELEGRAM_LAST_ERROR == "telegram_429" and attempt == 0:
            retry_after = max(1, min(parsed.get("retry_after") or 30, 120))
            print(f"Telegram 429 retry_after={retry_after}s | eyni mesaj bir dəfə təkrar göndəriləcək.", flush=True)
            time.sleep(retry_after)
            continue
        return False

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
            print(f"⛔ duplicate_url: {normalized_link}", flush=True)
            return True

        # Title is not a safe duplicate key: recurring announcements and
        # different sources can legitimately publish the same headline.
        # Keep title as metadata for future story grouping, but block only by
        # normalized URL/canonical URL.
        if title:
            print(f"✅ title_duplicate_allowed: {normalize_title_key(title)[:80]} | new_url={normalized_link}", flush=True)
        else:
            print(f"✅ new_url: {normalized_link}", flush=True)
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

    source_name = source.get("name") if isinstance(source, dict) else source
    payload = {"link": normalized_link, "title": title_key or clean_text(title), "source": source_name}
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
            conflict_is_url = exists(normalized_link, title_key)
            conflict_reason = "duplicate_url" if conflict_is_url else "db_dedup_conflict"
            if isinstance(source, dict):
                source["_reserve_failure_reason"] = conflict_reason
            print(
                f"⛔ {conflict_reason}: {normalized_link} | "
                "DB constraint yoxlanmalıdır: title təkbaşına duplicate açarı olmamalıdır.",
                flush=True,
            )
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


def has_any_date_signal(*values):
    for value in values:
        text = clean_text(value)
        if not text:
            continue
        if has_strong_date_signal(text) or contains_known_az_month(text):
            return True
        if re.search(r"\b\d{1,2}\s*[:.]\s*\d{2}\b", text):
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


def get_freshness_status(published_time):
    dt = parse_datetime_to_baku(published_time)
    if not dt:
        return "date_parse_failed"
    now_baku = datetime.now(BAKU_TZ)
    diff = now_baku - dt
    if diff.total_seconds() < 0:
        print(f"Gələcək tarix kimi göründü, keçildi: {published_time}", flush=True)
        return "future_date"
    if diff <= timedelta(hours=NEWS_TIME_LIMIT_HOURS):
        print(f"Tarix uyğundur: {published_time} | fərq: {diff.total_seconds() / 3600:.2f} saat", flush=True)
        return "fresh"
    print(f"Köhnə xəbər keçildi: {published_time} | fərq: {diff.total_seconds() / 3600:.2f} saat", flush=True)
    return "old_news"


def is_recent_news(published_time):
    return get_freshness_status(published_time) == "fresh"


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


def evaluate_publish_freshness(title_text, article_time):
    published_time = choose_publish_time(title_text, article_time)
    if not published_time:
        reason = "date_parse_failed" if has_any_date_signal(title_text, article_time) else "no_date"
        return None, reason
    return published_time, get_freshness_status(published_time)


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
            set_read_diagnostic(site, "unsafe_url", "rss")
            continue
        try:
            response = requests.get(
                rss_url,
                headers=REQUEST_HEADERS,
                timeout=REQUEST_TIMEOUT,
                allow_redirects=True,
            )
            if response.status_code != 200:
                set_read_diagnostic(site, http_status_reason(response.status_code), "rss")
                continue
            feed = feedparser.parse(response.content)
            if getattr(feed, "bozo", False) and not feed.entries:
                set_read_diagnostic(site, "invalid_xml", "rss")
                continue
            if not feed.entries:
                set_read_diagnostic(site, "rss_empty", "rss")
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
                mark_read_success(site, "rss", site.get("_fallback_used", False))
                print(f"RSS uyğun namizəd verdi: {site.get('name')} | {added}", flush=True)
                break
            set_read_diagnostic(site, "no_article", "rss")
        except Exception as exc:
            reason = classify_fetch_exception(exc)
            set_read_diagnostic(site, reason, "rss")
            print(f"RSS oxuma xətası: {rss_url} | {reason} | {exc}", flush=True)
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
    parser = "xml" if looks_like_xml_content(page_html) else "html.parser"
    soup = BeautifulSoup(page_html, parser)
    results = []
    for a in soup.find_all("a", href=True):
        title = clean_text(a.get_text(" ", strip=True))
        link = urljoin(page_url, a["href"])
        if not any(pattern.lower() in link.lower() for pattern in patterns):
            continue
        add_item(results, page_url, title, link, keywords)
    return unique_items(results)[:MAX_LINKS_PER_SITE]


def extract_links_fallback(page_url, page_html, keywords):
    parser = "xml" if looks_like_xml_content(page_html) else "html.parser"
    soup = BeautifulSoup(page_html, parser)
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
            set_read_diagnostic(site, http_status_reason(response.status_code), "sitemap")
            return []
        urls = re.findall(r"<loc>(.*?)</loc>", response.text, flags=re.IGNORECASE)
        if looks_like_xml_content(response.text) and not urls:
            set_read_diagnostic(site, "invalid_xml", "sitemap")
            return []
        if not urls:
            set_read_diagnostic(site, "sitemap_empty", "sitemap")
            return []
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
        reason = classify_fetch_exception(exc)
        set_read_diagnostic(site, reason, "sitemap")
        print(f"Sitemap oxuma xətası: {sitemap_url} | {reason} | {exc}", flush=True)
    results = unique_items(results)[:MAX_LINKS_PER_SITE]
    if results:
        mark_read_success(site, "sitemap", site.get("_fallback_used", False))
    elif not site.get("_read_failure_reason"):
        set_read_diagnostic(site, "sitemap_empty", "sitemap")
    return results

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

    if is_local_only_url(page_url):
        set_read_diagnostic(site, "unsafe_url", monitor_method or "latest_page")
        print(f"Unsafe/local URL keçildi: {page_url}", flush=True)
        return []

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
            items = extract_links_from_rss(site, google_rss_urls)
            if items:
                mark_read_success(site, "google_news")
            return items

        set_read_diagnostic(site, "rss_empty", "google_news")
        return []

    # 2) Blocked/dead/failed:
    # Bu metodlarda əsas sayta girmirik ki, 403/404 spam və vaxt itkisi olmasın.
    if monitor_method in {"blocked", "dead", "failed"}:
        print(f"Metod {monitor_method}: əsas sayt əlavə gəzilmir.", flush=True)
        set_read_diagnostic(site, monitor_method, monitor_method)
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
            mark_read_success(site, "rss")
            return unique_items(items)

        if site.pop("_rss_feed_had_entries", False):
            print("RSS feed oxundu, amma keyword uygun namized tapilmadi. HTML fallback edilmir.", flush=True)
            set_read_diagnostic(site, "no_article", "rss")
            return []

        print("RSS nəticə vermədi, HTML fallback yoxlanacaq.", flush=True)

    # 4) Sitemap:
    # Səndə olan extract_links_from_sitemap(site) funksiyasından istifadə edir.
    if monitor_method == "sitemap":
        items = extract_links_from_sitemap(site)

        if items:
            mark_read_success(site, "sitemap")
            return unique_items(items)

        if not site.get("_read_failure_reason"):
            set_read_diagnostic(site, "sitemap_empty", "sitemap")
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
            set_read_diagnostic(site, http_status_reason(r.status_code), monitor_method or "latest_page")
            return []

        page_html = decode_response_text(r)

    except Exception as e:
        reason = classify_fetch_exception(e)
        set_read_diagnostic(site, reason, monitor_method or "latest_page")
        print(f"Sayt xətası: {page_url} | {reason} | {e}", flush=True)
        return []

    domain = get_domain(page_url)
    site_patterns = patterns_data.get(domain, [])

    # 6) Selector metodu
    if monitor_method == "selector":
        if not selector:
            set_read_diagnostic(site, "selector_empty", "selector")
        else:
            items = extract_links_by_selector(page_url, page_html, selector, keywords)

            if items:
                mark_read_success(site, "selector")
                return unique_items(items)

            print("Selector nəticə vermədi, fallback yoxlanacaq.", flush=True)
            set_read_diagnostic(site, "selector_empty", "selector")

    # 7) XPath metodu
    if monitor_method == "xpath_pattern":
        if not xpaths:
            set_read_diagnostic(site, "xpath_empty", "xpath")
        else:
            items = extract_links_from_xpath(page_url, page_html, xpaths, keywords)

            if items:
                mark_read_success(site, "xpath")
                return unique_items(items)

            print("XPath nəticə vermədi, fallback yoxlanacaq.", flush=True)
            set_read_diagnostic(site, "xpath_empty", "xpath")

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
                site["_fallback_used"] = True
                items = extract_links_from_rss(site, discovered_rss)

                if items:
                    mark_read_success(site, "rss", fallback_used=True)
                    return unique_items(items)

        items = []

        if selector and monitor_method != "selector":
            items = extract_links_by_selector(page_url, page_html, selector, keywords)
            if items:
                mark_read_success(site, "selector", fallback_used=monitor_method not in {"selector", ""})

        if not items and xpaths and monitor_method != "xpath_pattern":
            items = extract_links_from_xpath(page_url, page_html, xpaths, keywords)
            if items:
                mark_read_success(site, "xpath", fallback_used=monitor_method not in {"xpath_pattern", ""})

        if not items and site_patterns:
            print(f"Pattern fallback işləyir: {domain}", flush=True)
            items = extract_links_by_patterns(
                page_url,
                page_html,
                keywords,
                site_patterns,
            )
            if items:
                mark_read_success(site, "fallback", fallback_used=True)

        if not items:
            print("HTML fallback işləyir...", flush=True)
            items = extract_links_fallback(page_url, page_html, keywords)
            if items:
                method = "homepage" if monitor_method == "homepage" else "latest_page"
                if monitor_method in {"recoverable", "selector", "xpath_pattern", "rss", "rss_discovered"}:
                    method = "fallback"
                mark_read_success(site, method, fallback_used=method == "fallback")

        items = unique_items(items)
        if not items:
            final_method = "fallback" if site.get("_fallback_used") else (monitor_method or "latest_page")
            final_reason = empty_reason_for_method(final_method)
            if not site.get("_read_failure_reason") or final_reason in {"homepage_empty", "latest_page_empty", "fallback_empty"}:
                set_read_diagnostic(site, final_reason, final_method)
        return items

    set_read_diagnostic(site, empty_reason_for_method(monitor_method or "latest_page"), monitor_method or "latest_page")
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
                    "last_checked_at": row.get("last_checked_at"),
                    "last_success_at": row.get("last_success_at"),
                    "last_error": row.get("last_error"),
                    "consecutive_fail_count": row.get("consecutive_fail_count"),
                    "last_result": row.get("last_result"),
                    "notes": row.get("notes"),
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
    result = {
        "sent": 0,
        "site": site.get("name"),
        "url": site.get("url"),
        "candidates": 0,
        "reason": "unknown",
        "read_method": "",
        "fallback_used": False,
    }
    print(f"[{index}/{total}] Yoxlanır: {site['name']} | {site['url']}", flush=True)
    try:
        items = fetch_site(site, patterns_data)
    except Exception as exc:
        print(f"❌ [{index}/{total}] {site['name']} | sayt emalı xətası: {exc}", flush=True)
        result["reason"] = "site_error"
        update_source_health(site, result)
        return result

    result["candidates"] = len(items)
    result["read_method"] = clean_text(site.get("_read_method"))
    result["fallback_used"] = bool(site.get("_fallback_used"))
    print(f"[{index}/{total}] {site['name']} | uyğun link sayı: {len(items)} | metod={result['read_method'] or 'unknown'} | fallback={result['fallback_used']}", flush=True)

    if not items:
        result["reason"] = get_read_failure_reason(site, "no_article")
        print(f"📊 [{index}/{total}] {site['name']} | namizəd=0 | göndərildi=0 | nəticə={result['reason']} | vaxt={time.time() - started:.1f}s", flush=True)
        update_source_health(site, result)
        return result

    for item in items[:site.get("limit", MAX_LINKS_PER_SITE)]:
        title = item["title"]
        link = item["link"]
        source = item["source"]
        matched_keywords = item.get("matched_keywords", [])

        if exists(link, title):
            result["reason"] = "duplicate_url"
            continue

        raw_title = item.get("raw_title") or title
        title_time = parse_datetime_to_baku(raw_title)
        rss_time = item.get("rss_published")
        article_time = extract_publish_time_from_article(link) or rss_time
        published_time, freshness_status = evaluate_publish_freshness(raw_title, article_time)

        print(f"[{index}/{total}] Xəbər: {title[:80]} | title_tarix: {title_time} | rss_tarix: {rss_time} | article_tarix: {article_time} | seçilən tarix: {published_time} | freshness={freshness_status} | Link: {link}", flush=True)

        if freshness_status != "fresh":
            result["reason"] = freshness_status
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
        if not reserve_news(link, clean_title, site):
            result["reason"] = site.pop("_reserve_failure_reason", "duplicate_url")
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
        result["reason"] = TELEGRAM_LAST_ERROR or "telegram_error"

    print(f"📊 [{index}/{total}] {site['name']} | namizəd={len(items)} | göndərildi=0 | nəticə={result['reason']} | vaxt={time.time() - started:.1f}s", flush=True)
    update_source_health(site, result)
    return result


def check_sites():
    started = time.time()
    connect_telegram_users_from_updates()
    cleanup_old_monitor_data_if_needed()
    sites = load_sites()
    if SCHEDULER_DRY_RUN:
        scheduler_decisions = [evaluate_source_schedule(site) for site in sites]
        log_scheduler_dry_run_summary(sites, scheduler_decisions)
    patterns_data = load_patterns()
    monitor_keywords_cache = load_active_monitor_keywords()
    total = len(sites)
    print(f"Yüklənən sayt sayı: {total}", flush=True)
    print(f"Monitorinq başladı | worker={MAX_WORKERS} | son {NEWS_TIME_LIMIT_HOURS} saat | {datetime.now(BAKU_TZ).strftime('%d.%m.%Y %H:%M:%S')} AZT", flush=True)

    sent_count = 0
    stats = {
        "sent": 0, "no_article": 0, "duplicate": 0, "duplicate_url": 0,
        "db_dedup_conflict": 0, "no_date": 0, "date_parse_failed": 0,
        "future_date": 0, "old_news": 0,
        "no_monitor_match": 0, "no_telegram_recipient": 0,
        "site_error": 0, "telegram_error": 0, "telegram_429": 0,
        "forbidden": 0, "chat_not_found": 0, "bot_blocked": 0,
        "bad_request": 0, "network_error": 0, "chat_migrated": 0,
        "telegram_disabled": 0,
        "http_403": 0, "http_404": 0,
        "http_429": 0, "timeout": 0, "dns_failure": 0, "ssl_failure": 0,
        "rss_empty": 0, "invalid_xml": 0, "selector_empty": 0,
        "xpath_empty": 0, "sitemap_empty": 0, "homepage_empty": 0,
        "latest_page_empty": 0, "fallback_empty": 0,
        "unsafe_url": 0, "unknown": 0,
    }
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
    print(f"🔎 Məqalə tapılmayan sayt: {stats.get('no_article', 0)}", flush=True)
    print(f"🧭 Oxuma diaqnostikası: http_403={stats.get('http_403', 0)} | http_404={stats.get('http_404', 0)} | http_429={stats.get('http_429', 0)} | timeout={stats.get('timeout', 0)} | dns={stats.get('dns_failure', 0)} | ssl={stats.get('ssl_failure', 0)} | rss_empty={stats.get('rss_empty', 0)} | invalid_xml={stats.get('invalid_xml', 0)} | selector_empty={stats.get('selector_empty', 0)} | xpath_empty={stats.get('xpath_empty', 0)} | sitemap_empty={stats.get('sitemap_empty', 0)} | latest_page_empty={stats.get('latest_page_empty', 0)} | homepage_empty={stats.get('homepage_empty', 0)} | fallback_empty={stats.get('fallback_empty', 0)} | unsafe_url={stats.get('unsafe_url', 0)}", flush=True)
    print(f"🔁 Təkrar keçilən: {stats.get('duplicate', 0) + stats.get('duplicate_url', 0)}", flush=True)
    print(f"🔗 URL dedup: duplicate_url={stats.get('duplicate_url', 0)} | db_conflict={stats.get('db_dedup_conflict', 0)}", flush=True)
    print(f"🕒 Tarix tapılmayan: {stats.get('no_date', 0)}", flush=True)
    print(f"⚠️ Tarix parse alınmayan: {stats.get('date_parse_failed', 0)}", flush=True)
    print(f"🔮 Gələcək tarixli keçilən: {stats.get('future_date', 0)}", flush=True)
    print(f"⏩ Köhnə xəbər: {stats.get('old_news', 0)}", flush=True)
    print(f"🔎 Monitor açar sözünə uyğun olmayan: {stats.get('no_monitor_match', 0)}", flush=True)
    print(f"📭 Telegram alıcısı olmayan: {stats.get('no_telegram_recipient', 0)}", flush=True)
    print(f"❌ Sayt/worker xətası: {stats.get('site_error', 0)}", flush=True)
    print(f"📨 Telegram xətası: {stats.get('telegram_error', 0)}", flush=True)
    print(f"📨 Telegram diaqnostikası: 429={stats.get('telegram_429', 0)} | forbidden={stats.get('forbidden', 0)} | blocked={stats.get('bot_blocked', 0)} | chat_not_found={stats.get('chat_not_found', 0)} | bad_request={stats.get('bad_request', 0)} | network={stats.get('network_error', 0)} | migrated={stats.get('chat_migrated', 0)} | disabled={stats.get('telegram_disabled', 0)}", flush=True)
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
