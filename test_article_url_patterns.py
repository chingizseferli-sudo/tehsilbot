import importlib.util
import os
import sys
import types


os.environ.setdefault("SUPABASE_URL", "")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "")


if "requests" not in sys.modules:
    requests = types.ModuleType("requests")

    class _RequestException(Exception):
        pass

    class _Timeout(_RequestException):
        pass

    class _SSLError(_RequestException):
        pass

    class _ConnectionError(_RequestException):
        pass

    requests.Timeout = _Timeout
    requests.exceptions = types.SimpleNamespace(
        SSLError=_SSLError,
        ConnectionError=_ConnectionError,
        RequestException=_RequestException,
    )
    requests.get = lambda *_args, **_kwargs: None
    requests.post = lambda *_args, **_kwargs: None
    requests.patch = lambda *_args, **_kwargs: None
    sys.modules["requests"] = requests


if "feedparser" not in sys.modules:
    feedparser = types.ModuleType("feedparser")
    feedparser.parse = lambda *_args, **_kwargs: types.SimpleNamespace(entries=[], bozo=False)
    sys.modules["feedparser"] = feedparser


if "bs4" not in sys.modules:
    bs4 = types.ModuleType("bs4")

    class _BeautifulSoup:
        def __init__(self, *_args, **_kwargs):
            pass

        def find(self, *_args, **_kwargs):
            return None

        def find_all(self, *_args, **_kwargs):
            return []

        def select(self, *_args, **_kwargs):
            return []

    bs4.BeautifulSoup = _BeautifulSoup
    sys.modules["bs4"] = bs4


spec = importlib.util.spec_from_file_location("site_monitor", "site_monitor.py")
site_monitor = importlib.util.module_from_spec(spec)
spec.loader.exec_module(site_monitor)


def assert_accepted(page_url, link):
    results = []
    site_monitor.add_item(
        results,
        page_url,
        "Universitetdə elmi tədbir və yeni proqram təqdim edilib",
        link,
        [],
    )
    assert len(results) == 1, link


def assert_rejected(page_url, link):
    results = []
    site_monitor.add_item(
        results,
        page_url,
        "Universitet xəbərləri və yenilikləri siyahısı",
        link,
        [],
    )
    assert results == [], link


def main():
    accepted = [
        ("https://adalet.az", "https://adalet.az/az/posts/detail/milli-meclisin-novbeti-iclasi-1782384545"),
        ("https://adalet.az", "https://adalet.az/az/writers/detail/kose-yazisi-1782384545"),
        ("https://asoiu.edu.az/allNews", "https://asoiu.edu.az/single_news/3992"),
        ("https://bdu-qazax.edu.az/index.php/az/kheberler", "https://bdu-qazax.edu.az/index.php/az/kheberler/425-maarif26"),
        ("https://www.atu.edu.az/xeberler/1", "https://www.atu.edu.az/xeber/1505"),
    ]
    rejected = [
        ("https://asoiu.edu.az", "https://asoiu.edu.az/allNews"),
        ("https://bdu-qazax.edu.az", "https://bdu-qazax.edu.az/index.php/az/kheberler"),
        ("https://bmtk.edu.az", "https://bmtk.edu.az/kateqoriya/xeberler/"),
        ("https://example.az", "https://example.az/archive/2026"),
        ("https://example.az", "https://example.az/tag/tehsil"),
        ("https://example.az", "https://example.az/search?q=tehsil"),
    ]

    for page_url, link in accepted:
        assert site_monitor.is_article_like_link(link), link
        assert_accepted(page_url, link)

    for page_url, link in rejected:
        assert_rejected(page_url, link)

    print("article url pattern fixtures ok")


if __name__ == "__main__":
    main()
