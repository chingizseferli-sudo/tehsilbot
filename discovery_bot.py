import json
import feedparser
from urllib.parse import quote_plus

OUTPUT_FILE = "discovered_sites.json"

SEARCH_QUERIES = [
    'təhsil Azərbaycan',
    'məktəb Azərbaycan',
    'şagird Azərbaycan',
    'universitet Azərbaycan',
    'imtahan Azərbaycan',
    'elm Azərbaycan',
    'tələbə Azərbaycan',
    'müəllim Azərbaycan',
    'sertifikasiya müəllim',
    'magistratura qəbul',
    'DİM imtahan',
    'Elm və Təhsil Nazirliyi'
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

def main():
    existing = load_existing()
    known_names = {site["name"].lower() for site in existing}
    new_sites = []

    for query in SEARCH_QUERIES:
        print("Axtarılır:", query)

        feed = feedparser.parse(google_news_rss(query))
        print("Nəticə sayı:", len(feed.entries))

        for entry in feed.entries[:50]:
            source = entry.get("source", {})
            source_name = source.get("title") if isinstance(source, dict) else None

            if not source_name:
                continue

            source_name = source_name.strip()

            if source_name.lower() in known_names:
                continue

            site = {
                "name": source_name,
                "url": "",
                "enabled": False,
                "xpaths": [],
                "selector": None,
                "keywords": [
                    "təhsil", "məktəb", "şagird", "müəllim",
                    "universitet", "imtahan", "tələbə", "elm", "tədris"
                ],
                "limit": 5,
                "source_type": "discovered_google_news_source"
            }

            new_sites.append(site)
            known_names.add(source_name.lower())

            print("Yeni mənbə adı tapıldı:", source_name)

    all_sites = existing + new_sites
    save_sites(all_sites)

    print("Yeni mənbə sayı:", len(new_sites))
    print("Ümumi mənbə sayı:", len(all_sites))

if __name__ == "__main__":
    main()
