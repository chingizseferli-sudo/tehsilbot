import argparse
import os
from collections import Counter
from datetime import datetime, timezone

import requests


SUPABASE_URL = os.getenv("SUPABASE_URL", "").strip().rstrip("/")
SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "").strip()
REQUEST_TIMEOUT = int(os.getenv("REQUEST_TIMEOUT", "20"))
TOP_LIMIT = int(os.getenv("KEYWORD_CLEANUP_TOP_LIMIT", "20"))

TARGET_REASONS = {"http_404", "dns_failure", "http_403", "sitemap_empty", "invalid_xml"}
DEACTIVATE_REASONS = {"http_404", "dns_failure"}
MANAGED_PREFIX = "[release_cleanup]"

SOURCE_SELECT = (
    "id,name,base_url,latest_url,rss_url,status,source_type,trust_level,"
    "monitor_method,last_checked_at,last_article_found_at,last_error,last_result,"
    "consecutive_fail_count,notes"
)


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


def clean_text(value):
    return " ".join(str(value or "").split())


def source_label(source):
    return clean_text(source.get("name")) or clean_text(source.get("base_url")) or clean_text(source.get("latest_url")) or source.get("id")


def source_url(source):
    return clean_text(source.get("latest_url")) or clean_text(source.get("base_url")) or clean_text(source.get("rss_url"))


def get_reason(source):
    result = clean_text(source.get("last_result"))
    error = clean_text(source.get("last_error"))
    if error in TARGET_REASONS:
        return error
    if result in TARGET_REASONS:
        return result
    return ""


def fail_count(source):
    try:
        return int(source.get("consecutive_fail_count") or 0)
    except Exception:
        return 0


def fetch_sources():
    rows = []
    offset = 0
    page_size = 1000
    while True:
        response = requests.get(
            f"{SUPABASE_URL}/rest/v1/sources",
            headers=headers(),
            params={
                "select": SOURCE_SELECT,
                "status": "eq.active",
                "limit": str(page_size),
                "offset": str(offset),
                "order": "name.asc",
            },
            timeout=REQUEST_TIMEOUT,
        )
        if response.status_code != 200:
            raise RuntimeError(f"sources SELECT failed: {response.status_code} | {response.text[:500]}")
        page = response.json() or []
        if not page:
            break
        rows.extend(page)
        if len(page) < page_size:
            break
        offset += page_size
    return rows


def classify_source(source):
    reason = get_reason(source)
    method = clean_text(source.get("monitor_method")) or "unknown"
    fails = fail_count(source)

    if reason in DEACTIVATE_REASONS and fails >= 5:
        return {
            "category": "deactivate",
            "action": "set status=inactive",
            "reason": reason,
            "risk": "low",
            "why": "Repeated hard dead-source signal.",
        }
    if reason == "http_403":
        return {
            "category": "accept/monitor",
            "action": "keep active; review fallback/Google News/RSS manually",
            "reason": reason,
            "risk": "medium",
            "why": "Blocked sources may still be high-value and should not be blindly deactivated.",
        }
    if reason == "sitemap_empty":
        action = "review method; sitemap may be wrong or non-news sitemap"
        if method == "sitemap":
            action = "repair method; try RSS/latest_page/selector instead of sitemap"
        return {
            "category": "repair",
            "action": action,
            "reason": reason,
            "risk": "medium",
            "why": "Sitemap produced no article candidates.",
        }
    if reason == "invalid_xml":
        action = "review parser/source method; XML/RSS may be malformed or HTML is being read as XML"
        if method in {"rss", "rss_discovered", "sitemap"}:
            action = "repair method/config; validate feed/sitemap and fallback to latest_page/selector if needed"
        return {
            "category": "repair",
            "action": action,
            "reason": reason,
            "risk": "medium",
            "why": "Parser could not read XML-like content reliably.",
        }
    return {
        "category": "accept/monitor",
        "action": "no automatic change",
        "reason": reason or "unknown",
        "risk": "low",
        "why": "Not in target cleanup set.",
    }


def managed_notes(existing_notes, decision):
    lines = [
        line for line in str(existing_notes or "").splitlines()
        if not line.strip().startswith(MANAGED_PREFIX)
    ]
    stamp = datetime.now(timezone.utc).isoformat()
    lines.append(
        f"{MANAGED_PREFIX} action={decision['category']}; reason={decision['reason']}; "
        f"recommendation={decision['action']}; reviewed_at={stamp}"
    )
    return "\n".join(line for line in lines if line.strip())


def apply_deactivation(source, decision):
    payload = {
        "status": "inactive",
        "notes": managed_notes(source.get("notes"), decision),
    }
    response = requests.patch(
        f"{SUPABASE_URL}/rest/v1/sources",
        headers=headers({"Prefer": "return=minimal"}),
        params={"id": f"eq.{source['id']}"},
        json=payload,
        timeout=REQUEST_TIMEOUT,
    )
    if response.status_code not in {200, 204}:
        raise RuntimeError(f"deactivate failed: {source_label(source)} | {response.status_code} | {response.text[:300]}")


def print_examples(title, rows):
    print(f"\n## {title}")
    if not rows:
        print("- none")
        return
    for source, decision in rows[:TOP_LIMIT]:
        print(
            f"- {source_label(source)} | reason={decision['reason']} | method={source.get('monitor_method') or '-'} "
            f"| fail={fail_count(source)} | action={decision['action']} | url={source_url(source)}"
        )


def main():
    parser = argparse.ArgumentParser(description="Release 1 read-only/dry-run cleanup planner for Keyword Monitor critical sources.")
    parser.add_argument("--apply", action="store_true", help="Apply only explicit safe deactivations for repeated http_404/dns_failure sources.")
    args = parser.parse_args()
    apply_mode = args.apply or os.getenv("KEYWORD_CLEANUP_APPLY", "").strip().lower() in {"1", "true", "yes"}

    print("Keyword Monitor Critical Source Cleanup")
    print(f"Mode: {'APPLY' if apply_mode else 'DRY-RUN'}")
    print("Scope: active sources with http_404, dns_failure, http_403, sitemap_empty, invalid_xml")
    print("Safety: no deletes; no method changes; apply only deactivates repeated http_404/dns_failure.")

    sources = fetch_sources()
    target_rows = []
    for source in sources:
        reason = get_reason(source)
        if reason not in TARGET_REASONS:
            continue
        target_rows.append((source, classify_source(source)))

    by_reason = Counter(decision["reason"] for _source, decision in target_rows)
    by_category = Counter(decision["category"] for _source, decision in target_rows)
    by_method_reason = Counter(
        (source.get("monitor_method") or "unknown", decision["reason"])
        for source, decision in target_rows
    )

    print("\n## Counts")
    print(f"- active_sources_read: {len(sources)}")
    print(f"- target_critical_sources: {len(target_rows)}")
    for key, value in by_category.most_common():
        print(f"- {key}: {value}")

    print("\n## Count by reason")
    for key, value in by_reason.most_common():
        print(f"- {key}: {value}")

    print("\n## Top method/reason clusters")
    for (method, reason), value in by_method_reason.most_common(TOP_LIMIT):
        print(f"- method={method} | reason={reason}: {value}")

    deactivate = [(s, d) for s, d in target_rows if d["category"] == "deactivate"]
    repair = [(s, d) for s, d in target_rows if d["category"] == "repair"]
    accept = [(s, d) for s, d in target_rows if d["category"] == "accept/monitor"]

    print_examples("Deactivate candidates", deactivate)
    print_examples("Repair candidates", repair)
    print_examples("Accept/monitor candidates", accept)

    if not apply_mode:
        print("\nDRY-RUN complete. No Supabase writes performed.")
        print("To apply safe deactivations only, run with --apply or KEYWORD_CLEANUP_APPLY=true.")
        return

    changed = 0
    for source, decision in deactivate:
        apply_deactivation(source, decision)
        changed += 1
        print(f"deactivated: {source_label(source)} | reason={decision['reason']}")
    print(f"\nAPPLY complete. Deactivated sources: {changed}. No deletes performed.")


if __name__ == "__main__":
    main()
