import json
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

import test_sources_readability as readability


PROBLEM_METHODS = {
    "blocked",
    "dead",
    "failed",
    "recoverable",
    "homepage",
}

PROBLEM_DISCOVERY_STATUSES = {
    "blocked",
    "dead",
    "failed",
    "manual_needed",
    "needs_manual_selector",
    "needs_review",
    "pending",
    "recoverable",
}

RSS_METHODS = {
    "rss",
    "rss_discovered",
}

SELECTOR_METHODS = {
    "selector",
    "xpath_pattern",
}


def clean_text(value):
    return " ".join(str(value or "").split()).strip()


def is_problem_source(source):
    method = clean_text(source.get("monitor_method")).lower()
    discovery_status = clean_text(source.get("discovery_status")).lower()
    rss_url = clean_text(source.get("rss_url"))
    selector = clean_text(source.get("selector"))
    article_pattern = clean_text(source.get("article_pattern"))
    score = int(source.get("discovery_score") or 0)

    if method in PROBLEM_METHODS:
        return True

    if discovery_status in PROBLEM_DISCOVERY_STATUSES:
        return True

    if method in RSS_METHODS and not rss_url:
        return True

    if method in SELECTOR_METHODS and not selector and not article_pattern:
        return True

    if score and score < 40:
        return True

    return False


def filter_sources(sources):
    scope = os.getenv("AUTO_CONFIG_SCOPE", "problems").strip().lower()
    only_methods = {
        item.strip().lower()
        for item in os.getenv("AUTO_CONFIG_METHODS", "").split(",")
        if item.strip()
    }
    limit = int(os.getenv("AUTO_CONFIG_LIMIT", "0") or 0)

    if scope == "all":
        selected = list(sources)
    else:
        selected = [source for source in sources if is_problem_source(source)]

    if only_methods:
        selected = [
            source
            for source in selected
            if clean_text(source.get("monitor_method")).lower() in only_methods
        ]

    if limit > 0:
        selected = selected[:limit]

    return selected


def configure_source(source):
    before = {
        "method": source.get("monitor_method"),
        "rss_url": source.get("rss_url"),
        "latest_url": source.get("latest_url"),
        "selector": source.get("selector"),
        "article_pattern": source.get("article_pattern"),
        "score": source.get("discovery_score"),
    }

    result = readability.analyze_source(source)

    return {
        "id": source.get("id"),
        "name": source.get("name"),
        "base_url": source.get("base_url"),
        "before": before,
        "after": result,
    }


def main():
    if not readability.SUPABASE_URL or not readability.SUPABASE_SERVICE_ROLE_KEY:
        raise RuntimeError("SUPABASE_URL və SUPABASE_SERVICE_ROLE_KEY env dəyərləri lazımdır.")

    sources = readability.fetch_sources()
    selected = filter_sources(sources)
    max_workers = int(os.getenv("AUTO_CONFIG_WORKERS", os.getenv("MAX_WORKERS", "8")) or 8)

    print("🚀 Auto Configure Sources başladı", flush=True)
    print(f"Ümumi aktiv mənbə: {len(sources)}", flush=True)
    print(f"Yoxlanacaq mənbə: {len(selected)}", flush=True)
    print(f"Worker sayı: {max_workers}", flush=True)

    stats = {}
    results = []

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(configure_source, source): source
            for source in selected
        }

        for future in as_completed(futures):
            source = futures[future]
            try:
                row = future.result()
                result = row["after"]
            except Exception as exc:
                row = {
                    "id": source.get("id"),
                    "name": source.get("name"),
                    "base_url": source.get("base_url"),
                    "error": str(exc),
                    "after": {
                        "method": "failed",
                        "status": "needs_review",
                        "ok": False,
                        "note": str(exc),
                    },
                }
                result = row["after"]

            method = result.get("method", "failed")
            stats[method] = stats.get(method, 0) + 1
            results.append(row)

            icon = "✅" if result.get("ok") else "⚠️"
            print(
                f"{icon} {row.get('name')} | {method} | {result.get('status')} | {result.get('note')}",
                flush=True,
            )

    report = {
        "created_at": datetime.utcnow().isoformat(),
        "total_sources": len(sources),
        "selected_sources": len(selected),
        "stats": stats,
        "results": results,
    }

    with open("auto_configure_sources_report.json", "w", encoding="utf-8") as file:
        json.dump(report, file, ensure_ascii=False, indent=2)

    print("=" * 60, flush=True)
    print("📊 AUTO CONFIGURE YEKUNU", flush=True)
    for method, count in sorted(stats.items(), key=lambda item: item[1], reverse=True):
        print(f"{method}: {count}", flush=True)
    print("✅ auto_configure_sources_report.json yaradıldı", flush=True)


if __name__ == "__main__":
    main()
