import argparse
import os
from datetime import datetime, timezone
from urllib.parse import urlparse

import requests

SUPABASE_URL = os.getenv("SUPABASE_URL", "").strip().rstrip("/")
SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "").strip()
REQUEST_TIMEOUT = int(os.getenv("REQUEST_TIMEOUT", "20"))
SOURCE_LIMIT = int(os.getenv("SOURCE_SYNC_SOURCE_LIMIT", "5000"))
SENT_LIMIT = int(os.getenv("SOURCE_SYNC_SENT_LIMIT", "10000"))

SOURCE_FIELDS = (
    "id,name,base_url,latest_url,rss_url,status,last_checked_at,last_success_at,"
    "last_article_found_at,last_error,last_result,consecutive_fail_count,notes"
)

CURRENT_READ_FAILURE_RESULTS = {
    "fallback_empty",
    "rss_empty",
    "invalid_xml",
    "selector_empty",
    "xpath_empty",
    "sitemap_empty",
    "homepage_empty",
    "latest_page_empty",
    "no_candidate",
    "no_article",
    "repair_failed",
    "fetch_failed",
    "source_review_required",
    "http_403",
    "http_404",
    "http_429",
    "timeout",
    "dns_failure",
    "ssl_failure",
    "unsafe_url",
    "site_error",
}


def headers(extra=None):
    if not SUPABASE_URL or not SUPABASE_SERVICE_ROLE_KEY:
        raise SystemExit("SUPABASE_URL və ya SUPABASE_SERVICE_ROLE_KEY yoxdur.")
    value = {
        "apikey": SUPABASE_SERVICE_ROLE_KEY,
        "Authorization": f"Bearer {SUPABASE_SERVICE_ROLE_KEY}",
        "Content-Type": "application/json",
    }
    if extra:
        value.update(extra)
    return value


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


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def domain_from_url(value):
    text = str(value or "").strip()
    if not text:
        return ""
    if "://" not in text:
        text = "https://" + text
    parsed = urlparse(text)
    host = (parsed.netloc or "").lower().split("@")[(-1)].split(":")[0]
    if host.startswith("www."):
        host = host[4:]
    return host


def fetch_sources():
    response = requests.get(
        f"{SUPABASE_URL}/rest/v1/sources",
        headers=headers(),
        params={"select": SOURCE_FIELDS, "limit": str(SOURCE_LIMIT)},
        timeout=REQUEST_TIMEOUT,
    )
    if response.status_code != 200:
        raise SystemExit(f"sources oxunmadı: {response.status_code} | {response.text[:300]}")
    return response.json() or []


def fetch_sent_news():
    variants = [
        "link,title,source,created_at",
        "link,title,source,inserted_at",
        "link,title,source",
    ]
    last_error = ""
    for fields in variants:
        params = {"select": fields, "limit": str(SENT_LIMIT)}
        if "created_at" in fields:
            params["order"] = "created_at.desc"
        elif "inserted_at" in fields:
            params["order"] = "inserted_at.desc"
        response = requests.get(
            f"{SUPABASE_URL}/rest/v1/sent_news",
            headers=headers(),
            params=params,
            timeout=REQUEST_TIMEOUT,
        )
        if response.status_code == 200:
            return response.json() or [], fields
        last_error = f"{response.status_code} | {response.text[:300]}"
    raise SystemExit(f"sent_news oxunmadı: {last_error}")


def build_domain_index(sources):
    index = {}
    for source in sources:
        for field in ("base_url", "latest_url", "rss_url"):
            domain = domain_from_url(source.get(field))
            if domain:
                index.setdefault(domain, []).append(source)
    return index


def sent_time(row):
    return parse_dt(row.get("created_at") or row.get("inserted_at"))


def managed_notes(existing, sent_at):
    lines = [
        line for line in str(existing or "").splitlines()
        if not line.strip().startswith("[sent_news_sync]")
    ]
    lines.append(f"[sent_news_sync] Telegram sent_news sübutu ilə sağlam təsdiqləndi; last_sent_at={sent_at}")
    return "\n".join(line for line in lines if line.strip())


def should_skip_due_newer_failure(source, sent_at):
    result = str(source.get("last_result") or "")
    if result not in CURRENT_READ_FAILURE_RESULTS:
        return False
    checked_at = parse_dt(source.get("last_checked_at"))
    if checked_at and sent_at and checked_at > sent_at:
        return True
    return False


def patch_source(source_id, payload):
    response = requests.patch(
        f"{SUPABASE_URL}/rest/v1/sources",
        headers=headers({"Prefer": "return=minimal"}),
        params={"id": f"eq.{source_id}"},
        json=payload,
        timeout=REQUEST_TIMEOUT,
    )
    if response.status_code not in (200, 204):
        raise RuntimeError(f"update failed: {source_id} | {response.status_code} | {response.text[:300]}")


def main():
    parser = argparse.ArgumentParser(description="Sync healthy source state from Telegram sent_news evidence.")
    parser.add_argument("--apply", action="store_true", help="Write updates to Supabase")
    args = parser.parse_args()

    sources = fetch_sources()
    sent_rows, sent_fields = fetch_sent_news()
    index = build_domain_index(sources)

    latest_by_source = {}
    skipped_newer_failure = []
    unmatched = 0

    for row in sent_rows:
        domain = domain_from_url(row.get("link"))
        if not domain:
            unmatched += 1
            continue
        candidates = index.get(domain, [])
        if not candidates:
            unmatched += 1
            continue
        sent_at = sent_time(row) or datetime.now(timezone.utc)
        for source in candidates:
            if should_skip_due_newer_failure(source, sent_at):
                skipped_newer_failure.append((source, row, sent_at))
                continue
            current = latest_by_source.get(source["id"])
            if not current or sent_at > current["sent_at"]:
                latest_by_source[source["id"]] = {"source": source, "row": row, "sent_at": sent_at}

    updates = []
    for item in latest_by_source.values():
        source = item["source"]
        sent_at_iso = item["sent_at"].isoformat()
        if (
            source.get("last_result") == "sent"
            and not source.get("last_error")
            and parse_dt(source.get("last_article_found_at"))
        ):
            continue
        payload = {
            "status": "active",
            "last_result": "sent",
            "last_error": None,
            "last_success_at": sent_at_iso,
            "last_article_found_at": sent_at_iso,
            "consecutive_fail_count": 0,
            "notes": managed_notes(source.get("notes"), sent_at_iso),
        }
        updates.append((source, item["row"], payload))

    print("Telegram sent_news sağlamlıq sinxronu")
    print(f"Mode: {'APPLY' if args.apply else 'DRY-RUN'}")
    print(f"sources: {len(sources)} | sent_news: {len(sent_rows)} | sent_fields: {sent_fields}")
    print(f"matched sources to update: {len(updates)}")
    print(f"skipped newer read failure: {len(skipped_newer_failure)} | unmatched sent rows: {unmatched}")

    for source, row, payload in updates[:40]:
        print(
            f"- {source.get('name') or source.get('base_url')} | {source.get('base_url')} "
            f"| {source.get('last_result') or '-'} -> sent | sent_at={payload['last_article_found_at']} "
            f"| sample={str(row.get('title') or row.get('link'))[:90]}"
        )

    if len(updates) > 40:
        print(f"... daha {len(updates) - 40} mənbə")

    if args.apply:
        applied = 0
        for source, _, payload in updates:
            patch_source(source["id"], payload)
            applied += 1
        print(f"Applied: {applied}")
    else:
        print("No writes. Apply üçün: python sync_healthy_sources_from_sent_news.py --apply")


if __name__ == "__main__":
    main()
