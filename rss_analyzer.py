import json
import time
import requests
import feedparser
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse

CONFIG_FILES = [
    "courier_config_clean.json",
    "discovered_sites.json"
]

RSS_PATHS = [
    "/rss",
    "/rss.xml",
    "/feed",
    "/feed.xml",
    "/az/rss",
    "/az/rss.xml",
    "/az/feed",
    "/az/feed.xml",
    "/category/education/feed",
    "/category/tehsil/feed",
]

TIMEOUT = 10

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; TehsilBotRSSFinder/1.0)",
    "Accept-Language": "az-AZ,az;q=0.9,en-US;q=0.8",
}

LOCAL_ONLY_DOMAINS = {"localhost", "127.0.0.1", "0.0.0.0", "::1"}


def clean_text(text):
    return " ".join(str(text or "").split()).strip()


def read_json(filename, default):
    try:
        with open(filename, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def write_json(filename, data):
    with open(filename, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def root_url(url):
    parsed = urlparse(url)
    if not parsed.scheme or not parsed.netloc:
        return ""
    return f"{parsed.scheme}://{parsed.netloc}"


def get_domain(url):
    d = urlparse(url).netloc.lower().strip()
    if d.startswith("www."):
        d = d[4:]
    return d


def is_local_only_url(url):
    return get_domain(url).split(":")[0] in LOCAL_ONLY_DOMAINS


def is_valid_feed(rss_url):
    if is_local_only_url(rss_url):
        return False, 0
    try:
        r = requests.get(rss_url, headers=HEADERS, timeout=TIMEOUT, allow_redirects=True)
        if r.status_code != 200:
            return False, 0

        feed = feedparser.parse(r.text)
        count = len(feed.entries or [])

        if count >= 3:
            return True, count

        return False, count

    except Exception:
        return False, 0


def discover_rss_from_html(site_url):
    found = []

    try:
        r = requests.get(site_url, headers=HEADERS, timeout=TIMEOUT, allow_redirects=True)
        if r.status_code != 200:
            return found

        soup = BeautifulSoup(r.text, "html.parser")

        for tag in soup.find_all("link", href=True):
            tag_type = (tag.get("type") or "").lower()
            tag_title = (tag.get("title") or "").lower()
            href = tag.get("href")

            if "rss" in tag_type or "atom" in tag_type or "rss" in tag_title or "feed" in tag_title:
                found.append(urljoin(site_url, href))

    except Exception:
        pass

    return list(dict.fromkeys(found))


def candidate_rss_urls(site_url):
    candidates = []
    candidates.extend(discover_rss_from_html(site_url))

    root = root_url(site_url)
    if root:
        for path in RSS_PATHS:
            candidates.append(urljoin(root, path))

    return [url for url in dict.fromkeys(candidates) if not is_local_only_url(url)]


def find_best_rss(site_url):
    for rss_url in candidate_rss_urls(site_url):
        ok, count = is_valid_feed(rss_url)

        if ok:
            return {
                "rss_url": rss_url,
                "rss_count": count
            }

        time.sleep(0.1)

    return None


def process_file(filename):
    data = read_json(filename, {"sites": []})

    if not isinstance(data, dict) or not isinstance(data.get("sites"), list):
        print(f"❌ Format düzgün deyil: {filename}", flush=True)
        return

    changed = False
    total = len(data["sites"])
    updated = 0
    skipped = 0
    no_rss = 0

    print(f"\n🔎 RSS analizi başladı: {filename} | sayt sayı: {total}", flush=True)

    for index, site in enumerate(data["sites"], start=1):
        if not site.get("enabled", True):
            skipped += 1
            continue

        url = clean_text(site.get("url", ""))

        if not url.startswith("http"):
            skipped += 1
            continue

        if clean_text(site.get("rss_url", "")):
            skipped += 1
            continue

        print(f"[{index}/{total}] RSS axtarılır: {site.get('name') or get_domain(url)} | {url}", flush=True)

        result = find_best_rss(url)

        if result:
            site["rss_url"] = result["rss_url"]
            site["rss_count"] = result["rss_count"]
            site["rss_checked_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
            changed = True
            updated += 1
            print(f"✅ RSS tapıldı: {result['rss_url']} | xəbər sayı: {result['rss_count']}", flush=True)
        else:
            site["rss_checked_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
            no_rss += 1
            print("⏩ RSS tapılmadı", flush=True)

    if changed:
        write_json(filename, data)

    print(f"✅ Fayl tamamlandı: {filename}", flush=True)
    print(f"RSS əlavə edildi: {updated}", flush=True)
    print(f"RSS tapılmadı: {no_rss}", flush=True)
    print(f"Keçildi: {skipped}", flush=True)


def main():
    for filename in CONFIG_FILES:
        process_file(filename)

    print("\n✅ Bütün RSS analizi tamamlandı", flush=True)


if __name__ == "__main__":
    main()
