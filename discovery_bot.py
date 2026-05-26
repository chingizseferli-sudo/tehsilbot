import json
import requests
from urllib.parse import urlparse

INPUT_FILES = [
    "courier_config_clean.json",
    "extension_sites.json",
    "bez.json"
]

OUTPUT_FILE = "discovered_sites.json"

PATHS_TO_CHECK = [
    "/rss",
    "/feed",
    "/rss.xml",
    "/feed.xml",
    "/sitemap.xml",
    "/news-sitemap.xml"
]

def get_domain(url):
    return urlparse(url).netloc.replace("www.", "")

def get_base_url(url):
    parsed = urlparse(url)
    return f"{parsed.scheme}://{parsed.netloc}"

def load_sites_from_files():
    sites = []

    for file in INPUT_FILES:
        try:
            with open(file, "r", encoding="utf-8") as f:
                data = json.load(f)

            for site in data.get("sites", []):
                url = site.get("url")
                if url:
                    sites.append(url)

        except Exception as e:
            print(f"{file} oxunmadı: {e}")

    return list(set(sites))

def endpoint_works(url):
    try:
        r = requests.get(
            url,
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=8
        )

        if r.status_code != 200:
            return False

        text = r.text.lower()

        if "<rss" in text or "<urlset" in text or "<feed" in text:
            return True

        return False

    except:
        return False

def main():
    urls = load_sites_from_files()
    print(f"Yoxlanacaq domen sayı: {len(urls)}")

    discovered = []
    seen = set()

    for url in urls:
        base = get_base_url(url)
        domain = get_domain(base)

        if not domain or domain in seen:
            continue

        seen.add(domain)

        print(f"Yoxlanır: {domain}")

        found_endpoint = None

        for path in PATHS_TO_CHECK:
            test_url = base + path

            if endpoint_works(test_url):
                found_endpoint = test_url
                print(f"Tapıldı: {test_url}")
                break

        if found_endpoint:
            discovered.append({
                "name": domain,
                "url": base,
                "rss_or_sitemap": found_endpoint,
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
                "source_type": "discovered_rss_sitemap"
            })

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump({"sites": discovered}, f, ensure_ascii=False, indent=2)

    print(f"Tamamlandı. Tapılan mənbə sayı: {len(discovered)}")

if __name__ == "__main__":
    main()
