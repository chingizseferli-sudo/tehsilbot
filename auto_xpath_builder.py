import json
import requests
from bs4 import BeautifulSoup
from collections import Counter
from urllib.parse import urljoin, urlparse

INPUT_FILE = "discovered_sites.json"
OUTPUT_FILE = "patterns.json"

HEADERS = {
    "User-Agent": "Mozilla/5.0"
}

BAD_PATTERNS = [
    "/tag/",
    "/category/",
    "/author/",
    "/page/",
    "/login/",
    "/register/",
    "/search/",
    "/video/",
    "/photo/",
    "/contact/",
    "/about/"
]


def get_domain(url):
    return urlparse(url).netloc.replace("www.", "")


def is_bad_pattern(pattern):
    return any(bad in pattern.lower() for bad in BAD_PATTERNS)


def analyze_site(site):
    url = site.get("url")

    if not url:
        return []

    try:
        print(f"\nYoxlanır: {url}")

        r = requests.get(url, headers=HEADERS, timeout=20)
        print("Status:", r.status_code)

        if r.status_code != 200:
            return []

        soup = BeautifulSoup(r.text, "html.parser")

        links = []

        for a in soup.find_all("a", href=True):
            full = urljoin(url, a["href"])

            if urlparse(full).netloc != urlparse(url).netloc:
                continue

            path = urlparse(full).path
            parts = [p for p in path.split("/") if p]

            if len(parts) >= 2:
                pattern = "/" + parts[0] + "/"

                if not is_bad_pattern(pattern):
                    links.append(pattern)

        counter = Counter(links)

        selected = [
            pattern
            for pattern, count in counter.most_common(10)
            if count >= 2
        ]

        print("Tapılan patternlər:", selected)

        return selected

    except Exception as e:
        print("Xəta:", e)
        return []


def main():
    with open(INPUT_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)

    result = {}

    for site in data.get("sites", []):
        domain = get_domain(site.get("url", ""))

        if not domain:
            continue

        patterns = analyze_site(site)

        if patterns:
            result[domain] = patterns

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    print("\nHazırdır:", OUTPUT_FILE)
    print("Pattern tapılan sayt sayı:", len(result))


if __name__ == "__main__":
    main()
