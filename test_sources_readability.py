import os
import re
import json
from datetime import datetime
from urllib.parse import urljoin, urlparse, quote
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
import feedparser
from bs4 import BeautifulSoup
from lxml import html as lxml_html

SUPABASE_URL = os.getenv("SUPABASE_URL", "").strip().rstrip("/")
SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "").strip()

REQUEST_TIMEOUT = int(os.getenv("REQUEST_TIMEOUT", "12"))
MAX_WORKERS = int(os.getenv("MAX_WORKERS", "10"))
LIMIT_SOURCES = int(os.getenv("LIMIT_SOURCES", "0"))
EXCLUDED_DOMAIN_SUFFIXES = {
    item.strip().lower().lstrip(".")
    for item in os.getenv("EXCLUDED_DOMAIN_SUFFIXES", "gov.az").split(",")
    if item.strip()
}

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,application/rss+xml;q=0.8,application/atom+xml;q=0.8,*/*;q=0.7",
    "Accept-Language": "az-AZ,az;q=0.9,en-US;q=0.8",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
}

COMMON_PATHS = [
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
    "/sosial",
    "/tehsil",
    "/elm",
]

BLOCK_WORDS = [
    "cloudflare",
    "checking your browser",
    "captcha",
    "access denied",
    "forbidden",
    "enable javascript",
    "just a moment",
    "cf-browser-verification",
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


def clean_text(value):
    return re.sub(r"\s+", " ", str(value or "")).strip()


def get_domain(url):
    return urlparse(str(url or "")).netloc.replace("www.", "").lower()


def is_excluded_domain(url):
    domain = get_domain(url).split(":")[0]
    return any(domain == suffix or domain.endswith("." + suffix) for suffix in EXCLUDED_DOMAIN_SUFFIXES)


def fetch_sources():
    params = {
        "select": "id,name,base_url,latest_url,rss_url,status,monitor_method,selector,article_pattern,discovery_score,notes",
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
        raise RuntimeError(
            f"Sources oxunmadı: {response.status_code} | {response.text[:300]}"
        )

    rows = response.json() or []
    filtered = [
        source for source in rows
        if not (
            is_excluded_domain(source.get("base_url"))
            or is_excluded_domain(source.get("latest_url"))
            or is_excluded_domain(source.get("rss_url"))
        )
    ]
    skipped = len(rows) - len(filtered)
    if skipped:
        print(f"Excluded domain skipped in readability: {skipped}", flush=True)
    return filtered


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
        print(
            f"Supabase update xətası: {source_id} | {response.status_code} | {response.text[:200]}",
            flush=True,
        )
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

        text = response.text or ""
        lower = text[:3000].lower()

        if response.status_code in (401, 403, 429):
            return None, "blocked", response.status_code

        if any(word in lower for word in BLOCK_WORDS):
            return text, "blocked", response.status_code

        if response.status_code == 404:
            return None, "dead", response.status_code

        if response.status_code != 200:
            return None, f"http_{response.status_code}", response.status_code

        response.encoding = response.apparent_encoding
        return response.text, "ok", response.status_code

    except requests.exceptions.SSLError:
        return None, "ssl_error", 0
    except requests.exceptions.ConnectionError:
        return None, "connection_error", 0
    except requests.exceptions.Timeout:
        return None, "timeout", 0
    except Exception as exc:
        return None, f"error_{type(exc).__name__}", 0


def looks_like_rss(text):
    if not text:
        return False

    lower = text[:800].lower()

    if "<rss" in lower or "<feed" in lower or "<channel" in lower:
        return True

    parsed = feedparser.parse(text)
    return bool(parsed.entries)


def test_rss_url(rss_url):
    if not rss_url:
        return None

    text, status, code = fetch_url(rss_url)

    if not text:
        return None

    parsed = feedparser.parse(text)

    if parsed.entries:
        return {
            "method": "rss",
            "status": "readable",
            "rss_url": rss_url,
            "latest_url": None,
            "score": min(len(parsed.entries), 100),
            "note": f"RSS işləyir | entry={len(parsed.entries)}",
        }

    return None


def discover_rss_from_html(base_url, html):
    rss_links = []

    if not html:
        return rss_links

    soup = BeautifulSoup(html, "html.parser")

    for tag in soup.find_all("link", href=True):
        tag_type = (tag.get("type") or "").lower()
        tag_title = (tag.get("title") or "").lower()
        href = tag.get("href")

        if (
            "rss" in tag_type
            or "atom" in tag_type
            or "rss" in tag_title
            or "feed" in tag_title
        ):
            rss_links.append(urljoin(base_url, href))

    for path in ["/rss", "/rss.xml", "/feed", "/feed.xml", "/az/rss", "/az/rss.xml"]:
        rss_links.append(urljoin(base_url, path))

    return list(dict.fromkeys(rss_links))[:10]


def test_rss_discovery(base_url, html):
    for rss_url in discover_rss_from_html(base_url, html):
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
    seen = set()

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


def test_css_selector(page_url, html, selector):
    selector = clean_text(selector)

    if not html or not selector:
        return None

    try:
        soup = BeautifulSoup(html, "html.parser")
        blocks = soup.select(selector)

        if len(blocks) >= 2:
            return {
                "method": "selector",
                "status": "readable",
                "rss_url": None,
                "latest_url": page_url,
                "score": min(len(blocks), 100),
                "note": f"CSS selector işləyir | blok={len(blocks)} | selector={selector}",
            }

    except Exception:
        return None

    return None


def test_xpath_patterns(page_url, html, article_pattern):
    article_pattern = clean_text(article_pattern)

    if not html or not article_pattern:
        return None

    patterns = [
        clean_text(part)
        for part in re.split(r"[,\n\r]+", article_pattern)
        if clean_text(part)
    ]

    if not patterns:
        return None

    try:
        tree = lxml_html.fromstring(html)

        best_count = 0
        best_pattern = ""

        for pattern in patterns:
            try:
                result = tree.xpath(pattern)
                count = len(result)

                if count > best_count:
                    best_count = count
                    best_pattern = pattern
            except Exception:
                continue

        if best_count >= 2:
            return {
                "method": "xpath_pattern",
                "status": "readable",
                "rss_url": None,
                "latest_url": page_url,
                "score": min(best_count, 100),
                "note": f"XPath pattern işləyir | blok={best_count} | pattern={best_pattern}",
            }

    except Exception:
        return None

    return None


def test_latest_page(url):
    if not url:
        return None

    html, status, code = fetch_url(url)

    if not html:
        if status == "blocked":
            return {
                "method": "blocked",
                "status": "blocked",
                "rss_url": None,
                "latest_url": url,
                "score": 0,
                "note": f"Sayt bloklayır | status={code}",
            }

        if status in {"dead", "connection_error"}:
            return None

        return None

    if looks_like_rss(html):
        parsed = feedparser.parse(html)
        if parsed.entries:
            return {
                "method": "rss",
                "status": "readable",
                "rss_url": url,
                "latest_url": None,
                "score": min(len(parsed.entries), 100),
                "note": f"URL RSS kimi işləyir | entry={len(parsed.entries)}",
            }

    article_count = count_article_links(url, html)

    if article_count >= 3:
        return {
            "method": "latest_page",
            "status": "readable",
            "rss_url": None,
            "latest_url": url,
            "score": article_count,
            "note": f"Son xəbərlər səhifəsi oxunur | link={article_count}",
        }

    if article_count > 0:
        return {
            "method": "recoverable",
            "status": "recoverable",
            "rss_url": None,
            "latest_url": url,
            "score": article_count,
            "note": f"Az sayda xəbər linki tapıldı | link={article_count}",
        }

    return None


def test_common_paths(base_url):
    best = None
    blocked_result = None

    for path in COMMON_PATHS:
        url = urljoin(base_url.rstrip("/") + "/", path.lstrip("/"))
        result = test_latest_page(url)

        if not result:
            continue

        if result["method"] == "blocked":
            blocked_result = blocked_result or result
            continue

        if not best or result["score"] > best["score"]:
            best = result

        if result["method"] in {"rss", "rss_discovered"} and result["score"] >= 5:
            return result

        if result["method"] == "latest_page" and result["score"] >= 5:
            return result

    return best or blocked_result


def test_sitemap(base_url):
    sitemap_url = urljoin(base_url.rstrip("/") + "/", "sitemap.xml")
    html, status, code = fetch_url(sitemap_url)

    if not html:
        return None

    urls = re.findall(r"<loc>(.*?)</loc>", html, flags=re.IGNORECASE)

    article_urls = [
        url for url in urls
        if any(x in url.lower() for x in ["/news", "/xeber", "/post", "/article", "/2024", "/2025", "/2026"])
    ]

    if len(article_urls) >= 3:
        return {
            "method": "sitemap",
            "status": "readable",
            "rss_url": None,
            "latest_url": sitemap_url,
            "score": min(len(article_urls), 100),
            "note": f"Sitemap oxunur | article_url={len(article_urls)}",
        }

    if len(urls) >= 5:
        return {
            "method": "recoverable",
            "status": "recoverable",
            "rss_url": None,
            "latest_url": sitemap_url,
            "score": min(len(urls), 100),
            "note": f"Sitemap var, amma article pattern zəifdir | url={len(urls)}",
        }

    return None


def test_google_news_fallback(base_url):
    domain = get_domain(base_url)

    if not domain:
        return None

    query = quote(f"site:{domain} when:7d")
    rss_url = f"https://news.google.com/rss/search?q={query}&hl=az&gl=AZ&ceid=AZ:az"

    text, status, code = fetch_url(rss_url)

    if not text:
        return None

    parsed = feedparser.parse(text)

    if parsed.entries:
        return {
            "method": "google_news_fallback",
            "status": "readable",
            "rss_url": rss_url,
            "latest_url": base_url,
            "score": min(len(parsed.entries), 100),
            "note": f"Sayt birbaşa zəifdir, Google News fallback işləyir | entry={len(parsed.entries)}",
        }

    return None


def classify_hard_failure(base_url, home_status, home_code):
    if home_status == "blocked":
        return {
            "method": "blocked",
            "status": "blocked",
            "rss_url": None,
            "latest_url": base_url,
            "score": 0,
            "note": f"Sayt bloklayır | status={home_code}",
        }

    if home_status in {"dead", "connection_error"}:
        return {
            "method": "dead",
            "status": "dead",
            "rss_url": None,
            "latest_url": base_url,
            "score": 0,
            "note": f"Sayt açılmır | {home_status}",
        }

    if home_status == "ssl_error":
        return {
            "method": "failed",
            "status": "needs_review",
            "rss_url": None,
            "latest_url": base_url,
            "score": 0,
            "note": "SSL xətası",
        }

    if home_status == "timeout":
        return {
            "method": "failed",
            "status": "needs_review",
            "rss_url": None,
            "latest_url": base_url,
            "score": 0,
            "note": "Timeout",
        }

    return {
        "method": "failed",
        "status": "needs_review",
        "rss_url": None,
        "latest_url": base_url,
        "score": 0,
        "note": f"Oxuma üsulu tapılmadı | {home_status}",
    }


def analyze_source(source):
    source_id = source.get("id")
    name = source.get("name") or "Mənbə"
    base_url = clean_text(source.get("base_url"))
    latest_url = clean_text(source.get("latest_url"))
    rss_url = clean_text(source.get("rss_url"))
    selector = clean_text(source.get("selector"))
    article_pattern = clean_text(source.get("article_pattern"))

    if not base_url:
        result = {
            "method": "failed",
            "status": "needs_review",
            "rss_url": None,
            "latest_url": None,
            "score": 0,
            "note": "base_url yoxdur",
        }
        update_result(source_id, result)
        return format_result(source, result)

    base_url = base_url.rstrip("/")

    print(f"Yoxlanır: {name} | {base_url}", flush=True)

    result = test_rss_url(rss_url)

    home_html = None
    home_status = "not_checked"
    home_code = 0

    if not result:
        home_html, home_status, home_code = fetch_url(base_url)

        if home_status == "blocked":
            result = test_google_news_fallback(base_url) or classify_hard_failure(
                base_url, home_status, home_code
            )

        if not result and home_html:
            result = test_rss_discovery(base_url, home_html)

        if not result and home_html and selector:
            result = test_css_selector(base_url, home_html, selector)

        if not result and home_html and article_pattern:
            result = test_xpath_patterns(base_url, home_html, article_pattern)

        if not result and latest_url:
            latest_html, latest_status, latest_code = fetch_url(latest_url)

            if latest_html and selector:
                result = test_css_selector(latest_url, latest_html, selector)

            if not result and latest_html and article_pattern:
                result = test_xpath_patterns(latest_url, latest_html, article_pattern)

            if not result:
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
                    "status": "readable",
                    "rss_url": None,
                    "latest_url": base_url,
                    "score": article_count,
                    "note": f"Homepage fallback işləyir | link={article_count}",
                }
            elif article_count > 0:
                result = {
                    "method": "recoverable",
                    "status": "recoverable",
                    "rss_url": None,
                    "latest_url": base_url,
                    "score": article_count,
                    "note": f"Homepage-də az link var | link={article_count}",
                }

        if not result:
            result = test_google_news_fallback(base_url)

        if not result:
            result = classify_hard_failure(base_url, home_status, home_code)

    update_result(source_id, result)

    return format_result(source, result)


def update_result(source_id, result):
    payload = {
        "monitor_method": result["method"],
        "discovery_status": result["status"],
        "discovery_score": result.get("score", 0),
        "notes": result["note"],
    }

    if result.get("rss_url"):
        payload["rss_url"] = result["rss_url"]

    if result.get("latest_url"):
        payload["latest_url"] = result["latest_url"]

    update_source(source_id, payload)


def format_result(source, result):
    return {
        "id": source.get("id"),
        "name": source.get("name"),
        "base_url": source.get("base_url"),
        "ok": result["status"] in {"readable", "recoverable"},
        "method": result["method"],
        "status": result["status"],
        "score": result.get("score", 0),
        "note": result["note"],
    }


def main():
    print("🚀 Source readability recovery testi başladı", flush=True)

    sources = fetch_sources()
    total = len(sources)

    print(f"Toplam aktiv mənbə: {total}", flush=True)
    print(f"Worker sayı: {MAX_WORKERS}", flush=True)

    stats = {}

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
                result = {
                    "name": "unknown",
                    "method": "failed",
                    "status": "needs_review",
                    "note": str(exc),
                    "ok": False,
                }

            method = result.get("method", "failed")
            stats[method] = stats.get(method, 0) + 1
            results.append(result)

            icon = "✅" if result.get("ok") else "❌"
            print(
                f"{icon} {result.get('name')} | {method} | {result.get('status')} | {result.get('note')}",
                flush=True,
            )

    print("=" * 60, flush=True)
    print("📊 READABILITY RECOVERY YEKUNU", flush=True)
    print(f"🌐 Mənbə sayı: {total}", flush=True)

    for method, count in sorted(stats.items(), key=lambda x: x[1], reverse=True):
        print(f"{method}: {count}", flush=True)

    print("=" * 60, flush=True)

    with open("source_readability_report.json", "w", encoding="utf-8") as file:
        json.dump(results, file, ensure_ascii=False, indent=2)

    print("✅ source_readability_report.json yaradıldı", flush=True)


if __name__ == "__main__":
    main()
