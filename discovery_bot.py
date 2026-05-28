import json
import feedparser
import requests
from bs4 import BeautifulSoup
from urllib.parse import quote_plus, urlparse, urljoin

OUTPUT_FILE = "discovered_sites.json"

SEARCH_QUERIES = [
    "təhsil Azərbaycan",
    "məktəb Azərbaycan",
    "şagird Azərbaycan",
    "universitet Azərbaycan",
    "imtahan Azərbaycan",
    "elm Azərbaycan",
    "tələbə Azərbaycan",
    "müəllim Azərbaycan",
    "sertifikasiya müəllim",
    "magistratura qəbul",
    "DİM imtahan",
    "Elm və Təhsil Nazirliyi",
    "site:.az təhsil",
    "site:.az məktəb",
    "site:.az universitet",
    "site:.az elm",
    "site:.az imtahan",
    "site:.az tələbə",
    "site:.az müəllim"
]

DEFAULT_KEYWORDS = [
    "təhsil", "məktəb", "şagird", "müəllim",
    "universitet", "imtahan", "tələbə", "elm",
    "tədris", "abituriyent", "magistr", "doktorant",
    "kollec", "lisey", "sertifikasiya", "sertifikatlaşdırma",
    "dim", "tkta", "məktəbəqədər", "institut"
]

HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Accept-Language": "az-AZ,az;q=0.9,en-US;q=0.8"
}

COMMON_NEWS_PATHS = [
    "/news",
    "/news/",
    "/xeber",
    "/xeber/",
    "/xeberler",
    "/xeberler/",
    "/xəbərlər",
    "/xəbərlər/",
    "/az/news",
    "/az/news/",
    "/az/xeberler",
    "/az/xeberler/",
    "/az/xəbərlər",
    "/az/xəbərlər/",
    "/media/news",
    "/media/news/",
    "/az/media/news",
    "/az/media/news/",
    "/all-news",
    "/allnews",
    "/latest",
    "/lastnews",
    "/son-xeberler",
    "/son-xeberler/",
    "/newsarchive",
    "/az/newsarchive",
    "/p/news"
]

BAD_WORDS = [
    "facebook", "instagram", "youtube", "telegram",
    "login", "register", "search", "contact", "about",
    "elaqe", "haqqimizda", "reklam", "tag", "author"
]


def load_existing():
    try:
        with open(OUTPUT_FILE, "r", encoding="utf-8") as f:
            return json.load(f).get("sites", [])
    except Exception:
        return []


def save_sites(sites):
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump({"sites": sites}, f, ensure_ascii=False, indent=2)


def google_news_rss(query):
    return (
        "https://news.google.com/rss/search?"
        f"q={quote_plus(query)}"
        "&hl=az"
        "&gl=AZ"
        "&ceid=AZ:az"
    )


def clean_domain(url):
    try:
        return urlparse(url).netloc.replace("www.", "").lower()
    except Exception:
        return ""


def base_url(url):
    parsed = urlparse(url)
    if not parsed.scheme or not parsed.netloc:
        return ""
    return f"{parsed.scheme}://{parsed.netloc}"


def looks_like_news_url(url):
    u = url.lower()
    if any(bad in u for bad in BAD_WORDS):
        return False

    hints = [
        "news", "xeber", "xeberler", "xəbər", "xəbərlər",
        "media/news", "all-news", "allnews", "latest",
        "lastnews", "son-xeber", "newsarchive", "p/news"
    ]

    return any(h in u for h in hints)


def page_has_news_links(url):
    try:
        r = requests.get(url, headers=HEADERS, timeout=10, allow_redirects=True)
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

            if looks_like_news_url(href) or any(k.lower() in text.lower() for k in DEFAULT_KEYWORDS):
                count += 1

            if count >= 3:
                return True

        return False

    except Exception:
        return False


def find_news_sections(source_url):
    root = base_url(source_url)
    if not root:
        return []

    found = []

    # 1) Əgər Google News mənbə URL-i artıq xəbər bölməsinə oxşayırsa
    if looks_like_news_url(source_url) and page_has_news_links(source_url):
        found.append(source_url.rstrip("/"))

    # 2) Standart xəbər yollarını yoxla
    for path in COMMON_NEWS_PATHS:
        candidate = urljoin(root, path)

        if candidate.rstrip("/") in found:
            continue

        if page_has_news_links(candidate):
            found.append(candidate.rstrip("/"))

        if len(found) >= 3:
            break

    # 3) Ana səhifədə xəbər bölməsi linklərini tap
    try:
        r = requests.get(root, headers=HEADERS, timeout=10)
        if r.status_code == 200:
            soup = BeautifulSoup(r.text, "html.parser")

            for a in soup.find_all("a", href=True):
                href = urljoin(root, a["href"]).split("#")[0]

                if clean_domain(href) != clean_domain(root):
                    continue

                if not looks_like_news_url(href):
                    continue

                href = href.rstrip("/")

                if href in found:
                    continue

                if page_has_news_links(href):
                    found.append(href)

                if len(found) >= 3:
                    break

    except Exception:
        pass

    # Əgər heç nə tapılmadısa, ən azı root-u qaytarmırıq.
    # Çünki root çox vaxt menu/logo verir və monitorinqi zəiflədir.
    return found


def main():
    existing = load_existing()

    known_urls = {
        site.get("url", "").strip().rstrip("/").lower()
        for site in existing
        if site.get("url")
    }

    new_sites = []

    for query in SEARCH_QUERIES:
        print("Axtarılır:", query)

        feed = feedparser.parse(google_news_rss(query))
        print("Nəticə sayı:", len(feed.entries))

        for entry in feed.entries[:50]:
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

            sections = find_news_sections(source_url)

            if not sections:
                print("Xəbər bölməsi tapılmadı:", source_name or domain, source_url)
                continue

            for section_url in sections:
                normalized = section_url.rstrip("/").lower()

                if normalized in known_urls:
                    continue

                site = {
                    "name": source_name or domain,
                    "url": section_url,
                    "enabled": True,
                    "xpaths": [],
                    "selector": None,
                    "keywords": DEFAULT_KEYWORDS,
                    "limit": 1,
                    "source_type": "discovered_news_section"
                }

                new_sites.append(site)
                known_urls.add(normalized)

                print("Yeni xəbər bölməsi tapıldı:", source_name or domain, section_url)

    all_sites = existing + new_sites
    save_sites(all_sites)

    print("Yeni bölmə sayı:", len(new_sites))
    print("Ümumi mənbə sayı:", len(all_sites))


if __name__ == "__main__":
    main()
