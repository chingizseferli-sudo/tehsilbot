import argparse
import json
import time
import feedparser
import requests
from bs4 import BeautifulSoup
from collections import Counter
from urllib.parse import quote_plus, urlparse, urljoin

DISCOVERED_FILE = "discovered_sites.json"
PATTERNS_FILE = "patterns.json"
KEYWORDS_FILE = "keywords.json"
CONFIG_FILE = "courier_config_clean.json"

REQUEST_TIMEOUT = 12

HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Accept-Language": "az-AZ,az;q=0.9,en-US;q=0.8",
}

DEFAULT_KEYWORDS = [
    "təhsil", "elm", "məktəb", "şagird", "müəllim",
    "universitet", "imtahan", "tələbə", "magistratura",
    "sertifikasiya", "tədqiqat", "olimpiada", "dim", "tkta"
]

COMMON_NEWS_PATHS_FAST = [
    "/news", "/xeber", "/xeberler", "/xəbərlər",
    "/az/news", "/az/xeber", "/az/xeberler", "/az/xəbərlər",
    "/media/news", "/az/media/news",
    "/son-xeberler", "/latest", "/all-news",
    "/tehsil", "/elm-ve-tehsil"
]

COMMON_NEWS_PATHS_DEEP = [
    "/news", "/news/", "/xeber", "/xeber/", "/xeberler", "/xeberler/",
    "/xəbərlər", "/xəbərlər/", "/az/news", "/az/news/", "/az/xeber",
    "/az/xeber/", "/az/xeberler", "/az/xeberler/", "/az/xəbərlər",
    "/az/xəbərlər/", "/media/news", "/media/news/", "/az/media/news",
    "/az/media/news/", "/all-news", "/allnews", "/latest", "/lastnews",
    "/son-xeberler", "/son-xeberler/", "/newsarchive", "/az/newsarchive",
    "/p/news", "/category/elm-ve-tehsil", "/category/tehsil",
    "/elm-ve-tehsil", "/tehsil", "/press-relizler", "/press-release",
    "/media", "/az/media", "/announcements", "/elanlar"
]

BAD_WORDS = [
    "facebook", "instagram", "youtube", "telegram", "login", "register",
    "search", "contact", "about", "elaqe", "haqqimizda", "reklam",
    "tag", "author", "wp-content", "uploads", "cdn-cgi"
]

GOOD_PATTERN_HINTS = [
    "news", "xeber", "xeberler", "xəbər", "xəbərlər",
    "article", "post", "read", "item", "son-xeber",
    "latest", "media", "tehsil", "elm"
]

BAD_PATTERNS = [
    "/tag/", "/category/", "/kateqoriya/", "/author/",
    "/page/", "/login/", "/register/", "/search/",
    "/video/", "/photo/", "/contact/", "/about/",
    "/elaqe/", "/haqqimizda/", "/reklam/",
    "/wp-content/", "/uploads/", "/cdn-cgi/"
]


def get_mode_settings(mode):
    if mode == "deep":
        return {
            "max_queries": 120,
            "max_entries_per_query": 50,
            "max_sections_per_source": 4,
            "sleep": 0.25,
            "paths": COMMON_NEWS_PATHS_DEEP,
            "check_home_links": True,
            "build_patterns": True,
        }

    return {
        "max_queries": 35,
        "max_entries_per_query": 20,
        "max_sections_per_source": 2,
        "sleep": 0.12,
        "paths": COMMON_NEWS_PATHS_FAST,
        "check_home_links": False,
        "build_patterns": False,
    }


def read_json(filename, default):
    try:
        with open(filename, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return default
    except Exception as e:
        print(f"JSON oxunmadı: {filename} | {e}")
        return default


def write_json(filename, data):
    with open(filename, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def load_keywords():
    data = read_json(KEYWORDS_FILE, {"keywords": DEFAULT_KEYWORDS})
    keywords = data.get("keywords", DEFAULT_KEYWORDS)

    cleaned = []
    for keyword in keywords:
        keyword = str(keyword).strip().lower()
        if keyword and keyword not in cleaned:
            cleaned.append(keyword)

    return cleaned or DEFAULT_KEYWORDS


KEYWORDS = load_keywords()


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
    return f"{parsed.scheme}://{parsed.netloc}"


def normalize_url(url):
    return url.strip().rstrip("/").lower()


def google_news_rss(query):
    return (
        "https://news.google.com/rss/search?"
        f"q={quote_plus(query)}"
        "&hl=az&gl=AZ&ceid=AZ:az"
    )


def build_search_queries(mode):
    queries = []

    for keyword in KEYWORDS:
        queries.append(f"{keyword} Azərbaycan")

    important = [
        "Elm və Təhsil Nazirliyi",
        "Dövlət İmtahan Mərkəzi",
        "Təhsildə Keyfiyyət Təminatı Agentliyi",
        "təhsil xəbərləri",
        "elm xəbərləri",
        "universitet xəbərləri",
        "məktəb xəbərləri",
        "imtahan xəbərləri",
    ]

    if mode == "deep":
        important += [
            "Azərbaycan məktəb xəbərləri",
            "Azərbaycan universitet yenilikləri",
            "təhsil agentliyi xəbərləri",
            "kollec xəbərləri Azərbaycan",
            "lisey xəbərləri Azərbaycan",
            "xaricdə təhsil xəbərləri",
            "elm və təhsil yenilikləri",
            "site:edu.az xəbər",
            "site:gov.az təhsil",
            "site:az universitet xəbər",
        ]

    for q in important:
        if q not in queries:
            queries.append(q)

    return queries


def looks_like_news_url(url):
    u = url.lower()

    if any(bad in u for bad in BAD_WORDS):
        return False

    hints = [
        "news", "xeber", "xeberler", "xəbər", "xəbərlər",
        "media/news", "all-news", "allnews", "latest",
        "lastnews", "son-xeber", "newsarchive", "p/news",
        "tehsil", "education", "elm-ve-tehsil", "elanlar",
        "announcements", "press"
    ]

    return any(h in u for h in hints)


def page_has_news_links(session, url):
    try:
        r = session.get(
            url,
            headers=HEADERS,
            timeout=REQUEST_TIMEOUT,
            allow_redirects=True
        )

        if r.status_code != 200:
            return False

        soup = BeautifulSoup(r.text, "html.parser")
        count = 0

        for a in soup.find_all("a", href=True):
            text = " ".join(a.get_text(" ", strip=True).split())
            href = urljoin(url, a["href"])

            if len(text) < 15:
                continue

            if clean_domain(href) != clean_domain(url):
                continue

            text_lower = text.lower()

            if looks_like_news_url(href) or any(k in text_lower for k in KEYWORDS):
                count += 1

            if count >= 3:
                return True

        return False

    except Exception:
        return False


def find_news_sections(session, source_url, settings):
    root = base_url(source_url)

    if not root:
        return []

    found = []

    if looks_like_news_url(source_url) and page_has_news_links(session, source_url):
        found.append(source_url.rstrip("/"))

    for path in settings["paths"]:
        candidate = urljoin(root, path).rstrip("/")

        if candidate in found:
            continue

        if page_has_news_links(session, candidate):
            found.append(candidate)

        if len(found) >= settings["max_sections_per_source"]:
            return found

    if settings["check_home_links"]:
        try:
            r = session.get(root, headers=HEADERS, timeout=REQUEST_TIMEOUT)

            if r.status_code == 200:
                soup = BeautifulSoup(r.text, "html.parser")

                for a in soup.find_all("a", href=True):
                    href = urljoin(root, a["href"]).split("#")[0].rstrip("/")

                    if clean_domain(href) != clean_domain(root):
                        continue

                    if not looks_like_news_url(href):
                        continue

                    if href in found:
                        continue

                    if page_has_news_links(session, href):
                        found.append(href)

                    if len(found) >= settings["max_sections_per_source"]:
                        break

        except Exception:
            pass

    return found


def collect_existing_domains():
    domains = set()

    discovered = read_json(DISCOVERED_FILE, {"sites": []})
    for site in discovered.get("sites", []):
        url = site.get("url", "")
        domain = clean_domain(url)
        if domain:
            domains.add(domain)

    config = read_json(CONFIG_FILE, {"sites": []})
    for site in config.get("sites", []):
        url = site.get("url", "")
        domain = clean_domain(url)
        if domain:
            domains.add(domain)

    return domains


def discover_sites(mode="fast", add_to_config=False):
    settings = get_mode_settings(mode)

    print("🔍 Discovery başladı")
    print("Rejim:", mode)
    print(f"Açar söz sayı: {len(KEYWORDS)}")

    data = read_json(DISCOVERED_FILE, {"sites": []})
    existing = data.get("sites", [])

    known_urls = {
        normalize_url(site.get("url", ""))
        for site in existing
        if site.get("url")
    }

    known_domains = collect_existing_domains()
    processed_domains = set()

    queries = build_search_queries(mode)[:settings["max_queries"]]
    print(f"Axtarış sorğusu sayı: {len(queries)}")

    new_sites = []

    session = requests.Session()
    session.headers.update(HEADERS)

    for query in queries:
        print("Axtarılır:", query)

        try:
            feed = feedparser.parse(google_news_rss(query))
        except Exception as e:
            print("Google News RSS xətası:", e)
            continue

        print("Nəticə sayı:", len(feed.entries))

        for entry in feed.entries[:settings["max_entries_per_query"]]:
            source = entry.get("source", {})
            source_name = None
            source_url = None

            if isinstance(source, dict):
                source_name = source.get("title")
                source_url = source.get("href")

            if not source_url:
                continue

            source_url = source_url.strip()

            if not source_url.startswith("http"):
                continue

            domain = clean_domain(source_url)

            if not domain:
                continue

            if domain in known_domains:
                continue

            if domain in processed_domains:
                continue

            processed_domains.add(domain)

            sections = find_news_sections(session, source_url, settings)

            if not sections:
                print("Xəbər bölməsi tapılmadı:", source_name or domain, source_url)
                continue

            for section_url in sections:
                normalized = normalize_url(section_url)
                section_domain = clean_domain(section_url)

                if not section_domain:
                    continue

                if normalized in known_urls:
                    continue

                if section_domain in known_domains:
                    continue

                site = {
                    "name": source_name or section_domain,
                    "url": section_url.rstrip("/"),
                    "enabled": True,
                    "xpaths": [],
                    "selector": None,
                    "keywords": KEYWORDS,
                    "limit": 1,
                    "source_type": f"discovered_{mode}_news_section"
                }

                new_sites.append(site)
                known_urls.add(normalized)
                known_domains.add(section_domain)

                print("✅ Yeni xəbər bölməsi tapıldı:", source_name or section_domain, section_url)

            time.sleep(settings["sleep"])

    all_sites = existing + new_sites
    write_json(DISCOVERED_FILE, {"sites": all_sites})

    print("Yeni bölmə sayı:", len(new_sites))
    print("Ümumi discovered mənbə sayı:", len(all_sites))

    if add_to_config:
        add_new_sites_to_config(new_sites)

    return new_sites


def add_new_sites_to_config(new_sites):
    if not new_sites:
        print("Config-ə əlavə ediləcək yeni sayt yoxdur")
        return

    config = read_json(CONFIG_FILE, {"sites": []})

    if "sites" not in config:
        config["sites"] = []

    existing_domains = {
        clean_domain(site.get("url", ""))
        for site in config.get("sites", [])
        if site.get("url")
    }

    added = 0

    for site in new_sites:
        domain = clean_domain(site.get("url", ""))

        if not domain:
            continue

        if domain in existing_domains:
            continue

        config["sites"].append(site)
        existing_domains.add(domain)
        added += 1

    write_json(CONFIG_FILE, config)

    print(f"courier_config_clean.json faylına əlavə edildi: {added}")


def is_bad_pattern(pattern):
    return any(bad in pattern.lower() for bad in BAD_PATTERNS)


def is_good_pattern(pattern):
    return any(hint in pattern.lower() for hint in GOOD_PATTERN_HINTS)


def analyze_site_patterns(session, site):
    url = site.get("url")

    if not url:
        return []

    try:
        print(f"Pattern yoxlanır: {url}")

        r = session.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)

        print("Status:", r.status_code)

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

        selected = [
            pattern
            for pattern, count in counter.most_common(15)
            if count >= 1
        ]

        print("Tapılan patternlər:", selected)

        return selected

    except Exception as e:
        print("Pattern xətası:", e)
        return []


def build_patterns():
    print("🧩 Pattern builder başladı")

    discovered = read_json(DISCOVERED_FILE, {"sites": []})
    patterns = read_json(PATTERNS_FILE, {})

    checked = 0
    updated = 0

    session = requests.Session()
    session.headers.update(HEADERS)

    for site in discovered.get("sites", []):
        url = site.get("url", "")

        if not url:
            continue

        domain = clean_domain(url)

        if not domain:
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

    print("Yoxlanılan sayt sayı:", checked)
    print("Pattern yenilənən sayt sayı:", updated)


def main():
    parser = argparse.ArgumentParser(description="TəhsilBot Discovery Bot")

    parser.add_argument(
        "--mode",
        choices=["fast", "deep"],
        default="fast",
        help="fast: sürətli gündəlik axtarış, deep: geniş və ağır axtarış"
    )

    parser.add_argument(
        "--add-to-config",
        action="store_true",
        help="Yeni tapılan saytları courier_config_clean.json faylına əlavə edir"
    )

    parser.add_argument(
        "--patterns",
        action="store_true",
        help="Pattern builder-i məcburi işə salır"
    )

    args = parser.parse_args()

    new_sites = discover_sites(
        mode=args.mode,
        add_to_config=args.add_to_config
    )

    settings = get_mode_settings(args.mode)

    if args.patterns or settings["build_patterns"]:
        build_patterns()

    print("✅ Discovery tamamlandı")
    print("Rejim:", args.mode)
    print("Yeni sayt sayı:", len(new_sites))


if __name__ == "__main__":
    main()
