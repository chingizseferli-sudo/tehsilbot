import json
import requests
from urllib.parse import urlparse

OUTPUT_FILE = "discovered_sites.json"

SEED_DOMAINS = [
    "edu.gov.az",
    "science.gov.az",
    "dim.gov.az",
    "arti.edu.az",
    "tkta.edu.az",
    "vet.edu.gov.az",
    "tif.edu.az",
    "media.gov.az",
    "presscouncil.az",
    "apa.az",
    "report.az",
    "azertag.az",
    "qafqazinfo.az",
    "trend.az",
    "oxu.az"
]

RSS_PATHS = [
    "/rss",
    "/rss.xml",
    "/feed",
    "/feed.xml",
    "/sitemap.xml",
    "/news-sitemap.xml"
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


def works(url):
    try:
        r = requests.get(url, timeout=10, headers={"User-Agent": "Mozilla/5.0"})
        return r.status_code == 200 and len(r.text) > 100
    except:
        return False


def main():
    existing = load_existing()
    known = {site["url"] for site in existing}
    new_sites = []

    for domain in SEED_DOMAINS:
        base = f"https://{domain}"

        for path in RSS_PATHS:
            url = base + path

            if url in known:
                continue

            print("Yoxlanır:", url)

            if works(url):
                item = {
                    "name": domain,
                    "url": base,
                    "enabled": True,
                    "xpaths": [],
                    "selector": None,
                    "keywords": [
                        "təhsil", "məktəb", "şagird", "müəllim",
                        "universitet", "imtahan", "tələbə", "elm", "tədris"
                    ],
                    "limit": 5,
                    "source_type": "discovered_rss_or_sitemap"
                }

                new_sites.append(item)
                known.add(url)

                print("Tapıldı:", url)
                break

    all_sites = existing + new_sites
    save_sites(all_sites)

    print("Yeni mənbə sayı:", len(new_sites))
    print("Ümumi mənbə sayı:", len(all_sites))


if __name__ == "__main__":
    main()
