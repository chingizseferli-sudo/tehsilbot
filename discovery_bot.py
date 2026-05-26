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
    'müəllim Azərbaycan xəbər',
    'site:.az təhsil',
    'site:.az məktəb',
    'site:.az universitet'
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
        "&num=100"
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

def main():
    existing = load_existing()
    known_domains = {get_domain(site["url"]) for site in existing}

    new_sites = []

    for query in SEARCH_QUERIES:
        print(f"Axtarılır: {query}")

        feed = feedparser.parse(google_news_rss(query))

        for entry in feed.entries[:20]:
            link = entry.link
            domain = get_domain(link)

            if not domain:
                continue

            if domain in known_domains:
                continue

            if "google.com" in domain:
                continue

            site = {
                "name": domain,
                "url": f"https://{domain}",
                "enabled": True,
                "xpaths": [],
                "selector": None,
                "keywords": [
                    "təhsil",
                    "məktəb",
                    "şagird",
                    "müəllim",
                    "universitet",
                    "imtahan",
                    "tələbə",
                    "elm",
                    "tədris"
                ],
                "limit": 5,
                "source_type": "discovered_google_news"
            }

            new_sites.append(site)
            known_domains.add(domain)

            print(f"Yeni mənbə tapıldı: {domain}")

    all_sites = existing + new_sites
    save_sites(all_sites)

    print(f"Tamamlandı. Yeni mənbə sayı: {len(new_sites)}")
    print(f"Ümumi mənbə sayı: {len(all_sites)}")

if __name__ == "__main__":
    main()
