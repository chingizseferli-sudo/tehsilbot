import json
import feedparser
from urllib.parse import quote_plus, urlparse

OUTPUT_FILE = "discovered_sites.json"

SEARCH_QUERIES = [
    'təhsil Azərbaycan xəbər',
    'məktəb Azərbaycan xəbər',
    'şagird Azərbaycan xəbər',
    'universitet Azərbaycan xəbər',
    'imtahan Azərbaycan xəbər',
    'elm Azərbaycan xəbər',
    'tələbə Azərbaycan xəbər',
    'müəllim Azərbaycan xəbər'
]

DEFAULT_KEYWORDS = [
    "təhsil", "məktəb", "şagird", "müəllim",
    "universitet", "imtahan", "tələbə", "elm", "tədris"
]


def get_domain(url):
    return urlparse(url).netloc.replace("www.", "")


def google_news_rss(query):
    return (
        "https://news.google.com/rss/search?"
        f"q={quote_plus(query)}"
        "&hl=az"
        "&gl=AZ"
        "&ceid=AZ:az"
    )


def load_existing():
    try:
        with open(OUTPUT_FILE, "r", encoding="utf-8") as f:
            return json.load(f).get("sites", [])
    except:
        return []


def save_sites(sites):
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump({"sites": sites}, f, ensure_ascii=False, indent=2)


def get_real_source(entry):
    source = entry.get("source", {})

    if isinstance(source, dict):
        href = source.get("href")
        title = source.get("title")
    else:
        href = None
        title = None

    if href:
        return href

    return None


def main():
    existing = load_existing()
    known_domains = {get_domain(site["url"]) for site in existing}

    new_sites = []

    for query in SEARCH_QUERIES:
        print(f"Axtarılır: {query}")

        feed = feedparser.parse(google_news_rss(query))

        print(f"Nəticə sayı: {len(feed.entries)}")

        for entry in feed.entries[:50]:
            real_url = get_real_source(entry)

            if not real_url:
                print("Mənbə linki tapılmadı:", entry.get("title", "")[:80])
                continue

            domain = get_domain(real_url)

            print("Tapılan domen:", domain)

            if not domain:
                continue

            if "google.com" in domain:
                continue

            if domain in known_domains:
                continue

            site = {
                "name": domain,
                "url": real_url,
                "enabled": True,
                "xpaths": [],
                "selector": None,
                "keywords": DEFAULT_KEYWORDS,
                "limit": 5,
                "source_type": "discovered_google_news"
            }

            new_sites.append(site)
            known_domains.add(domain)

            print(f"Yeni mənbə əlavə edildi: {domain}")

    all_sites = existing + new_sites
    save_sites(all_sites)

    print(f"Tamamlandı. Yeni mənbə sayı: {len(new_sites)}")
    print(f"Ümumi mənbə sayı: {len(all_sites)}")


if __name__ == "__main__":
    main()
