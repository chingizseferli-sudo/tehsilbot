
import json
import time
import shutil
import requests
from bs4 import BeautifulSoup
from urllib.parse import urlparse, urljoin

FILES = [
    "courier_config_clean.json",
    "discovered_sites.json"
]

TIMEOUT = 8

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; TehsilBotCleaner/1.0)",
    "Accept-Language": "az-AZ,az;q=0.9,en-US;q=0.8",
}

NEWS_HINTS = [
    "xəbər", "xeber", "news", "media", "son xəbər", "son xeber",
    "məqalə", "meqale", "article", "post", "press", "elan"
]

BAD_DOMAINS = [
    "facebook.com", "instagram.com", "youtube.com", "youtu.be",
    "t.me", "twitter.com", "x.com", "linkedin.com"
]


def read_json(filename):
    try:
        with open(filename, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"sites": []}


def write_json(filename, data):
    with open(filename, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def domain(url):
    try:
        d = urlparse(url).netloc.lower()
        if d.startswith("www."):
            d = d[4:]
        return d
    except Exception:
        return ""


def valid_url(url):
    if not url:
        return False
    if not isinstance(url, str):
        return False
    if not url.startswith("http"):
        return False
    if any(bad in url.lower() for bad in BAD_DOMAINS):
        return False
    return bool(domain(url))


def looks_like_news_site(url):
    try:
        r = requests.get(
            url,
            headers=HEADERS,
            timeout=TIMEOUT,
            allow_redirects=True
        )

        if r.status_code != 200:
            return False, f"status {r.status_code}"

        soup = BeautifulSoup(r.text, "html.parser")

        news_links = 0

        for a in soup.find_all("a", href=True):
            text = a.get_text(" ", strip=True).lower()
            href = urljoin(url, a["href"]).lower()

            if domain(href) != domain(url):
                continue

            combined = text + " " + href

            if any(hint in combined for hint in NEWS_HINTS):
                news_links += 1

            if news_links >= 3:
                return True, "news links found"

        return False, "news link azdır"

    except Exception as e:
        return False, str(e)[:120]


def clean_file(filename):
    data = read_json(filename)

    if "sites" not in data or not isinstance(data["sites"], list):
        print(f"❌ Format düzgün deyil: {filename}")
        return

    backup = filename + ".backup"
    shutil.copyfile(filename, backup)

    cleaned = []
    seen_domains = set()

    removed_no_url = 0
    removed_duplicate = 0
    removed_not_news = 0

    print(f"\n🧹 Təmizlənir: {filename}")
    print(f"Başlanğıc sayt sayı: {len(data['sites'])}")

    for site in data["sites"]:
        url = str(site.get("url", "")).strip()

        if not valid_url(url):
            removed_no_url += 1
            continue

        d = domain(url)

        if d in seen_domains:
            removed_duplicate += 1
            continue

        ok, reason = looks_like_news_site(url)

        if not ok:
            removed_not_news += 1
            print(f"Silindi: {d} | {reason}")
            continue

        seen_domains.add(d)
        cleaned.append(site)

        time.sleep(0.15)

    data["sites"] = cleaned
    write_json(filename, data)

    print(f"✅ Təmizləndi: {filename}")
    print(f"Qalan sayt sayı: {len(cleaned)}")
    print(f"URL-siz / səhv URL silindi: {removed_no_url}")
    print(f"Təkrar domain silindi: {removed_duplicate}")
    print(f"Xəbər saytı olmayan silindi: {removed_not_news}")
    print(f"Backup yaradıldı: {backup}")


def main():
    for filename in FILES:
        clean_file(filename)

    print("\n✅ Bütün təmizləmə tamamlandı")


if __name__ == "__main__":
    main()
