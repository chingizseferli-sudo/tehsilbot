import json
import feedparser
from urllib.parse import quote_plus, urlparse

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
    "site:.az imtahan"
]

DEFAULT_KEYWORDS = [
    "təhsil", "məktəb", "şagird", "müəllim",
    "universitet", "imtahan", "tələbə", "elm", "tədris"
]


def load_existing():
    try:
        with open(OUTPUT_FILE, "r", encoding="utf-8") as f:
            return json.load(f).get("sites", [])
    except:
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
    except:
        return ""


def main():
    existing = load_existing()

    known_urls = {
        site.get("url", "").strip().lower()
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

            if source_url.lower() in known_urls:
                continue

            site = {
                "name": source_name or domain,
                "url": source_url,
                "enabled": True,
                "xpaths": [],
                "selector": None,
                "keywords": DEFAULT_KEYWORDS,
                "limit": 3,
                "source_type": "discovered_google_news_source"
            }

            new_sites.append(site)
            known_urls.add(source_url.lower())

            print("Yeni sayt tapıldı:", source_name or domain, source_url)

    all_sites = existing + new_sites
    save_sites(all_sites)

    print("Yeni sayt sayı:", len(new_sites))
    print("Ümumi sayt sayı:", len(all_sites))


if __name__ == "__main__":
    main()
