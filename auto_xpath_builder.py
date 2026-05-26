import requests
from bs4 import BeautifulSoup
from collections import Counter
from urllib.parse import urljoin, urlparse

URL = "https://apa.az"

headers = {
    "User-Agent": "Mozilla/5.0"
}

r = requests.get(URL, headers=headers, timeout=20)
soup = BeautifulSoup(r.text, "html.parser")

links = []

for a in soup.find_all("a", href=True):
    href = a["href"]

    if not href:
        continue

    full = urljoin(URL, href)

    if urlparse(full).netloc != urlparse(URL).netloc:
        continue

    links.append(full)

patterns = []

for link in links:
    path = urlparse(link).path

    parts = [p for p in path.split("/") if p]

    if len(parts) >= 2:
        patterns.append("/" + parts[0] + "/")

counter = Counter(patterns)

print("\nƏn çox təkrarlanan link strukturları:\n")

for pattern, count in counter.most_common(20):
    print(f"{count:4d}  {pattern}")
