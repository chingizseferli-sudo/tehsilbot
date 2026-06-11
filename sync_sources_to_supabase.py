import json
import os
from datetime import datetime
from zoneinfo import ZoneInfo
from urllib.parse import urlparse

import requests

SUPABASE_URL = os.getenv("SUPABASE_URL", "").strip().rstrip("/")
SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "").strip()

BAKU_TZ = ZoneInfo("Asia/Baku")

FILES = [
    "courier_config_clean.json",
    "discovered_sites.json",
    "review_sites.json",
]

REQUEST_TIMEOUT = 15


def headers(extra=None):
    h = {
        "apikey": SUPABASE_SERVICE_ROLE_KEY,
        "Authorization": f"Bearer {SUPABASE_SERVICE_ROLE_KEY}",
        "Content-Type": "application/json",
    }
    if extra:
        h.update(extra)
    return h


def clean_text(value):
    return str(value or "").strip()


def clean_domain(url):
    try:
        domain = urlparse(url).netloc.lower().strip()
        if domain.startswith("www."):
            domain = domain[4:]
        return domain
    except Exception:
        return ""


def base_url(url):
    parsed = urlparse(url)
    if not parsed.scheme or not parsed.netloc:
        return ""
    return f"{parsed.scheme}://{parsed.netloc}".rstrip("/")


def read_sites(filename):
    try:
        with open(filename, "r", encoding="utf-8") as f:
            data = json.load(f)

        if isinstance(data, dict):
            return data.get("sites", [])

        if isinstance(data, list):
            return data

        return []
    except FileNotFoundError:
        print(f"Fayl tapılmadı: {filename}")
        return []
    except Exception as e:
        print(f"JSON oxunmadı: {filename} | {e}")
        return []


def detect_monitor_method(site):
    if site.get("rss_url"):
        return "rss"
    if site.get("selector"):
        return "selector"
    if site.get("xpaths"):
        return "xpath"
    return site.get("monitor_method") or "html"


def build_payload(site, source_file):
    url = clean_text(site.get("url") or site.get("base_url") or site.get("latest_url"))
    if not url:
        return None

    if not url.startswith("http"):
        url = "https://" + url

    root = base_url(url)
    if not root:
        return None

    score = int(site.get("score") or site.get("discovery_score") or 50)

    return {
        "name": clean_text(site.get("name")) or clean_domain(url),
        "base_url": root,
        "latest_url": clean_text(site.get("latest_url")) or url,
        "rss_url": clean_text(site.get("rss_url")) or None,
        "source_type": site.get("source_type") or "news_site",
        "status": site.get("status") if site.get("status") in ["active", "inactive"] else "active",
        "trust_level": site.get("trust_level") or ("high" if score >= 80 else "medium"),
        "monitor_method": detect_monitor_method(site),
        "selector": site.get("selector"),
        "article_pattern": ",".join(site.get("xpaths", [])[:3]) if site.get("xpaths") else site.get("article_pattern"),
        "discovery_status": site.get("discovery_status") or "accepted",
        "discovery_score": score,
        "last_discovered_at": datetime.now(BAKU_TZ).isoformat(),
        "notes": f"imported_from={source_file}",
    }


def upsert_source(payload):
    response = requests.post(
        f"{SUPABASE_URL}/rest/v1/sources",
        headers=headers({"Prefer": "resolution=merge-duplicates,return=minimal"}),
        params={"on_conflict": "base_url"},
        json=payload,
        timeout=REQUEST_TIMEOUT,
    )

    if response.status_code in (200, 201, 204):
        return True

    print(
        f"Supabase xətası: {response.status_code} | {payload.get('base_url')} | {response.text[:300]}"
    )
    return False


def main():
    if not SUPABASE_URL or not SUPABASE_SERVICE_ROLE_KEY:
        raise RuntimeError("SUPABASE_URL və SUPABASE_SERVICE_ROLE_KEY yoxdur")

    all_sites = []
    seen_domains = set()

    for filename in FILES:
        sites = read_sites(filename)

        for site in sites:
            payload = build_payload(site, filename)
            if not payload:
                continue

            domain = clean_domain(payload["base_url"])
            if not domain:
                continue

            if domain in seen_domains:
                continue

            seen_domains.add(domain)
            all_sites.append(payload)

    print(f"Toplanan unikal mənbə sayı: {len(all_sites)}")

    added = 0
    failed = 0

    for payload in all_sites:
        if upsert_source(payload):
            added += 1
            print(f"✅ Yazıldı: {payload['name']} | {payload['base_url']}")
        else:
            failed += 1

    print("=" * 50)
    print(f"✅ Supabase yazılan: {added}")
    print(f"❌ Xəta: {failed}")
    print("=" * 50)


if __name__ == "__main__":
    main()
