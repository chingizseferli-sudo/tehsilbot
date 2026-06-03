import argparse
import json
import re
import time
from collections import Counter
from urllib.parse import quote_plus, urljoin, urlparse

import feedparser
import requests
from bs4 import BeautifulSoup

DISCOVERED_FILE = "discovered_sites.json"
CONFIG_FILE = "courier_config_clean.json"
REVIEW_FILE = "review_sites.json"
REJECTED_FILE = "rejected_sites.json"
PATTERNS_FILE = "patterns.json"
KEYWORDS_FILE = "keywords.json"

REQUEST_TIMEOUT = 12

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; TehsilBotDiscovery/3.0)",
    "Accept-Language": "az-AZ,az;q=0.9,tr-TR;q=0.8,en-US;q=0.7,en;q=0.6",
}

DEFAULT_KEYWORDS = [
    "təhsil", "elm", "məktəb", "şagird", "müəllim", "universitet",
    "imtahan", "tələbə", "magistratura", "sertifikasiya", "olimpiada",
    "DİM", "TKTA", "ARTİ", "kurikulum", "dərs", "sinif",
]

NEWS_SECTION_WORDS = [
    "xəbərlər", "xeberler", "xəbər", "xeber", "xəbər lenti", "xeber lenti",
    "son xəbərlər", "son xeberler", "bütün xəbərlər", "butun xeberler",
    "media", "mətbuat", "metbuat", "press", "press center", "press-centre",
    "news", "latest", "latest news", "all news", "updates", "announcements",
]

COMMON_NEWS_PATHS_FAST = [
    "/news", "/xeber", "/xeberler", "/xəbərlər", "/media/news",
    "/az/news", "/az/xeber", "/az/xeberler", "/az/xəbərlər",
    "/son-xeberler", "/latest", "/all-news", "/tehsil", "/elm-ve-tehsil",
]

COMMON_NEWS_PATHS_DEEP = [
    "/news", "/news/", "/xeber", "/xeber/", "/xeberler", "/xeberler/",
    "/xəbərlər", "/xəbərlər/", "/az/news", "/az/news/", "/az/xeber",
    "/az/xeber/", "/az/xeberler", "/az/xeberler/", "/az/xəbərlər",
    "/az/xəbərlər/", "/media", "/media/news", "/media/news/", "/az/media",
    "/az/media/news", "/az/media/news/", "/all-news", "/allnews", "/latest",
    "/lastnews", "/son-xeberler", "/son-xeberler/", "/newsarchive",
    "/az/newsarchive", "/p/news", "/category/elm-ve-tehsil", "/category/tehsil",
    "/elm-ve-tehsil", "/tehsil", "/press-relizler", "/press-release",
    "/announcements", "/elanlar", "/updates", "/az/updates",
]

RSS_PATHS = [
    "/rss", "/rss.xml", "/feed", "/feed.xml", "/atom.xml",
    "/az/rss", "/az/rss.xml", "/az/feed", "/az/feed.xml",
]

BAD_DOMAINS = [
    "facebook.com", "instagram.com", "youtube.com", "youtu.be", "t.me",
    "twitter.com", "x.com", "linkedin.com", "whatsapp.com", "google.com",
]

BAD_WORDS = [
    "facebook", "instagram", "youtube", "telegram", "login", "register",
    "search", "contact", "about", "elaqe", "haqqimizda", "reklam",
    "tag", "author", "wp-content", "uploads", "cdn-cgi", "pdf", "docx",
]

ARTICLE_HINTS = [
    "/news/", "/xeber/", "/xeberler/", "/xəbərlər/", "/post/", "/article/",
    "/read/", "/item/", "/son-xeber/", "/sosial/", "/education/", "/tehsil/",
    "/elm/", "/2024/", "/2025/", "/2026/",
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
        return {
            "max_queries": 180,
            "max_entries_per_query": 70,
            "max_sections_per_source": 5,
            "sleep": 0.20,
            "paths": COMMON_NEWS_PATHS_DEEP,
            "check_home_links": True,
            "build_patterns": True,
        }

    return {
        "max_queries": 60,
        "max_entries_per_query": 30,
        "max_sections_per_source": 3,
        "sleep": 0.10,
        "paths": COMMON_NEWS_PATHS_FAST,
        "check_home_links": True,
        "build_patterns": False,
    }


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


def load_keywords() -> list[str]:
    data = read_json(KEYWORDS_FILE, {"keywords": DEFAULT_KEYWORDS})
    keywords = data.get("keywords", DEFAULT_KEYWORDS) if isinstance(data, dict) else DEFAULT_KEYWORDS
    cleaned = []
    for keyword in keywords:
        keyword = clean_text(keyword).lower()
        if keyword and keyword not in cleaned:
            cleaned.append(keyword)
    return cleaned or DEFAULT_KEYWORDS


KEYWORDS = load_keywords()


def clean_domain(url: str) -> str:
    try:
        domain = urlparse(url).netloc.lower().strip()
        if domain.startswith("www."):
            domain = domain[4:]
        return domain
    except Exception:
        return ""


def base_url(url: str) -> str:
    parsed = urlparse(url)
    if not parsed.scheme or not parsed.netloc:
        return ""
    return f"{parsed.scheme}://{parsed.netloc}"


def normalize_url(url: str) -> str:
    return clean_text(url).split("#")[0].rstrip("/").lower()


def is_bad_domain(url: str) -> bool:
    domain = clean_domain(url)
    return any(bad in domain for bad in BAD_DOMAINS)


def google_news_rss(query: str) -> str:
    return (
        "https://news.google.com/rss/search?"
        f"q={quote_plus(query)}"
        "&hl=az&gl=AZ&ceid=AZ:az"
    )


def build_search_queries(mode: str) -> list[str]:
    queries = []

    # Ümumi açar sözlər
    for keyword in KEYWORDS:
        queries.append(f"{keyword} Azərbaycan")
        if mode == "deep":
            queries.append(f"{keyword} xəbər")

    important = [
        "təhsil xəbərləri",
        "elm xəbərləri",
        "universitet xəbərləri",
        "məktəb xəbərləri",
        "müəllim xəbərləri",
        "şagird xəbərləri",
        "imtahan xəbərləri",
        "sertifikasiya xəbərləri",
        "olimpiada xəbərləri",
        "ali təhsil xəbərləri",
        "peşə təhsili xəbərləri",
        "xaricdə təhsil xəbərləri",
        "tələbə xəbərləri",
        "təhsil portalı",
        "elm portalı",
        "universitet media xəbərləri",
        "məktəb yenilikləri",
    ]

    if mode == "deep":
        important += [
            "site:.az təhsil xəbərləri",
            "site:.az məktəb xəbərləri",
            "site:.az universitet xəbərləri",
            "site:.az müəllim xəbərləri",
            "site:.az şagird xəbərləri",
            "site:.az imtahan xəbərləri",
            "site:.az elm xəbərləri",
            "site:.az media news",
            "site:.az xeberler təhsil",
            "site:.az xəbərlər təhsil",
            "site:.az son xəbərlər təhsil",
            "site:.edu.az xəbər",
            "site:.edu.az xəbərlər",
            "site:.edu.az media",
            "site:.edu.az news",
            "site:.edu.az tələbə",
            "site:.edu.az universitet xəbərləri",
        ]

    # Gov istənmədiyi üçün qəsdən əlavə olunmur.
    for q in important:
        q = clean_text(q)
        if q and q not in queries and "gov" not in q.lower():
            queries.append(q)

    return queries


def looks_like_news_url(url: str) -> bool:
    u = url.lower()
    if any(bad in u for bad in BAD_WORDS):
        return False
    return any(hint in u for hint in [
        "news", "xeber", "xeberler", "xəbər", "xəbərlər", "media/news",
        "all-news", "allnews", "latest", "lastnews", "son-xeber", "newsarchive",
        "p/news", "tehsil", "education", "elm-ve-tehsil", "announcements", "press",
    ])


def is_article_like_url(url: str) -> bool:
    u = url.lower()
    if any(bad in u for bad in BAD_WORDS):
        return False
    return any(hint in u for hint in ARTICLE_HINTS)


def discover_rss_links(session: requests.Session, page_url: str, page_html: str | None = None) -> list[str]:
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

    return list(dict.fromkeys(rss_links))[:8]


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
    for rss_url in discover_rss_links(session, page_url, page_html):
        ok, count = test_rss(session, rss_url)
        if ok:
            return rss_url, count
    return None, 0


def page_has_news_links(session: requests.Session, url: str) -> tuple[bool, int]:
    try:
        r = session.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT, allow_redirects=True)
        if r.status_code != 200:
            return False, 0

        soup = BeautifulSoup(r.text, "html.parser")
        count = 0

        for a in soup.find_all("a", href=True):
            text = clean_text(a.get_text(" ", strip=True))
            href = urljoin(url, a["href"]).split("#")[0]

            if len(text) < 15:
                continue
            if clean_domain(href) != clean_domain(url):
                continue

            combined = f"{text.lower()} {href.lower()}"
            if looks_like_news_url(href) or is_article_like_url(href) or any(k in combined for k in KEYWORDS):
                count += 1

            if count >= 5:
                return True, count

        return count >= 3, count
    except Exception:
        return False, 0


def find_news_sections(session: requests.Session, source_url: str, settings: dict) -> list[str]:
    root = base_url(source_url)
    if not root:
        return []

    found = []

    ok, _count = page_has_news_links(session, source_url)
    if looks_like_news_url(source_url) and ok:
        found.append(source_url.rstrip("/"))

    for path in settings["paths"]:
        candidate = urljoin(root, path).rstrip("/")
        if candidate in found:
            continue
        ok, _count = page_has_news_links(session, candidate)
        if ok:
            found.append(candidate)
        if len(found) >= settings["max_sections_per_source"]:
            return found

    if settings["check_home_links"]:
        try:
            r = session.get(root, headers=HEADERS, timeout=REQUEST_TIMEOUT, allow_redirects=True)
            if r.status_code == 200:
                soup = BeautifulSoup(r.text, "html.parser")
                for a in soup.find_all("a", href=True):
                    text = clean_text(a.get_text(" ", strip=True)).lower()
                    href = urljoin(root, a["href"]).split("#")[0].rstrip("/")
                    if clean_domain(href) != clean_domain(root):
                        continue
                    combined = f"{text} {href.lower()}"
                    if not any(word in combined for word in NEWS_SECTION_WORDS):
                        continue
                    if href in found:
                        continue
                    ok, _count = page_has_news_links(session, href)
                    if ok:
                        found.append(href)
                    if len(found) >= settings["max_sections_per_source"]:
                        break
        except Exception:
            pass

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
                if len(title) >= 15 and clean_domain(href) == clean_domain(url) and is_article_like_url(href):
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

    try:
        r = session.get(section_url, headers=HEADERS, timeout=REQUEST_TIMEOUT, allow_redirects=True)
        if r.status_code != 200:
            return {
                "name": name,
                "url": section_url,
                "enabled": True,
                "score": 0,
                "status": "rejected",
                "reason": f"HTTP {r.status_code}",
            }
        html_text = r.text
    except Exception as e:
        return {
            "name": name,
            "url": section_url,
            "enabled": True,
            "score": 0,
            "status": "rejected",
            "reason": str(e)[:150],
        }

    rss_url, rss_count = find_working_rss(session, section_url, html_text)
    if rss_url:
        score += 50
        reasons.append(f"RSS tapıldı ({rss_count})")

    ok, news_count = page_has_news_links(session, section_url)
    if ok:
        score += 25
        reasons.append(f"xəbər linkləri var ({news_count})")

    selector, xpaths, article_count = guess_selector_and_xpath(session, section_url)
    if selector:
        score += 15
        reasons.append(f"selector tapıldı: {selector}")
    elif xpaths:
        score += 8
        reasons.append("generic xpath əlavə edildi")

    domain = clean_domain(section_url)
    if domain.endswith(".edu.az"):
        score += 10
        reasons.append("edu.az domeni")

    if any(k in section_url.lower() for k in ["tehsil", "education", "elm", "universitet"]):
        score += 8
        reasons.append("URL təhsil/elm kontekstlidir")

    if score >= 80:
        status = "approved"
    elif score >= 50:
        status = "review"
    else:
        status = "rejected"

    return {
        "name": name or domain,
        "url": section_url.rstrip("/"),
        "enabled": True,
        "rss_url": rss_url,
        "selector": selector,
        "xpaths": xpaths,
        "keywords": KEYWORDS,
        "limit": 5,
        "score": score,
        "status": status,
        "analysis": {
            "rss_count": rss_count,
            "news_link_count": news_count,
            "article_block_count": article_count,
            "reasons": reasons,
        },
        "source_type": "discovered_professional",
    }


def collect_existing_domains() -> set[str]:
    domains = set()
    for filename in [DISCOVERED_FILE, CONFIG_FILE, REVIEW_FILE, REJECTED_FILE]:
        data = read_json(filename, {"sites": []})
        if not isinstance(data, dict):
            continue
        for site in data.get("sites", []):
            url = site.get("url", "")
            domain = clean_domain(url)
            if domain:
                domains.add(domain)
    return domains


def append_unique(filename: str, new_sites: list[dict]) -> int:
    data = read_json(filename, {"sites": []})
    if not isinstance(data, dict):
        data = {"sites": []}
    if "sites" not in data or not isinstance(data["sites"], list):
        data["sites"] = []

    existing_domains = {clean_domain(site.get("url", "")) for site in data["sites"] if site.get("url")}
    existing_urls = {normalize_url(site.get("url", "")) for site in data["sites"] if site.get("url")}

    added = 0
    for site in new_sites:
        d = clean_domain(site.get("url", ""))
        u = normalize_url(site.get("url", ""))
        if not d or not u:
            continue
        if d in existing_domains or u in existing_urls:
            continue
        data["sites"].append(site)
        existing_domains.add(d)
        existing_urls.add(u)
        added += 1

    write_json(filename, data)
    return added


def discover_sites(mode: str = "fast", add_to_config: bool = False):
    settings = get_mode_settings(mode)

    print("🔍 Discovery başladı", flush=True)
    print("Rejim:", mode, flush=True)
    print(f"Açar söz sayı: {len(KEYWORDS)}", flush=True)

    known_domains = collect_existing_domains()
    processed_domains = set()
    queries = build_search_queries(mode)[:settings["max_queries"]]
    print(f"Axtarış sorğusu sayı: {len(queries)}", flush=True)

    approved_sites = []
    review_sites = []
    rejected_sites = []

    session = requests.Session()
    session.headers.update(HEADERS)

    for query in queries:
        if "gov" in query.lower():
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
            if is_bad_domain(source_url):
                continue

            domain = clean_domain(source_url)
            if not domain:
                continue
            if domain in known_domains or domain in processed_domains:
                continue

            processed_domains.add(domain)

            sections = find_news_sections(session, source_url, settings)
            if not sections:
                print("Xəbər bölməsi tapılmadı:", source_name or domain, source_url, flush=True)
                continue

            for section_url in sections:
                section_domain = clean_domain(section_url)
                if not section_domain or section_domain in known_domains:
                    continue

                analyzed = analyze_section(session, source_name or section_domain, section_url)
                status = analyzed.get("status")
                score = analyzed.get("score", 0)

                if status == "approved":
                    approved_sites.append(analyzed)
                    known_domains.add(section_domain)
                    print(f"✅ APPROVED {score}: {analyzed['name']} | {section_url}", flush=True)
                elif status == "review":
                    review_sites.append(analyzed)
                    known_domains.add(section_domain)
                    print(f"🟡 REVIEW {score}: {analyzed['name']} | {section_url}", flush=True)
                else:
                    rejected_sites.append(analyzed)
                    known_domains.add(section_domain)
                    print(f"🔴 REJECTED {score}: {analyzed['name']} | {section_url}", flush=True)

            time.sleep(settings["sleep"])

    discovered_added = append_unique(DISCOVERED_FILE, approved_sites + review_sites)
    review_added = append_unique(REVIEW_FILE, review_sites)
    rejected_added = append_unique(REJECTED_FILE, rejected_sites)

    config_added = 0
    if add_to_config:
        config_added = append_unique(CONFIG_FILE, approved_sites)

    print("\n===== DISCOVERY YEKUNU =====", flush=True)
    print(f"✅ Approved: {len(approved_sites)} | config-ə əlavə: {config_added}", flush=True)
    print(f"🟡 Review: {len(review_sites)} | review faylına əlavə: {review_added}", flush=True)
    print(f"🔴 Rejected: {len(rejected_sites)} | rejected faylına əlavə: {rejected_added}", flush=True)
    print(f"📌 discovered_sites əlavə: {discovered_added}", flush=True)
    print("============================\n", flush=True)

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
    parser = argparse.ArgumentParser(description="TəhsilBot Professional Discovery Bot")
    parser.add_argument("--mode", choices=["fast", "deep"], default="fast")
    parser.add_argument("--add-to-config", action="store_true", help="Yalnız score 80+ approved saytları config-ə əlavə edir")
    parser.add_argument("--patterns", action="store_true", help="Pattern builder-i məcburi işə salır")
    args = parser.parse_args()

    new_sites = discover_sites(mode=args.mode, add_to_config=args.add_to_config)
    settings = get_mode_settings(args.mode)

    if args.patterns or settings["build_patterns"]:
        build_patterns()

    print("✅ Discovery tamamlandı", flush=True)
    print("Rejim:", args.mode, flush=True)
    print("Yeni namizəd sayı:", len(new_sites), flush=True)


if __name__ == "__main__":
    main()
