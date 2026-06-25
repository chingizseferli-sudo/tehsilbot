import os
from collections import Counter
from datetime import datetime, timedelta, timezone

import requests


SUPABASE_URL = os.getenv("SUPABASE_URL", "").strip().rstrip("/")
SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "").strip()
REQUEST_TIMEOUT = int(os.getenv("REQUEST_TIMEOUT", "20"))
RECENT_DAYS = int(os.getenv("HEALTH_REPORT_RECENT_DAYS", "7"))
STALE_HOURS = int(os.getenv("HEALTH_REPORT_STALE_HOURS", "24"))
NO_ARTICLE_DAYS = int(os.getenv("HEALTH_REPORT_NO_ARTICLE_DAYS", "7"))
TOP_LIMIT = int(os.getenv("HEALTH_REPORT_TOP_LIMIT", "15"))


SOURCE_FIELDS = (
    "id,name,base_url,latest_url,rss_url,status,source_type,monitor_method,"
    "last_checked_at,last_success_at,last_article_found_at,last_error,last_result,"
    "consecutive_fail_count,notes"
)


READING_FAILURES = {
    "rss_empty",
    "invalid_xml",
    "selector_empty",
    "xpath_empty",
    "sitemap_empty",
    "homepage_empty",
    "latest_page_empty",
    "fallback_empty",
    "http_403",
    "http_404",
    "http_429",
    "timeout",
    "dns_failure",
    "ssl_failure",
    "unsafe_url",
    "site_error",
}

DATE_FAILURES = {"old_news", "no_date", "date_parse_failed", "future_date"}
DEDUP_FAILURES = {"duplicate_url", "db_dedup_conflict", "duplicate"}
TELEGRAM_FAILURES = {
    "telegram_error",
    "telegram_429",
    "forbidden",
    "chat_not_found",
    "bot_blocked",
    "bad_request",
    "network_error",
    "chat_migrated",
}


def headers():
    if not SUPABASE_URL or not SUPABASE_SERVICE_ROLE_KEY:
        raise SystemExit("SUPABASE_URL və ya SUPABASE_SERVICE_ROLE_KEY yoxdur.")
    return {
        "apikey": SUPABASE_SERVICE_ROLE_KEY,
        "Authorization": f"Bearer {SUPABASE_SERVICE_ROLE_KEY}",
        "Content-Type": "application/json",
    }


def parse_dt(value):
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        parsed = datetime.fromisoformat(text)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except Exception:
        return None


def supabase_get(table, params):
    response = requests.get(
        f"{SUPABASE_URL}/rest/v1/{table}",
        headers=headers(),
        params=params,
        timeout=REQUEST_TIMEOUT,
    )
    if response.status_code != 200:
        raise RuntimeError(f"Supabase SELECT {table} failed: {response.status_code} | {response.text[:500]}")
    return response.json() or []


def fetch_all(table, select, extra_params=None, page_size=1000):
    rows = []
    offset = 0
    while True:
        params = {
            "select": select,
            "limit": str(page_size),
            "offset": str(offset),
        }
        if extra_params:
            params.update(extra_params)
        page = supabase_get(table, params)
        if not page:
            break
        rows.extend(page)
        if len(page) < page_size:
            break
        offset += page_size
    return rows


def safe_fetch_sources():
    try:
        return fetch_all("sources", SOURCE_FIELDS, {"order": "name.asc"})
    except RuntimeError as exc:
        print(f"Health field SELECT alınmadı, minimal sources oxunur: {exc}")
        return fetch_all(
            "sources",
            "id,name,base_url,latest_url,rss_url,status,source_type,monitor_method,notes",
            {"order": "name.asc"},
        )


def safe_fetch_alerts():
    since = (datetime.now(timezone.utc) - timedelta(days=RECENT_DAYS)).isoformat()
    variants = [
        ("id,status,sent_at,created_at,error", {"or": f"(sent_at.gte.{since},created_at.gte.{since})"}),
        ("id,status,sent_at,created_at", {"or": f"(sent_at.gte.{since},created_at.gte.{since})"}),
        ("id,status,sent_at", {"sent_at": f"gte.{since}"}),
    ]
    for select, params in variants:
        try:
            return fetch_all("monitor_alerts", select, params)
        except RuntimeError as exc:
            last_error = exc
    print(f"monitor_alerts oxunmadı: {last_error}")
    return []


def count_by(rows, field):
    return Counter(str(row.get(field) or "unknown") for row in rows)


def notes_has_fallback(row):
    return "fallback_used=true" in str(row.get("notes") or "").lower()


def notes_method(row, marker):
    notes = str(row.get("notes") or "")
    for part in notes.split(";"):
        part = part.strip()
        if part.startswith(marker + "="):
            return part.split("=", 1)[1].strip() or "unknown"
    return "unknown"


def print_counter(title, counter, limit=TOP_LIMIT):
    print(f"\n## {title}")
    if not counter:
        print("- none")
        return
    for key, value in counter.most_common(limit):
        print(f"- {key}: {value}")


def print_sources(title, rows, limit=TOP_LIMIT):
    print(f"\n## {title}")
    if not rows:
        print("- none")
        return
    for row in rows[:limit]:
        name = row.get("name") or row.get("base_url") or row.get("latest_url") or row.get("id")
        method = row.get("monitor_method") or "unknown"
        result = row.get("last_result") or "-"
        error = row.get("last_error") or "-"
        checked = row.get("last_checked_at") or "-"
        article = row.get("last_article_found_at") or "-"
        fail = row.get("consecutive_fail_count") or 0
        print(f"- {name} | method={method} | result={result} | error={error} | fail={fail} | checked={checked} | article={article}")


def severity_groups(sources):
    now = datetime.now(timezone.utc)
    stale_cutoff = now - timedelta(hours=STALE_HOURS)
    no_article_cutoff = now - timedelta(days=NO_ARTICLE_DAYS)
    critical = []
    warning = []
    attention = []

    for source in sources:
        result = str(source.get("last_result") or "")
        error = str(source.get("last_error") or "")
        fail_count = int(source.get("consecutive_fail_count") or 0)
        checked_at = parse_dt(source.get("last_checked_at"))
        article_at = parse_dt(source.get("last_article_found_at"))

        if fail_count >= 5 or error in {"http_403", "http_429", "timeout", "dns_failure", "ssl_failure", "unsafe_url"}:
            critical.append(source)
            continue
        if result in READING_FAILURES or error in READING_FAILURES or fail_count >= 3:
            warning.append(source)
            continue
        if not checked_at or checked_at < stale_cutoff:
            attention.append(source)
            continue
        if not article_at or article_at < no_article_cutoff:
            attention.append(source)

    return critical, warning, attention


def main():
    print("Visual Monitor — Keyword Monitor Health Report")
    print("Mode: READ ONLY")
    print(f"Window: recent_days={RECENT_DAYS}, stale_hours={STALE_HOURS}, no_article_days={NO_ARTICLE_DAYS}")

    sources = safe_fetch_sources()
    alerts = safe_fetch_alerts()
    active_sources = [row for row in sources if str(row.get("status") or "").lower() == "active"]
    critical, warning, attention = severity_groups(active_sources)

    print("\n## Summary")
    print(f"- sources_total: {len(sources)}")
    print(f"- sources_active: {len(active_sources)}")
    print(f"- critical: {len(critical)}")
    print(f"- warning: {len(warning)}")
    print(f"- attention: {len(attention)}")
    print(f"- recent_alert_rows: {len(alerts)}")

    print_counter("last_result distribution", count_by(active_sources, "last_result"))
    print_counter("last_error distribution", count_by(active_sources, "last_error"))
    print_counter("monitor_method distribution", count_by(active_sources, "monitor_method"))

    fail_buckets = Counter()
    for row in active_sources:
        fail = int(row.get("consecutive_fail_count") or 0)
        if fail == 0:
            bucket = "0"
        elif fail <= 2:
            bucket = "1-2"
        elif fail <= 4:
            bucket = "3-4"
        else:
            bucket = "5+"
        fail_buckets[bucket] += 1
    print_counter("consecutive_fail_count distribution", fail_buckets)

    print_counter("date/freshness skips", Counter(row.get("last_result") for row in active_sources if row.get("last_result") in DATE_FAILURES))
    print_counter("dedup skips", Counter(row.get("last_result") for row in active_sources if row.get("last_result") in DEDUP_FAILURES))
    print_counter("telegram delivery failures", Counter(row.get("last_result") for row in active_sources if row.get("last_result") in TELEGRAM_FAILURES))
    print_counter("recent monitor_alerts status", count_by(alerts, "status"))

    attempted_counter = Counter(notes_method(row, "method_attempted") for row in active_sources)
    succeeded_counter = Counter(notes_method(row, "method_succeeded") for row in active_sources)
    print_counter("top attempted reading methods", attempted_counter)
    print_counter("top succeeded reading methods", succeeded_counter)

    fallback_sources = [row for row in active_sources if notes_has_fallback(row)]
    blocked_sources = [
        row for row in active_sources
        if row.get("last_result") in {"http_403", "blocked"} or row.get("last_error") in {"http_403", "blocked"}
    ]

    now = datetime.now(timezone.utc)
    stale_cutoff = now - timedelta(hours=STALE_HOURS)
    no_article_cutoff = now - timedelta(days=NO_ARTICLE_DAYS)
    not_checked_recently = [
        row for row in active_sources
        if not parse_dt(row.get("last_checked_at")) or parse_dt(row.get("last_checked_at")) < stale_cutoff
    ]
    no_article_recently = [
        row for row in active_sources
        if not parse_dt(row.get("last_article_found_at")) or parse_dt(row.get("last_article_found_at")) < no_article_cutoff
    ]

    print_sources("critical sources", critical)
    print_sources("warning sources", warning)
    print_sources("sources not checked recently", not_checked_recently)
    print_sources("sources with no article found recently", no_article_recently)
    print_sources("sources using fallback", fallback_sources)
    print_sources("blocked/403 sources", blocked_sources)

    print("\n## Release operator checklist")
    print("- Critical count should be reviewed before release.")
    print("- blocked/403 sources should have fallback or be accepted as known limitations.")
    print("- Telegram delivery failures should be zero or explained.")
    print("- no_date/date_parse_failed should be reviewed if they are rising.")
    print("- duplicate_url is expected; db_dedup_conflict is a schema risk and should be zero.")
    print("- Scheduler real skipping is intentionally deferred unless request volume becomes unsafe.")


if __name__ == "__main__":
    main()
