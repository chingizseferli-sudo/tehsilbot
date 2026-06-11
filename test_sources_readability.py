import os
import re
import json
import time
from datetime import datetime
from urllib.parse import urljoin, urlparse
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
import feedparser
from bs4 import BeautifulSoup

SUPABASE_URL = os.getenv("SUPABASE_URL", "").strip().rstrip("/")
SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "").strip()

REQUEST_TIMEOUT = int(os.getenv("REQUEST_TIMEOUT", "12"))
MAX_WORKERS = int(os.getenv("MAX_WORKERS", "10"))
LIMIT_SOURCES = int(os.getenv("LIMIT_SOURCES", "0"))

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; VisualMonitorBot/1.0)",
    "Accept-Language": "az-AZ,az;q=0.9,en-US;q=0.8",
}

COMMON_LATEST_PATHS = [
    "/rss",
    "/rss.xml",
    "/feed",
    "/feed.xml",
    "/az/rss",
    "/az/rss.xml",
    "/news/rss",
    "/xeberler/rss",
    "/xeber/rss",
    "/sitemap.xml",
    "/news",
    "/xeberler",
    "/xeber",
    "/az/news",
    "/az/xeberler",
    "/az/xeber",
    "/latest",
    "/lastnews",
    "/son-xeberler",
    "/gundem",
    "/cemiyyet",
    "/tehsil",
    "/elm",
]


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
    return bool(SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY)


def clean_text(value):
    return re.sub(r"\s+", " ", str(value or "")).strip()


def get_base(url):
    parsed = urlparse(url)
    return f"{parsed.scheme}://{parsed.netloc}"


def get_domain(url):
    return urlparse(url).netloc.replace("www.", "").lower()


def fetch_sources():
    if not supabase_ready():
        raise RuntimeError("SUPABASE_URL və ya SUPABASE_SERVICE_ROLE_KEY yoxdur.")

    params = {
        "select": "id,name,base_url,latest_url,rss_url,status,monitor_method",
        "status": "eq.active",
        "order": "name.asc",
    }

    if LIMIT_SOURCES > 0:
        params["limit"] = str(LIMIT_SOURCES)

    response = requests.get(
        f"{SUPABASE_URL}/rest/v1/sources",
        headers=supabase_headers(),
        params=params,
        timeout=REQUEST_TIMEOUT,
    )

    if response.status_code != 200:
        raise RuntimeError(f"Sources oxunmadı: {response.status_code} | {response.text[:300]}")

    return response.json() or []


def update_source(source_id, payload):
    payload["last_discovered_at"] = datetime.utcnow().isoformat()

    response = requests.patch(
        f"{SUPABASE_URL}/rest/v1/sources",
        headers=supabase_headers({"Prefer": "return=minimal"}),
        params={"id": f"eq.{source_id}"},
        json=payload,
        timeout=REQUEST_TIMEOUT,
    )

    if response.status_code not in (200, 204):
        print(f"Supabase update xətası: {source_id} | {response.status_code} | {response.text[:200]}", flush=True)
        return False

    return True


def fetch_url(url):
    try:
        response = requests.get(
            url,
            headers=HEADERS,
            timeout=REQUEST_TIMEOUT,
            allow_redirects=True,
        )

        if response.status_code != 200:
            return None, f"http_{response.status_code}"

        response.encoding = response.apparent_encoding
        return response.text, "ok"

    except Exception as exc:
        return None, f"error_{type(exc).__name__}"


def looks_like_rss(url, text):
    if not text:
        return False

    lower = text[:500].lower()

    if "<rss" in lower or "<feed" in lower or "<channel" in lower:
        return True

    parsed = feedparser.parse(text)
    return bool(parsed.entries)


def test_rss_url(rss_url):
    if not rss_url:
        return None

    text, status = fetch_url(rss_url)

    if not text:
        return None

    parsed = feedparser.parse(text)

    if parsed.entries:
        return {
            "method": "rss",
            "rss_url": rss_url,
            "latest_url": None,
            "score": min(len(parsed.entries), 20),
            "note": f"RSS işləyir | entry={len(parsed.entries)}",
        }

    return None


def discover_rss_from_home(base_url, html):
    if not html:
        return []

    rss_links = []

    try:
        soup = BeautifulSoup(html, "html.parser")

        for tag in soup.find_all("link", href=True):
            tag_type = (tag.get("type") or "").lower()
            tag_title = (tag.get("title") or "").lower()
            href = tag.get("href")

            if "rss" in tag_type or "atom" in tag_type or "rss" in tag_title or "feed" in tag_title:
                rss_links.append(urljoin(base_url, href))

    except Exception:
        pass

    for path in ["/rss", "/rss.xml", "/feed", "/feed.xml", "/az/rss", "/az/rss.xml"]:
        rss_links.append(urljoin(base_url, path))

    return list(dict.fromkeys(rss_links))[:10]


def test_rss_discovery(base_url, html):
    rss_links = discover_rss_from_home(base_url, html)

    for rss_url in rss_links:
        result = test_rss_url(rss_url)
        if result:
            result["method"] = "rss_discovered"
            result["note"] = f"RSS avtomatik tapıldı | {rss_url}"
            return result

    return None


def count_article_links(page_url, html):
    if not html:
        return 0

    domain = get_domain(page_url)
    soup = BeautifulSoup(html, "html.parser")
    count = 0

    article_patterns = [
        "/news/",
        "/xeber/",
        "/xeberler/",
        "/xəbərlər/",
        "/az/news/",
        "/az/xeber/",
        "/az/xeberler/",
        "/post/",
        "/article/",
        "/read/",
        "/item/",
        "/hadise/",
        "/cemiyyet/",
        "/sosial/",
        "/tehsil/",
        "/elm/",
        "/2024/",
        "/2025/",
        "/2026/",
    ]

    seen = set()

    for a in soup.find_all("a", href=True):
        href = urljoin(page_url, a["href"]).split("#")[0].split("?")[0]
        title = clean_text(a.get_text(" ", strip=True))

        if not href.startswith("http"):
            continue

        if get_domain(href) != domain:
            continue

        if href in seen:
            continue

        seen.add(href)

        if len(title) < 12:
            continue

        if any(pattern in href.lower() for pattern in article_patterns):
            count += 1

    return count


def test_latest_page(url):
    if not url:
        return None

    html, status = fetch_url(url)

    if not html:
        return None

    if looks_like_rss(url, html):
        parsed = feedparser.parse(html)
        if parsed.entries:
            return {
                "method": "rss",
                "rss_url": url,
                "latest_url": None,
                "score": min(len(parsed.entries), 20),
                "note": f"URL RSS kimi işləyir | entry={len(parsed.entries)}",
            }

    article_count = count_article_links(url, html)

    if article_count >= 3:
        return {
            "method": "latest_page",
            "rss_url": None,
            "latest_url": url,
            "score": article_count,
            "note": f"Son xəbərlər səhifəsi oxunur | link={article_count}",
        }

    return None


def test_common_paths(base_url):
    best = None

    for path in COMMON_LATEST_PATHS:
        url = urljoin(base_url, path)

        result = test_latest_page(url)

        if not result:
            continue

        if not best or result["score"] > best["score"]:
            best = result

        if result["method"] in ("rss", "rss_discovered") and result["score"] >= 5:
            return result

    return best


def test_sitemap(base_url):
    sitemap_url = urljoin(base_url, "/sitemap.xml")
    html, status = fetch_url(sitemap_url)

    if not html:
        return None

    urls = re.findall(r"<loc>(.*?)</loc>", html, flags=re.IGNORECASE)

    if len(urls) >= 5:
        return {
            "method": "sitemap",
            "rss_url": None,
            "latest_url": sitemap_url,
            "score": min(len(urls), 50),
            "note": f"Sitemap oxunur | url={len(urls)}",
        }

    return None


def analyze_source(source):
    source_id = source.get("id")
    name = source.get("name") or "Mənbə"
    base_url = clean_text(source.get("base_url"))
    latest_url = clean_text(source.get("latest_url"))
    rss_url = clean_text(source.get("rss_url"))

    if not base_url:
        return {
            "id": source_id,
            "name": name,
            "ok": False,
            "method": "failed",
            "note": "base_url yoxdur",
        }

    base_url = base_url.rstrip("/")

    print(f"Yoxlanır: {name} | {base_url}", flush=True)

    result = None

    result = test_rss_url(rss_url)
    if result:
        pass
    else:
        home_html, home_status = fetch_url(base_url)

        if home_html:
            result = test_rss_discovery(base_url, home_html)

        if not result and latest_url:
            result = test_latest_page(latest_url)

        if not result:
            result = test_common_paths(base_url)

        if not result:
            result = test_sitemap(base_url)

        if not result and home_html:
            article_count = count_article_links(base_url, home_html)
            if article_count >= 3:
                result = {
                    "method": "homepage",
                    "rss_url": None,
                    "latest_url": base_url,
                    "score": article_count,
                    "note": f"Homepage fallback işləyir | link={article_count}",
                }

    if not result:
        payload = {
            "monitor_method": "failed",
            "discovery_status": "needs_review",
            "notes": "Oxuma üsulu tapılmadı",
        }

        update_source(source_id, payload)

        return {
            "id": source_id,
            "name": name,
            "ok": False,
            "method": "failed",
            "note": "Oxuma üsulu tapılmadı",
        }

    payload = {
        "monitor_method": result["method"],
        "discovery_status": "readable",
        "discovery_score": result.get("score", 0),
        "notes": result["note"],
    }

    if result.get("rss_url"):
        payload["rss_url"] = result["rss_url"]

    if result.get("latest_url"):
        payload["latest_url"] = result["latest_url"]

    update_source(source_id, payload)

    return {
        "id": source_id,
        "name": name,
        "ok": True,
        "method": result["method"],
        "note": result["note"],
    }


def main():
    print("🚀 Source readability testi başladı", flush=True)

    sources = fetch_sources()
    total = len(sources)

    print(f"Toplam aktiv mənbə: {total}", flush=True)
    print(f"Worker sayı: {MAX_WORKERS}", flush=True)

    stats = {
        "rss": 0,
        "rss_discovered": 0,
        "latest_page": 0,
        "sitemap": 0,
        "homepage": 0,
        "failed": 0,
    }

    results = []

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {
            executor.submit(analyze_source, source): source
            for source in sources
        }

        for future in as_completed(futures):
            try:
                result = future.result()
            except Exception as exc:
                print(f"Worker xətası: {exc}", flush=True)
                stats["failed"] += 1
                continue

            method = result.get("method", "failed")
            stats[method] = stats.get(method, 0) + 1
            results.append(result)

            icon = "✅" if result.get("ok") else "❌"
            print(
                f"{icon} {result.get('name')} | {method} | {result.get('note')}",
                flush=True,
            )

    print("=" * 60, flush=True)
    print("📊 READABILITY YEKUNU", flush=True)
    print(f"🌐 Mənbə sayı: {total}", flush=True)
    print(f"🟢 RSS hazır: {stats.get('rss', 0)}", flush=True)
    print(f"🟢 RSS tapıldı: {stats.get('rss_discovered', 0)}", flush=True)
    print(f"📰 Latest page: {stats.get('latest_page', 0)}", flush=True)
    print(f"🗺️ Sitemap: {stats.get('sitemap', 0)}", flush=True)
    print(f"🏠 Homepage: {stats.get('homepage', 0)}", flush=True)
    print(f"❌ Oxunmayan: {stats.get('failed', 0)}", flush=True)
    print("=" * 60, flush=True)

    with open("source_readability_report.json", "w", encoding="utf-8") as file:
        json.dump(results, file, ensure_ascii=False, indent=2)

    print("✅ source_readability_report.json yaradıldı", flush=True)


if __name__ == "__main__":
    main()
