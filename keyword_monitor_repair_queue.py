import argparse
import os
from collections import Counter
from datetime import datetime, timezone
from urllib.parse import urlparse

import requests


SUPABASE_URL = os.getenv("SUPABASE_URL", "").strip().rstrip("/")
SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "").strip()
REQUEST_TIMEOUT = int(os.getenv("REQUEST_TIMEOUT", "20"))
TOP_LIMIT = int(os.getenv("KEYWORD_REPAIR_TOP_LIMIT", "200"))

TARGET_REASONS = {"sitemap_empty", "invalid_xml"}
MANAGED_PREFIX = "[release_repair]"
APPLY_ALLOWED_METHODS = {"selector", "latest_page"}

HIGH_VALUE_DOMAIN_SUFFIXES = (
    ".edu.az",
    ".gov.az",
    ".gov.cz",
)
HIGH_VALUE_DOMAINS = {
    "azertag.az",
    "e-qanun.az",
    "marja.az",
    "news.milli.az",
    "sputnik.az",
    "static.bsu.az",
    "airport.az",
    "mia.az",
}
HIGH_VALUE_SOURCE_TYPES = {"education", "government", "university"}

SOURCE_SELECT = (
    "id,name,base_url,latest_url,rss_url,status,source_type,trust_level,"
    "monitor_method,selector,article_pattern,last_checked_at,last_article_found_at,"
    "last_error,last_result,consecutive_fail_count,notes"
)


def headers(extra=None):
    if not SUPABASE_URL or not SUPABASE_SERVICE_ROLE_KEY:
        raise SystemExit("SUPABASE_URL or SUPABASE_SERVICE_ROLE_KEY is missing.")
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
    return (
        clean_text(source.get("name"))
        or clean_text(source.get("base_url"))
        or clean_text(source.get("latest_url"))
        or source.get("id")
    )


def source_url(source):
    return (
        clean_text(source.get("latest_url"))
        or clean_text(source.get("base_url"))
        or clean_text(source.get("rss_url"))
    )


def source_host(source):
    for value in (source.get("latest_url"), source.get("base_url"), source.get("rss_url")):
        value = clean_text(value)
        if not value:
            continue
        parsed = urlparse(value if "://" in value else f"https://{value}")
        if parsed.hostname:
            return parsed.hostname.lower().removeprefix("www.")
    return ""


def is_high_value_source(source):
    host = source_host(source)
    source_type = clean_text(source.get("source_type")).lower()
    trust_level = clean_text(source.get("trust_level")).lower()
    if source_type in HIGH_VALUE_SOURCE_TYPES:
        return True
    if trust_level == "high":
        return True
    if host in HIGH_VALUE_DOMAINS:
        return True
    return any(host.endswith(suffix) for suffix in HIGH_VALUE_DOMAIN_SUFFIXES)


def get_reason(source):
    result = clean_text(source.get("last_result"))
    error = clean_text(source.get("last_error"))
    if error in TARGET_REASONS:
        return error
    if result in TARGET_REASONS:
        return result
    return ""


def has_selector_config(source):
    return bool(clean_text(source.get("selector")) or clean_text(source.get("article_pattern")))


def has_latest_page_config(source):
    latest_url = clean_text(source.get("latest_url"))
    if not latest_url:
        return False
    lowered = latest_url.lower()
    return not (
        lowered.endswith(".xml")
        or "sitemap" in lowered
        or lowered.endswith(".rss")
        or lowered.endswith("/rss")
    )


def has_homepage_config(source):
    base_url = clean_text(source.get("base_url"))
    if not base_url:
        return False
    lowered = base_url.lower()
    return not (
        lowered.endswith(".xml")
        or "sitemap" in lowered
        or lowered.endswith(".rss")
        or lowered.endswith("/rss")
    )


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
    current_method = clean_text(source.get("monitor_method")) or "unknown"
    high_value = is_high_value_source(source)

    decision = {
        "category": "review_required",
        "reason": reason,
        "current_method": current_method,
        "proposed_method": "",
        "payload": {},
        "risk": "medium",
        "why": "No obvious safe method/config change.",
    }

    if high_value:
        decision.update(
            {
                "category": "review_required",
                "risk": "medium",
                "why": "High-value official/education/major source; do not change method blindly.",
            }
        )
        return decision

    if has_selector_config(source) and current_method not in {"selector", "xpath_pattern"}:
        decision.update(
            {
                "category": "repair_candidate",
                "proposed_method": "selector",
                "payload": {"monitor_method": "selector"},
                "risk": "low",
                "why": "Selector/article pattern already exists; current XML/sitemap path is failing.",
            }
        )
        return decision

    if reason == "sitemap_empty" and current_method == "sitemap":
        if has_latest_page_config(source):
            decision.update(
                {
                    "category": "repair_candidate",
                    "proposed_method": "latest_page",
                    "payload": {"monitor_method": "latest_page"},
                    "risk": "low",
                    "why": "Sitemap returned no candidates; latest_url is available and not sitemap-like.",
                }
            )
            return decision
        if has_homepage_config(source):
            decision.update(
                {
                    "category": "repair_candidate",
                    "proposed_method": "homepage",
                    "payload": {"monitor_method": "homepage"},
                    "risk": "medium",
                    "why": "Sitemap returned no candidates; only homepage-like base_url is available.",
                }
            )
            return decision

    if reason == "invalid_xml" and current_method in {"rss", "rss_discovered", "sitemap"}:
        if has_latest_page_config(source):
            decision.update(
                {
                    "category": "repair_candidate",
                    "proposed_method": "latest_page",
                    "payload": {"monitor_method": "latest_page"},
                    "risk": "low",
                    "why": "XML/RSS parser failed; latest_url is available as safer HTML reading path.",
                }
            )
            return decision
        if has_homepage_config(source):
            decision.update(
                {
                    "category": "repair_candidate",
                    "proposed_method": "homepage",
                    "payload": {"monitor_method": "homepage"},
                    "risk": "medium",
                    "why": "XML/RSS parser failed; only homepage-like base_url is available.",
                }
            )
            return decision

    if reason in TARGET_REASONS:
        decision.update(
            {
                "category": "review_required",
                "risk": "medium",
                "why": "Failure is real, but method change is not obvious from existing config.",
            }
        )
        return decision

    decision.update(
        {
            "category": "accept_monitor",
            "risk": "low",
            "why": "Not in repair queue target reasons.",
        }
    )
    return decision


def managed_notes(existing_notes, decision):
    lines = [
        line for line in str(existing_notes or "").splitlines()
        if not line.strip().startswith(MANAGED_PREFIX)
    ]
    stamp = datetime.now(timezone.utc).isoformat()
    lines.append(
        f"{MANAGED_PREFIX} action={decision['category']}; reason={decision['reason']}; "
        f"current_method={decision['current_method']}; proposed_method={decision['proposed_method'] or '-'}; "
        f"risk={decision['risk']}; reviewed_at={stamp}; note={decision['why']}"
    )
    return "\n".join(line for line in lines if line.strip())


def patch_source(source, decision):
    payload = dict(decision["payload"])
    payload["notes"] = managed_notes(source.get("notes"), decision)
    response = requests.patch(
        f"{SUPABASE_URL}/rest/v1/sources",
        headers=headers({"Prefer": "return=minimal"}),
        params={"id": f"eq.{source['id']}"},
        json=payload,
        timeout=REQUEST_TIMEOUT,
    )
    if response.status_code not in {200, 204}:
        raise RuntimeError(f"repair patch failed: {source_label(source)} | {response.status_code} | {response.text[:300]}")


def print_rows(title, rows):
    print(f"\n## {title}")
    if not rows:
        print("- none")
        return
    for source, decision in rows[:TOP_LIMIT]:
        print(
            f"- id={source.get('id')} | name={source_label(source)} | domain={source_host(source)} "
            f"| type={source.get('source_type') or '-'} | method={decision['current_method']} "
            f"| result={source.get('last_result') or '-'} | error={source.get('last_error') or '-'} "
            f"| proposed={decision['proposed_method'] or '-'} | risk={decision['risk']} "
            f"| reason={decision['reason']} | why={decision['why']} | url={source_url(source)}"
        )


def main():
    parser = argparse.ArgumentParser(description="Release 1 dry-run/apply repair queue for sitemap_empty and invalid_xml sources.")
    parser.add_argument("--apply", action="store_true", help="Apply only obvious safe method changes and preserve release repair notes.")
    args = parser.parse_args()
    apply_mode = args.apply or os.getenv("KEYWORD_REPAIR_APPLY", "").strip().lower() in {"1", "true", "yes"}

    print("Keyword Monitor Sitemap/XML Repair Queue")
    print(f"Mode: {'APPLY' if apply_mode else 'DRY-RUN'}")
    print("Scope: active sources with sitemap_empty or invalid_xml")
    print("Safety: no deletes; no deactivation; no Telegram changes; apply is limited to selector/latest_page.")

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
        (decision["current_method"], decision["reason"])
        for _source, decision in target_rows
    )
    by_proposed = Counter(decision["proposed_method"] or "review_only" for _source, decision in target_rows)

    print("\n## Counts")
    print(f"- active_sources_read: {len(sources)}")
    print(f"- repair_queue_targets: {len(target_rows)}")
    for key, value in by_category.most_common():
        print(f"- {key}: {value}")

    print("\n## Count by reason")
    for key, value in by_reason.most_common():
        print(f"- {key}: {value}")

    print("\n## Proposed methods")
    for key, value in by_proposed.most_common():
        print(f"- {key}: {value}")

    print("\n## Top method/reason clusters")
    for (method, reason), value in by_method_reason.most_common(TOP_LIMIT):
        print(f"- method={method} | reason={reason}: {value}")

    repair = [(s, d) for s, d in target_rows if d["category"] == "repair_candidate"]
    apply_eligible = [(s, d) for s, d in repair if d["proposed_method"] in APPLY_ALLOWED_METHODS]
    apply_skipped = [(s, d) for s, d in repair if d["proposed_method"] not in APPLY_ALLOWED_METHODS]
    review = [(s, d) for s, d in target_rows if d["category"] == "review_required"]
    accept = [(s, d) for s, d in target_rows if d["category"] == "accept_monitor"]

    print(f"\n## Apply eligibility")
    print(f"- apply_eligible_selector_latest_page: {len(apply_eligible)}")
    print(f"- skipped_homepage_or_other: {len(apply_skipped)}")

    print_rows("Repair candidates", repair)
    print_rows("Apply skipped", apply_skipped)
    print_rows("Review required", review)
    print_rows("Accept/monitor", accept)

    if not apply_mode:
        print("\nDRY-RUN complete. No Supabase writes performed.")
        print("To apply obvious safe method changes only, run with --apply or KEYWORD_REPAIR_APPLY=true.")
        return

    changed = 0
    for source, decision in apply_eligible:
        patch_source(source, decision)
        changed += 1
        print(
            f"repaired: {source_label(source)} | {decision['current_method']} -> "
            f"{decision['proposed_method']} | reason={decision['reason']}"
        )
    print(f"\nAPPLY complete. Repaired sources: {changed}. Skipped homepage/other proposals: {len(apply_skipped)}. No deletes or deactivations performed.")


if __name__ == "__main__":
    main()
